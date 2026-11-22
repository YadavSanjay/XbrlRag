"""Fakes and builders for the fiscal-year-range spec tests (docs/specs/fiscal-year-range.md).

The fakes sit at the I/O boundaries and nowhere else: a provider adapter behind a real
``LLMGateway``, an ``EntityExtractor`` whose connection is fake (entity_master and
fiscal_calendar), and a fake embedder and vector store for the narrative step. The router, the
extractor's logic, ``extract_years_in_range``, the dispatcher, the planner, the narrative step,
``search_scopes`` and ``filing_targets`` are real.

AAA is synthetic. The builders are session-scoped factory fixtures, so hypothesis tests can use
them without tripping the function-scoped-fixture health check.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink
from src.retriever.xbrl.entity_extractor import EntityExtractor
from src.retriever.xbrl.intent_router import IntentRouter
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.xbrl_narrative_retriever import XbrlNarrativeRetriever

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
# A priced model, explicit so the tests do not depend on the active profile.
MODEL = "gpt-5-mini"

AAA_CIK = "0000000001"
ENTITIES: dict[str, dict[str, Any]] = {
    "AAA": {"cik": AAA_CIK, "trading_symbol": "AAA", "company_name": "Alpha Holdings",
            "fye_month": 12, "fye_day": 31, "last_seen_fy": 2025},
}


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


# ----------------------------------------------------------------------------------
# The provider boundary.
# ----------------------------------------------------------------------------------


class FakeAdapter(ProviderAdapter):
    """Records each request and answers every call with *reply*."""

    def __init__(self, reply: str) -> None:
        self.provider = "fake"
        self.reply = reply
        self.params: list[dict] = []
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.params.append(params)
        return self.reply

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=raw, finish_reason="stop", prompt_tokens=100,
                              completion_tokens=50, reasoning_tokens=0, cached_prompt_tokens=0)

    def user_text(self, call: int = 0) -> str:
        """The user message's text parts, joined."""
        parts: list[str] = []
        for message in self.params[call]["messages"]:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            else:
                parts.extend(str(p.get("text", "")) for p in content)
        return "\n".join(parts)


def real_gateway(stage: str, adapter: FakeAdapter) -> LLMGateway:
    return LLMGateway(stage=stage, cost_tracker=CostTracker(), rate_limiter=None,
                      run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                      adapters={p: adapter for p in PROVIDERS})


# ----------------------------------------------------------------------------------
# The database boundary: entity_master and fiscal_calendar behind the extractor.
# ----------------------------------------------------------------------------------


class _EntityCursor:
    """Answers the extractor's SELECTs from ``ENTITIES``; fiscal_calendar has no rows."""

    def __init__(self) -> None:
        self.row: dict[str, Any] | None = None

    def __enter__(self) -> _EntityCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.row = None
        if "fiscal_calendar" in sql:
            return  # no calendar row: the extractor leaves the advisory dates None
        if "similarity(" in sql:
            wanted = str(params[0]).strip().lower()
            for entity in ENTITIES.values():
                if entity["company_name"].lower() == wanted:
                    self.row = {**entity, "sim": 1.0}
                    return
            self.row = {**next(iter(ENTITIES.values())), "sim": 0.0}
        elif "upper(trading_symbol)" in sql:
            self.row = ENTITIES.get(str(params[0]).strip().upper())
        elif "WHERE cik = %s" in sql:
            self.row = next((e for e in ENTITIES.values() if e["cik"] == params[0]), None)

    def fetchone(self) -> dict[str, Any] | None:
        return self.row

    def fetchall(self) -> list[dict[str, Any]]:
        return [] if self.row is None else [self.row]


class _EntityConn:
    def __enter__(self) -> _EntityConn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def cursor(self, cursor_factory: Any = None) -> _EntityCursor:
        return _EntityCursor()

    def close(self) -> None:
        return None


class FakeDbExtractor(EntityExtractor):
    """The real extractor, on a fake connection (the DB boundary)."""

    def __init__(self) -> None:
        super().__init__(db_conn_params={}, company_aliases={})

    def _connect(self) -> _EntityConn:
        return _EntityConn()


def router_json(periods: list[str], *, intent: str = "NUMERIC_LOOKUP",
                concepts: list[str] | None = None) -> str:
    """The router LLM's reply: company AAA, the given period mentions."""
    return json.dumps({
        "intent": intent,
        "company_mentions": ["AAA"],
        "period_mentions": list(periods),
        "concept_phrases": list(concepts if concepts is not None else ["revenue"]),
        "dimension_hint_present": False,
    })


def route(periods: list[str], *, intent: str = "NUMERIC_LOOKUP", query: str = "AAA revenue"):
    """A real ``IntentRouter`` on the real extractor; the LLM and DB are fake."""
    router = IntentRouter(api_key="sk-spec-offline", model=MODEL,
                          entity_extractor=FakeDbExtractor())
    adapter = FakeAdapter(router_json(periods, intent=intent))
    router._llm_client = real_gateway("intent_router", adapter)
    return router, router.route(query)


def years_in_scope(result, query: str = "AAA revenue") -> list[int]:
    """The ``FISCAL_YEARS_IN_SCOPE`` list the real planner puts in its user message."""
    adapter = FakeAdapter(json.dumps({"error": "spec_capture", "reason": "prompt captured"}))
    planner = PlanGenerator(api_key="sk-spec-offline", model=MODEL, max_retries=0)
    planner._llm_client = real_gateway("plan_generator", adapter)
    planner.generate_plan(query, result)
    text = adapter.user_text(0)
    head = "FISCAL_YEARS_IN_SCOPE"
    assert head in text, "the planner's user message has no FISCAL_YEARS_IN_SCOPE block"
    after = text.split(head, 1)[1]
    start = after.index("[")
    end = after.index("]", start)
    return json.loads(after[start:end + 1])


# ----------------------------------------------------------------------------------
# The vector-store and embedder boundaries, for the XBRL narrative step.
# ----------------------------------------------------------------------------------


class _NoPoints:
    points: list = []


class FakeVectorStore:
    """Records every hybrid query's arguments and returns no points."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def hybrid_query_text(self, **kwargs: Any) -> _NoPoints:
        self.calls.append(kwargs)
        return _NoPoints()


class FakeEmbedder:
    def embed_texts(self, items: list[tuple[str, str]]) -> dict:
        return {key: ([0.0, 0.0], {"indices": [], "values": []}) for key, _ in items}


class FakeReranker:
    def rerank(self, query: str, texts: list[str]) -> list[float]:
        return [0.0 for _ in texts]


def narrative_years(result, query: str = "AAA risk factors") -> list[int] | None:
    """The ``fiscal_years`` filter the real narrative step searches with."""
    store = FakeVectorStore()
    retriever = XbrlNarrativeRetriever(embedding_generator=FakeEmbedder(), vector_db_client=store,
                                       reranker=FakeReranker(), expand_neighbors=False)
    retriever.execute_step({"id": "n1", "op": "narrative", "section_hint": None}, query,
                           intent_result=result)
    assert len(store.calls) == 1
    return store.calls[0]["fiscal_years_in_query"]


# ----------------------------------------------------------------------------------
# Factory fixtures.
# ----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def make_route():
    """Factory: ``(router, result)`` for period mentions, from the real router."""
    return route


@pytest.fixture(scope="session")
def planner_years():
    """Factory: the planner's ``FISCAL_YEARS_IN_SCOPE`` for a router result."""
    return years_in_scope


@pytest.fixture(scope="session")
def narrative_filter():
    """Factory: the narrative step's ``fiscal_years`` filter for a router result."""
    return narrative_years


@pytest.fixture(scope="session")
def aaa_cik() -> str:
    """AAA's synthetic cik."""
    return AAA_CIK
