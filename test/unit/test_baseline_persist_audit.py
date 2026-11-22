"""``run_suite.py --persist-audit`` for the baseline arms.

Every baseline arm answers inside ``answer_in_trace``; with ``--persist-audit`` the
case's trace (its llm_call events, cost and final answer) must land in ``audit.*`` as
XbrlRag's own does, so the eval UI's ``/tech/traces/<query_id>`` link resolves. Off by
default stays off. Fakes sit only at the provider adapter and the Postgres connection;
the arm, the gateway, the trace and the audit store's row building are real.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.systems import build_system
from src.eval.systems._stage_llm import answer_in_trace
from src.eval.systems.closed_book import ClosedBookSystem
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import CircuitBreakerOpen, RunBudget, RunBudgetExceeded
from src.observability import audit_store
from src.observability.audit_store import PostgresAuditStore, enable_audit_persistence
from src.observability.trace_context import (
    AuditSink,
    TraceContext,
    current_trace,
    get_audit_sink,
    set_audit_sink,
)

_PROVIDERS = ("openai", "openai_compat", "anthropic", "huggingface")
_CASE = EvalCase(id="c1", question="What was AAA revenue in 2024?", kind="lookup",
                 expected_value=1.0, ticker="AAA", fy=2024)


@pytest.fixture(autouse=True)
def _isolated_sink():
    """Each test starts with no process-wide sink and leaves none behind."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


class _RecordingSink(AuditSink):
    def __init__(self) -> None:
        self.traces: list[TraceContext] = []

    def persist_query(self, trace: TraceContext) -> None:
        self.traces.append(trace)


class _FakeAdapter(ProviderAdapter):
    """The LLM provider boundary: a fixed reply and token counts."""

    def __init__(self, reply: str) -> None:
        self.provider = "fake"
        self.reply = reply

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=self.reply, finish_reason="stop", prompt_tokens=1000,
                              completion_tokens=500, reasoning_tokens=0,
                              cached_prompt_tokens=0)


def _gateway(reply: str) -> LLMGateway:
    adapter = _FakeAdapter(reply)
    return LLMGateway(stage="closed_book", cost_tracker=CostTracker(), rate_limiter=None,
                      run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                      adapters={p: adapter for p in _PROVIDERS})


class _FakeCursor:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any) -> None:
        self._calls.append(("execute", sql, params))

    def executemany(self, sql: str, params: Any) -> None:
        self._calls.append(("executemany", sql, list(params)))


class _FakeConn:
    """The Postgres boundary: records the statements a persist would run."""

    def __init__(self) -> None:
        self.calls: list = []

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.calls)

    def close(self) -> None:
        return None


# ----------------------------------------------------------------------------------
# answer_in_trace records the final answer on the trace before it closes
# ----------------------------------------------------------------------------------


def test_answer_is_on_the_persisted_trace():
    sink = _RecordingSink()
    set_audit_sink(sink)

    def produce() -> SystemAnswer:
        trace = current_trace()
        assert trace is not None
        trace.record_llm_call(prompt_tokens=10, completion_tokens=5, cost_usd=0.25)
        return SystemAnswer(text="Revenue was $1.", numeric_values=[1.0])

    answer = answer_in_trace("q?", produce)

    [trace] = sink.traces
    assert trace.query_id == answer.query_id
    assert trace.total_cost_usd == pytest.approx(0.25)
    assert trace.answer is not None
    assert trace.answer["answer_summary"] == "Revenue was $1."
    assert trace.answer["metadata"]["fully_answered"] is True
    assert trace.answer["baseline"]["numeric_values"] == [1.0]


@pytest.mark.parametrize(
    "produced, refused",
    [
        (SystemAnswer(text="cannot say", refused=True), True),
        (SystemAnswer(text="maybe 1", hedged=True), False),
    ],
)
def test_a_refusal_or_hedge_is_not_fully_answered(produced, refused):
    sink = _RecordingSink()
    set_audit_sink(sink)

    answer_in_trace("q?", lambda: produced)

    meta = sink.traces[0].answer["metadata"]
    assert meta["fully_answered"] is False
    assert meta["refused"] is refused


def test_a_failed_case_persists_its_error():
    sink = _RecordingSink()
    set_audit_sink(sink)

    def produce() -> SystemAnswer:
        raise ValueError("bad reply")

    answer = answer_in_trace("q?", produce)

    [trace] = sink.traces
    assert answer.error == repr(ValueError("bad reply"))
    assert trace.error == answer.error
    assert trace.answer["metadata"]["fully_answered"] is False
    assert trace.answer["baseline"]["error"] == answer.error


def test_trace_answer_is_json_safe():
    sink = _RecordingSink()
    set_audit_sink(sink)
    odd = SystemAnswer(text="x", agent={"when": object()})

    answer_in_trace("q?", lambda: odd)

    json.dumps(sink.traces[0].answer)  # what psycopg2's Json does on persist


@pytest.mark.parametrize("halt", [RunBudgetExceeded("cap"), CircuitBreakerOpen("open")])
def test_run_halt_still_propagates(halt):
    set_audit_sink(_RecordingSink())

    def produce() -> SystemAnswer:
        raise halt

    with pytest.raises(type(halt)):
        answer_in_trace("q?", produce)


# ----------------------------------------------------------------------------------
# build_system(persist_audit=...) registers the sink for every arm
# ----------------------------------------------------------------------------------


@pytest.fixture
def fake_store(monkeypatch):
    """PostgresAuditStore.from_env returns a store whose connection is a fake."""
    conn = _FakeConn()
    built: list[PostgresAuditStore] = []

    def from_env(cls, dotenv_path: str | None = None) -> PostgresAuditStore:
        store = cls(db_conn_params={})
        monkeypatch.setattr(store, "_connect", lambda: conn)
        built.append(store)
        return store

    monkeypatch.setattr(PostgresAuditStore, "from_env", classmethod(from_env))
    return conn, built


@pytest.mark.parametrize("name", ["closed_book", "naive_rag_plain", "html_rag"])
def test_persist_audit_registers_the_sink_for_a_baseline(fake_store, name):
    _, built = fake_store
    build_system(name, persist_audit=True)
    assert get_audit_sink() is built[0]


def test_off_by_default(fake_store):
    _, built = fake_store
    build_system("closed_book")
    build_system("closed_book", persist_audit=False)
    assert get_audit_sink() is None
    assert built == []


def test_closed_book_case_lands_in_audit(fake_store):
    conn, _ = fake_store
    build_system("closed_book", persist_audit=True)
    reply = json.dumps({"value": 391035000000, "unit": "USD", "rows": [],
                        "refused": False, "hedged": False, "cited_chunks": []})

    answer = ClosedBookSystem(gateway=_gateway(reply)).answer(_CASE)

    assert answer.error is None
    audit = next(c for c in conn.calls if c[0] == "execute")
    assert "audit.query_audit" in audit[1]
    row = audit[2]
    assert row["query_id"] == answer.query_id
    assert row["query"] == _CASE.question
    assert row["llm_calls"] == 1
    assert row["answer"].adapted["answer_summary"] == answer.text
    assert row["answer"].adapted["baseline"]["numeric_values"] == [391035000000.0]
    events = next(c for c in conn.calls if c[0] == "executemany")
    kinds = [kind for (_qid, _seq, kind, _stage, _payload) in events[2]]
    assert "llm_call" in kinds


# ----------------------------------------------------------------------------------
# enable_audit_persistence
# ----------------------------------------------------------------------------------


def test_enable_keeps_an_existing_sink(fake_store):
    _, built = fake_store
    existing = _RecordingSink()
    set_audit_sink(existing)
    enable_audit_persistence()
    assert get_audit_sink() is existing
    assert built == []


def test_enable_fails_soft(monkeypatch):
    def boom(cls, dotenv_path: str | None = None) -> PostgresAuditStore:
        raise RuntimeError("no db config")

    monkeypatch.setattr(audit_store.PostgresAuditStore, "from_env", classmethod(boom))
    enable_audit_persistence()
    assert get_audit_sink() is None
