"""Fakes and shared text for the dependent-subquery-period spec tests
(docs/specs/dependent-subquery-period.md).

The decomposer is the real ``QueryDecomposer`` with its real ``LLMGateway`` (real
``CostTracker``, real ``RunBudget``, real trace); the only fake is ``ReplyAdapter``, the
provider adapter behind that gateway, which answers every call with one hand-built JSON reply.
The audit sink is off for the session (the database boundary). Everything is synthetic except
the 094b question and its decomposition, copied from the stored trace (query
``531dbaf7c8e04b5b``) as text.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink, trace_query
from src.retriever.xbrl.query_decomposer import QueryDecomposer, SubQuery

API_KEY = "sk-spec-offline"
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
_ENV_VARS = ("XBRLRAG_LLM_PROFILE", "XBRLRAG_OPENAI_SERVICE_TIER", "XBRLRAG_RUN_MAX_COST_USD",
             "XBRLRAG_RUN_MAX_LLM_CALLS", "XBRLRAG_RUN_MAX_TOKENS",
             "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES")

# Case int-21e0eddbbb5b-094b3f3b (template lg-formal-2), as stored.
Q094B = ("Identify the 3 members of the covered companies with the largest Total Debt for FY24, "
         "and give the percentage growth in Payments to Acquire Property, Plant, and Equipment "
         "of the first-ranked one.")
SQ1_094B = ("Among the covered companies, identify the 3 companies with the largest Total Debt "
            "for FY24.")
SQ2_094B = ("For <<TOP:sq1:1>>, what is the percentage growth in Payments to Acquire Property, "
            "Plant, and Equipment?")
SQ2_094B_INHERITED = ("For <<TOP:sq1:1>>, what is the percentage growth in Payments to Acquire "
                      "Property, Plant, and Equipment for fiscal year 2024?")

# A two-year leader-growth question (template lg-conv-2), as stored for case 0f25.
Q_TWO_YEARS = ("Looking at the top 3 of the covered companies by total debt in FY24, by how much "
               "did the leader's capital expenditures change from FY23?")


def sq(sid: str, query: str, depends_on: list[str] | None = None) -> SubQuery:
    """One sub-query."""
    return SubQuery(id=sid, query=query, depends_on=list(depends_on or []))


def dicts(subqueries: list[SubQuery]) -> list[dict]:
    """The sub-queries as plain dicts, for exact comparison."""
    return [s.to_dict() for s in subqueries]


def reply(subqueries: list[SubQuery]) -> str:
    """The decomposer's JSON reply naming *subqueries*."""
    return json.dumps({"subqueries": dicts(subqueries)})


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
    """Clears the profile, service-tier and run-budget variables for the session."""
    saved = {k: os.environ.pop(k) for k in _ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        for k in _ENV_VARS:
            os.environ.pop(k, None)
        os.environ.update(saved)


class ReplyAdapter(ProviderAdapter):
    """A provider adapter that answers every attempt with one fixed reply."""

    def __init__(self, content: str) -> None:
        self.provider = "fake"
        self.content = content
        self.invoked = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {k: v for k, v in kwargs.items() if k != "extra"}

    def invoke(self, client: Any, params: dict) -> Any:
        self.invoked += 1
        return self.content

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)


def decompose(question: str, reply_json: str) -> tuple[list[SubQuery], list[dict]]:
    """Runs the real decomposer on *question* with the LLM answering *reply_json*.

    Returns:
        The sub-queries and the trace's events.
    """
    decomposer = QueryDecomposer(api_key=API_KEY)
    adapter = ReplyAdapter(reply_json)
    decomposer._llm_client = LLMGateway(
        stage="decomposer", cost_tracker=CostTracker(), rate_limiter=None,
        run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
        adapters={p: adapter for p in PROVIDERS})
    with trace_query(question) as trace:
        out = decomposer.decompose(question)
    assert adapter.invoked == 1
    return out, list(trace.events)


def inherit_events(events: list[dict]) -> list[dict]:
    """The ``inherit_period`` decision events of a trace."""
    return [e for e in events if e.get("kind") == "decision"
            and e.get("point") == "query_decomposer" and e.get("decision") == "inherit_period"]


class Kit:
    """The helpers and stored texts, handed to the tests as one fixture."""

    Q094B = Q094B
    SQ1_094B = SQ1_094B
    SQ2_094B = SQ2_094B
    SQ2_094B_INHERITED = SQ2_094B_INHERITED
    Q_TWO_YEARS = Q_TWO_YEARS
    sq = staticmethod(sq)
    dicts = staticmethod(dicts)
    reply = staticmethod(reply)
    decompose = staticmethod(decompose)
    inherit_events = staticmethod(inherit_events)


@pytest.fixture(scope="session")
def kit() -> Kit:
    """The shared helpers (session-scoped, so hypothesis tests may use it)."""
    return Kit()
