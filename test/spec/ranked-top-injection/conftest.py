"""Fakes and builders for the ranked-top-injection spec tests (docs/specs/ranked-top-injection.md).

The pipeline is the real ``XbrlRetrievalPipeline`` with its real LLM stages (decomposer,
intent router, plan generator, synthesizer, faithfulness verifier), dispatcher and plan
executor. The fakes sit only at I/O boundaries:

- ``ReplyAdapter``: a fake provider adapter behind a real ``LLMGateway`` (real
  ``CostTracker``, real trace, real ``RunBudget``), one per stage, installed as the stage's
  ``_llm_client``. It answers every call with one hand-built JSON reply and records each
  call's params, so a test reads the text a stage was sent;
- ``FakeResolver``: the SQL boundary of ``EntityExtractor`` (``resolve_companies`` /
  ``resolve_periods``), returning the real dataclasses and recording every mention it is
  given;
- ``FakeScreen``: the fact store's cross-filer screen, returning a real ``ScreenResult``
  whose rows are given by the test, in the order given; ``Untouchable`` for the lookup,
  breakdown and narrative backends; ``NoCache``: a disabled query cache.

Everything is synthetic (tickers, values, fact ids, questions).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink
from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import IntentRouter
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.query_decomposer import QueryDecomposer
from src.retriever.xbrl.xbrl_factstore_screen import ScreenResult, ScreenStatus

API_KEY = "sk-spec-offline"
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
_ENV_VARS = ("XBRLRAG_LLM_PROFILE", "XBRLRAG_OPENAI_SERVICE_TIER", "XBRLRAG_QUERY_CACHE",
             "XBRLRAG_RUN_MAX_COST_USD", "XBRLRAG_RUN_MAX_LLM_CALLS", "XBRLRAG_RUN_MAX_TOKENS",
             "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES")

# Synthetic filers: ticker -> (cik, name).
FILERS = {
    "AAA": ("0000000001", "Alpha Holdings Inc."),
    "BBB": ("0000000002", "Bravo Systems Corp."),
    "CCC": ("0000000003", "Charlie Logistics Inc."),
    "DDD": ("0000000004", "Delta Retail Co."),
    "EEE": ("0000000005", "Echo Networks Inc."),
}

COMPOUND = ("Identify the 3 covered companies with the largest revenue for fiscal year 2024, "
            "and state the growth in accounts receivable of the first-ranked one.")
SQ1 = ("Among the covered companies, identify the 3 companies with the largest revenue for "
       "fiscal year 2024.")


def screen_rows(tickers: list[str]) -> list[dict]:
    """Ranked screen rows for *tickers*, in the given order: rank 1 first, values falling."""
    return [{"rank": i, "ticker": t, "cik": FILERS[t][0], "company_name": FILERS[t][1],
             "value": float(1_000_000 * (len(tickers) - i + 1)), "unit": "USD",
             "fact_id": 900 + i, "fiscal_year": 2024}
            for i, t in enumerate(tickers, start=1)]


def decomposer_reply(sq2: str) -> str:
    """The decomposer's JSON: the screen ``sq1`` and the dependent ``sq2``."""
    return json.dumps({"subqueries": [
        {"id": "sq1", "query": SQ1, "depends_on": []},
        {"id": "sq2", "query": sq2, "depends_on": ["sq1"]},
    ]})


ROUTER_REPLY = json.dumps({
    "intent": "NUMERIC_SCREEN", "company_mentions": [],
    "period_mentions": ["fiscal year 2024"], "concept_phrases": ["revenue"],
    "dimension_hint_present": False})
PLAN_REPLY = json.dumps({"steps": [{
    "id": "s1", "op": "screen", "concept_family": "Revenue", "fy": 2024,
    "dimension": "NONE", "metric_kind": "value", "order": "desc", "top_n": 3}]})
SYNTHESIZER_REPLY = json.dumps({
    "answer_summary": "The screen ranked the covered companies by revenue.",
    "answer_details": [{"statement": "The screen ranked the covered companies by revenue.",
                        "fact_ids": ["901"], "chunk_ids": []}],
    "unanswered_parts": [], "metadata": {"fully_answered": True}})
VERIFIER_REPLY = json.dumps({"verdicts": [{"index": 0, "verdict": "SUPPORTED",
                                           "reason": "matches the cited fact"}]})


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the database boundary)."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture(scope="session", autouse=True)
def _clean_env():
    """Clears the profile, service-tier, cache and run-budget variables for the session."""
    saved = {k: os.environ.pop(k) for k in _ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        for k in _ENV_VARS:
            os.environ.pop(k, None)
        os.environ.update(saved)


class ReplyAdapter(ProviderAdapter):
    """Answers every call with *reply*; records each call's params."""

    def __init__(self, reply: str) -> None:
        self.provider = "fake"
        self.reply = reply
        self.calls: list[dict] = []

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {k: v for k, v in kwargs.items() if k != "extra"}

    def invoke(self, client: Any, params: dict) -> Any:
        self.calls.append(params)
        return self.reply

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)


_USER_QUERY = re.compile(r"USER_QUERY:\s*\n(?P<q>.*?)\s*$", re.DOTALL)


def user_query_of(params: dict) -> str:
    """The ``USER_QUERY`` a router call was sent (the router's user template ends with it)."""
    for message in params.get("messages", []):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, list):  # content parts: [{"type": "text", "text": ...}]
                content = "".join(str(p.get("text", "")) for p in content)
            m = _USER_QUERY.search(str(content))
            if m:
                return m.group("q")
    raise AssertionError("no USER_QUERY in the router call")


class FakeResolver:
    """The SQL boundary of ``EntityExtractor``: a mention equal to a ``FILERS`` ticker
    (case-insensitive) resolves; anything else is unresolved. Every period is FY2024."""

    def __init__(self) -> None:
        self.mentions: list[list[str]] = []

    def resolve_companies(self, mentions: list[str]) -> list[ResolvedCompany]:
        self.mentions.append(list(mentions))
        out = []
        for m in mentions:
            t = m.strip().upper()
            if t in FILERS:
                cik, name = FILERS[t]
                out.append(ResolvedCompany(mention=m, cik=cik, ticker=t, company_name=name,
                                           resolution="exact_ticker", similarity=1.0))
            else:
                out.append(ResolvedCompany(mention=m, resolution="unresolved"))
        return out

    def resolve_periods(self, mentions: list[str],
                        companies: list[ResolvedCompany] | None = None) -> list[ResolvedPeriod]:
        return [ResolvedPeriod(mention=m, fiscal_year=2024) for m in mentions]


class FakeScreen:
    """The cross-filer screen: every step returns *rows*, in the order given."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def screen(self, step: dict) -> ScreenResult:
        return ScreenResult(step_id=step.get("id"), status=ScreenStatus.OK,
                            rows=[dict(r) for r in self.rows])


class Untouchable:
    """A backend these questions must never reach."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"the {self._name} backend was touched ({attr})")


class NoCache:
    """The query cache, disabled."""

    enabled = False


def gateway(name: str, adapter: ProviderAdapter) -> LLMGateway:
    """A real gateway for stage *name* whose every provider is *adapter*."""
    return LLMGateway(stage=name, cost_tracker=CostTracker(), rate_limiter=None,
                      run_budget=RunBudget(), max_attempts=1,
                      adapters={p: adapter for p in PROVIDERS})


def router_with(reply: str) -> tuple[IntentRouter, ReplyAdapter, FakeResolver]:
    """A real ``IntentRouter`` answering *reply*, over a recording ``FakeResolver``."""
    resolver = FakeResolver()
    adapter = ReplyAdapter(reply)
    router = IntentRouter(api_key=API_KEY, entity_extractor=resolver)
    router._llm_client = gateway("router", adapter)
    return router, adapter, resolver


class Kit:
    """One real pipeline: a compound question whose ``sq1`` screens *tickers* (in that rank
    order) and whose ``sq2`` is *sq2*."""

    def __init__(self, sq2: str, tickers: list[str]) -> None:
        replies = {"decomposer": decomposer_reply(sq2), "router": ROUTER_REPLY,
                   "plan_generator": PLAN_REPLY, "synthesizer": SYNTHESIZER_REPLY,
                   "verifier": VERIFIER_REPLY}
        self.adapters = {s: ReplyAdapter(r) for s, r in replies.items()}
        resolver = FakeResolver()
        stages = {
            "decomposer": QueryDecomposer(api_key=API_KEY),
            "router": IntentRouter(api_key=API_KEY, entity_extractor=resolver),
            "plan_generator": PlanGenerator(api_key=API_KEY),
            "synthesizer": XbrlRagSynthesizer(api_key=API_KEY),
            "verifier": XbrlFaithfulnessVerifier(api_key=API_KEY),
        }
        for name, stage in stages.items():
            stage._llm_client = gateway(name, self.adapters[name])
        self.pipeline = XbrlRetrievalPipeline(
            entity_extractor=resolver,
            intent_router=stages["router"],
            query_decomposer=stages["decomposer"],
            plan_generator=stages["plan_generator"],
            factstore_lookup=Untouchable("lookup"),
            factstore_screen=FakeScreen(screen_rows(tickers)),
            factstore_breakdown=Untouchable("breakdown"),
            narrative_retriever=Untouchable("narrative"),
            synthesizer=stages["synthesizer"],
            faithfulness_verifier=stages["verifier"],
            persist_audit=False,
            query_cache=NoCache(),
        )

    def routed_queries(self) -> list[str]:
        """The query text of each router call, in order (sq1's, then sq2's)."""
        self.pipeline.generate(COMPOUND)
        return [user_query_of(p) for p in self.adapters["router"].calls]


@pytest.fixture(scope="session")
def h():
    """The builders and constants above, as attributes."""
    class _H:
        pass
    for name, value in {
        "Kit": Kit, "router_with": router_with, "screen_rows": screen_rows,
        "FILERS": FILERS, "SQ1": SQ1, "COMPOUND": COMPOUND,
    }.items():
        setattr(_H, name, staticmethod(value) if callable(value) and not isinstance(value, type)
                else value)
    return _H
