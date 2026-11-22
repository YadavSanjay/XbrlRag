"""One re-decomposition of a question whose single plan was refused as a nested ranking.

A question the decomposer left whole ("Among the five companies with the highest revenue,
which has the lowest debt to equity?") reaches the planner as one need; the planner cannot
screen over another screen's members and refuses with ``error_code`` "nested_ranking"
(prompt v10). The pipeline then asks the decomposer once more, with that code as
PLANNER_FEEDBACK (prompt v4), and runs a split result as a compound query.

The fakes sit at the I/O boundaries only: a provider adapter behind a real ``LLMGateway`` for
each LLM stage, a duck-typed entity resolver (the SQL boundary), backends that must never be
reached, and a recording audit sink (the DB boundary). The decomposer, router, planner and
pipeline run for real. All companies and questions are synthetic.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget, RunBudgetExceeded
from src.observability.trace_context import AuditSink, get_audit_sink, set_audit_sink
from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import IntentRouter
from src.retriever.xbrl.plan_generator import NESTED_RANKING_ERROR, PlanGenerator
from src.retriever.xbrl.query_decomposer import QueryDecomposer

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
API_KEY = "sk-test"

QUESTION = ("Among the five companies with the highest revenue in FY2024, which has the "
            "lowest debt to equity?")
SQ1 = "Which five companies had the highest revenue in FY2024?"
SQ2 = "Of <<TOP:sq1:5>>, which had the lowest debt to equity in FY2024?"

PASSTHROUGH = json.dumps({"subqueries": [{"id": "sq1", "query": QUESTION, "depends_on": []}]})
SPLIT = json.dumps({"subqueries": [
    {"id": "sq1", "query": SQ1, "depends_on": []},
    {"id": "sq2", "query": SQ2, "depends_on": ["sq1"]},
]})
ROUTER = json.dumps({"intent": "NUMERIC_SCREEN", "company_mentions": [],
                     "period_mentions": ["FY2024"],
                     "concept_phrases": ["revenue", "debt to equity"],
                     "dimension_hint_present": False})


def _plan_error(code: str | None) -> str:
    body: dict[str, Any] = {
        "error": "cannot rank within another ranking's result",
        "user_query": QUESTION,
        "reason": "a screen cannot be filtered by another screen's members",
        "suggestion": "ask the two rankings separately",
    }
    if code is not None:
        body["error_code"] = code
    return json.dumps(body)


NESTED = _plan_error(NESTED_RANKING_ERROR)
OTHER = _plan_error("other")
UNCODED = _plan_error(None)


class _ScriptAdapter(ProviderAdapter):
    """Plays one scripted reply per call (an Exception entry is raised); then *final*."""

    def __init__(self, final: str, script: list[Any] | None = None) -> None:
        self.provider = "fake"
        self.final = final
        self.script = list(script or [])
        self.params: list[dict] = []
        self._lock = threading.Lock()

    @property
    def invoked(self) -> int:
        return len(self.params)

    def user_text(self, call: int) -> str:
        return str(self.params[call]["messages"][1]["content"][0]["text"])

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {k: v for k, v in kwargs.items() if k != "extra"}

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.params.append(params)
            reply = self.script.pop(0) if self.script else self.final
        if isinstance(reply, Exception):
            raise reply
        return reply

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)


def _gateway(stage: str, adapter: _ScriptAdapter, budget: RunBudget | None = None) -> LLMGateway:
    return LLMGateway(stage=stage, cost_tracker=CostTracker(), rate_limiter=None,
                      run_budget=budget or RunBudget(), max_attempts=1,
                      sleep_fn=lambda _s: None, adapters={p: adapter for p in PROVIDERS})


class _Resolver:
    """The SQL boundary of ``EntityExtractor``: no companies, every period FY2024."""

    def resolve_companies(self, mentions: list[str]) -> list[ResolvedCompany]:
        return []

    def resolve_periods(self, mentions: list[str],
                        companies: list[ResolvedCompany] | None = None) -> list[ResolvedPeriod]:
        return [ResolvedPeriod(mention=m, fiscal_year=2024) for m in mentions]


class _Untouchable:
    """A backend these questions never reach (every plan is refused)."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"the {self._name} backend was touched ({attr})")


class _NoCache:
    enabled = False


class _Sink(AuditSink):
    """Records each completed trace (the audit DB boundary)."""

    def __init__(self) -> None:
        self.traces: list[Any] = []

    def persist_query(self, trace: Any) -> None:
        self.traces.append(trace)


@pytest.fixture
def sink():
    previous = get_audit_sink()
    recorder = _Sink()
    set_audit_sink(recorder)
    try:
        yield recorder
    finally:
        set_audit_sink(previous)


class _Rig:
    """The pipeline with scripted decomposer and planner replies."""

    def __init__(self, decomposer: _ScriptAdapter, planner: _ScriptAdapter,
                 decomposer_budget: RunBudget | None = None) -> None:
        self.decomposer = decomposer
        self.planner = planner
        self.router = _ScriptAdapter(ROUTER)
        resolver = _Resolver()
        stages: dict[str, Any] = {
            "decomposer": QueryDecomposer(api_key=API_KEY),
            "router": IntentRouter(api_key=API_KEY, entity_extractor=resolver),
            "plan_generator": PlanGenerator(api_key=API_KEY),
        }
        stages["decomposer"]._llm_client = _gateway("decomposer", decomposer, decomposer_budget)
        stages["router"]._llm_client = _gateway("intent_router", self.router)
        stages["plan_generator"]._llm_client = _gateway("plan_generator", planner)
        self.pipeline = XbrlRetrievalPipeline(
            entity_extractor=resolver,  # type: ignore[arg-type]  # duck-typed SQL fake
            intent_router=stages["router"],
            query_decomposer=stages["decomposer"],
            plan_generator=stages["plan_generator"],
            factstore_lookup=_Untouchable("lookup"),  # type: ignore[arg-type]  # never reached
            factstore_screen=_Untouchable("screen"),  # type: ignore[arg-type]  # never reached
            factstore_breakdown=_Untouchable("breakdown"),  # type: ignore[arg-type]
            narrative_retriever=_Untouchable("narrative"),  # type: ignore[arg-type]
            synthesizer=_Untouchable("synthesizer"),  # type: ignore[arg-type]
            faithfulness_verifier=_Untouchable("verifier"),  # type: ignore[arg-type]
            persist_audit=False,
            query_cache=_NoCache(),  # type: ignore[arg-type]  # disabled cache
        )


def _redecompose_events(trace: Any) -> list[dict]:
    return [e for e in trace.events
            if e.get("kind") == "decision" and e.get("point") == "redecompose_on_refusal"]


def test_a_nested_ranking_refusal_is_decomposed_once_and_run_as_compound(sink):
    rig = _Rig(_ScriptAdapter(SPLIT, [PASSTHROUGH]), _ScriptAdapter(OTHER, [NESTED]))
    response = rig.pipeline.generate(QUESTION)

    assert rig.decomposer.invoked == 2
    assert "PLANNER_FEEDBACK: null" in rig.decomposer.user_text(0)
    assert 'PLANNER_FEEDBACK: "nested_ranking"' in rig.decomposer.user_text(1)
    assert QUESTION in rig.decomposer.user_text(1)
    # the refused question, then each of the two sub-queries
    assert rig.planner.invoked == 3
    assert response.metadata["decomposed"] is True
    assert [s["id"] for s in response.metadata["subqueries"]] == ["sq1", "sq2"]

    (trace,) = sink.traces
    events = _redecompose_events(trace)
    assert [e["decision"] for e in events] == ["retry", "split"]
    assert all(e["reason"] == NESTED_RANKING_ERROR for e in events)
    assert events[1]["subqueries"] == [SQ1, SQ2]
    assert trace.answer["metadata"]["decomposed"] is True


@pytest.mark.parametrize("refusal", [OTHER, UNCODED], ids=["other_code", "no_code"])
def test_other_refusals_are_not_retried(sink, refusal):
    rig = _Rig(_ScriptAdapter(SPLIT, [PASSTHROUGH]), _ScriptAdapter(refusal))
    response = rig.pipeline.generate(QUESTION)

    assert rig.decomposer.invoked == 1
    assert rig.planner.invoked == 1
    assert response.metadata["fully_answered"] is False
    assert "decomposed" not in response.metadata
    assert _redecompose_events(sink.traces[0]) == []


def test_the_error_code_reaches_the_refusal_metadata(sink):
    rig = _Rig(_ScriptAdapter(PASSTHROUGH), _ScriptAdapter(OTHER))
    assert rig.pipeline.generate(QUESTION).metadata["plan_error_code"] == "other"
    rig = _Rig(_ScriptAdapter(PASSTHROUGH), _ScriptAdapter(UNCODED))
    assert "plan_error_code" not in rig.pipeline.generate(QUESTION).metadata


def test_a_question_already_split_is_never_redecomposed(sink):
    rig = _Rig(_ScriptAdapter(SPLIT), _ScriptAdapter(NESTED))
    response = rig.pipeline.generate(QUESTION)

    assert rig.decomposer.invoked == 1
    assert rig.planner.invoked == 2  # one per sub-query
    assert response.metadata["decomposed"] is True
    assert _redecompose_events(sink.traces[0]) == []


def test_sub_queries_of_a_retry_are_never_redecomposed(sink):
    # every plan is refused as nested, the sub-queries' plans too: still one retry only
    rig = _Rig(_ScriptAdapter(SPLIT, [PASSTHROUGH]), _ScriptAdapter(NESTED))
    response = rig.pipeline.generate(QUESTION)

    assert rig.decomposer.invoked == 2
    assert rig.planner.invoked == 3
    assert response.metadata["decomposed"] is True
    assert [e["decision"] for e in _redecompose_events(sink.traces[0])] == ["retry", "split"]


def test_a_retry_that_does_not_split_keeps_the_refusal(sink):
    rig = _Rig(_ScriptAdapter(PASSTHROUGH), _ScriptAdapter(NESTED))
    response = rig.pipeline.generate(QUESTION)

    assert rig.decomposer.invoked == 2
    assert rig.planner.invoked == 1
    assert response.metadata["plan_error_code"] == NESTED_RANKING_ERROR
    assert response.metadata["fully_answered"] is False
    events = _redecompose_events(sink.traces[0])
    assert [e["decision"] for e in events] == ["retry", "not_split"]


def test_a_failing_retry_falls_back_to_the_refusal(sink):
    # the second sub-query's planner call fails at the provider (not a run halt)
    planner = _ScriptAdapter(OTHER, [NESTED, OTHER, RuntimeError("provider down")])
    rig = _Rig(_ScriptAdapter(SPLIT, [PASSTHROUGH]), planner)
    response = rig.pipeline.generate(QUESTION)

    assert rig.decomposer.invoked == 2
    assert response.query == QUESTION
    assert response.metadata["plan_error_code"] == NESTED_RANKING_ERROR
    assert "decomposed" not in response.metadata
    (trace,) = sink.traces
    events = _redecompose_events(trace)
    assert [e["decision"] for e in events] == ["retry", "split", "failed"]
    # the trace's answer is the refusal again, not the failed sub-query's
    assert trace.answer["metadata"]["plan_error_code"] == NESTED_RANKING_ERROR


def test_a_failing_retry_call_falls_back_to_the_refusal(sink):
    rig = _Rig(_ScriptAdapter(SPLIT, [PASSTHROUGH, RuntimeError("provider down")]),
               _ScriptAdapter(NESTED))
    response = rig.pipeline.generate(QUESTION)

    assert rig.decomposer.invoked == 2
    assert response.metadata["plan_error_code"] == NESTED_RANKING_ERROR
    events = _redecompose_events(sink.traces[0])
    assert [e["decision"] for e in events] == ["retry", "not_split"]


def test_a_run_halt_in_the_retry_propagates(sink):
    # decomposer, router and planner make three calls; the retry is the fourth
    rig = _Rig(_ScriptAdapter(SPLIT, [PASSTHROUGH]), _ScriptAdapter(NESTED),
               decomposer_budget=RunBudget(max_llm_calls=3))
    with pytest.raises(RunBudgetExceeded):
        rig.pipeline.generate(QUESTION)

    assert rig.decomposer.invoked == 1
    (trace,) = sink.traces
    assert trace.annotations.get("halted") is True
    assert [e["decision"] for e in _redecompose_events(trace)] == ["retry"]
