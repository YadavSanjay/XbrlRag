"""Unit tests for the Postgres audit store (Phase 3 persistence).

`_build_rows` is tested purely (no DB). `persist_query` is tested against a fake
connection that records SQL. A DB round-trip test runs only if audit.query_audit
exists in a reachable xbrl_db.
"""
import os

import pytest

from src.observability.audit_store import PostgresAuditStore
from src.observability.trace_context import (
    log_event,
    set_audit_sink,
    trace_query,
    traced_stage,
)


def _populated_trace():
    """Run a small fake query end-to-end and return its TraceContext."""
    captured = {}
    with trace_query("What was NVDA revenue?", user_id="analyst1", query_id="qid-test-1") as tc:
        with traced_stage("intent_router"):
            log_event("decision", point="branch_dispatch", branch="PLAN_GENERATOR",
                      intent="NUMERIC_COMPUTE", degraded=False, reason="needs a plan")
        tc.annotate(branch="PLAN_GENERATOR", intent="NUMERIC_COMPUTE", degraded=False)
        tc.record_llm_call(prompt_tokens=1000, completion_tokens=200, cost_usd=0.0015, retries=1)
        log_event("numeric_lineage", op="compute", step_id="g", expr="a/b", result=0.5,
                  source_fact_ids={"a": 11, "b": 22})
        tc.annotate(fully_answered=True)
        captured["tc"] = tc
    return captured["tc"]


# ----------------------------------------------------------------------
# _build_rows (pure)
# ----------------------------------------------------------------------

def test_build_rows_audit_row_fields():
    tc = _populated_trace()
    audit_row, _ = PostgresAuditStore._build_rows(tc)

    assert audit_row["query_id"] == "qid-test-1"
    assert audit_row["user_id"] == "analyst1"
    assert audit_row["query"] == "What was NVDA revenue?"
    assert audit_row["llm_calls"] == 1
    assert audit_row["total_prompt_tokens"] == 1000
    assert audit_row["total_completion_tokens"] == 200
    assert audit_row["total_cost_usd"] == 0.0015
    assert audit_row["total_retries"] == 1
    assert audit_row["branch"] == "PLAN_GENERATOR"
    assert audit_row["intent"] == "NUMERIC_COMPUTE"
    assert audit_row["degraded"] is False
    assert audit_row["fully_answered"] is True
    assert audit_row["error"] is None
    assert "intent_router" in audit_row["stage_latency_ms"]
    assert audit_row["total_latency_ms"] is not None
    assert audit_row["started_at"]  # ISO wall-clock timestamp


def test_build_rows_events_exclude_summary_and_are_sequenced():
    tc = _populated_trace()
    _, event_rows = PostgresAuditStore._build_rows(tc)

    kinds = [r[2] for r in event_rows]
    assert "query_summary" not in kinds          # summary is the audit row, not an event
    assert "query_start" in kinds
    assert "decision" in kinds
    assert "numeric_lineage" in kinds
    # seq is dense and ordered
    seqs = [r[1] for r in event_rows]
    assert seqs == list(range(len(event_rows)))
    # payload is the full event dict
    lineage = next(r for r in event_rows if r[2] == "numeric_lineage")
    assert lineage[4]["source_fact_ids"] == {"a": 11, "b": 22}


def test_build_rows_records_error():
    try:
        with trace_query("boom query", query_id="qid-err") as tc:
            raise ValueError("kaboom")
    except ValueError:
        pass
    audit_row, _ = PostgresAuditStore._build_rows(tc)
    assert "kaboom" in audit_row["error"]


# ----------------------------------------------------------------------
# persist_query against a fake connection
# ----------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, log):
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._log.append(("execute", sql, params))

    def executemany(self, sql, seq_of_params):
        self._log.append(("executemany", sql, list(seq_of_params)))


class _FakeConn:
    def __init__(self):
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self.calls)

    def close(self):
        self.closed = True


def test_persist_query_writes_audit_and_events(monkeypatch):
    tc = _populated_trace()
    store = PostgresAuditStore(db_conn_params={})
    fake = _FakeConn()
    monkeypatch.setattr(store, "_connect", lambda: fake)

    store.persist_query(tc)

    assert fake.closed
    kinds = [c[0] for c in fake.calls]
    assert "execute" in kinds       # the query_audit insert
    assert "executemany" in kinds   # the query_event batch insert

    audit_call = next(c for c in fake.calls if c[0] == "execute")
    assert "audit.query_audit" in audit_call[1]
    assert audit_call[2]["query_id"] == "qid-test-1"

    events_call = next(c for c in fake.calls if c[0] == "executemany")
    assert "audit.query_event" in events_call[1]
    assert len(events_call[2]) >= 3  # query_start + decision + numeric_lineage


def test_persist_failure_does_not_break_request_path(monkeypatch):
    """A sink that raises must not surface out of trace_query."""
    class _Boom(PostgresAuditStore):
        def persist_query(self, trace):
            raise RuntimeError("db down")

    set_audit_sink(_Boom(db_conn_params={}))
    try:
        # should complete cleanly despite the sink raising
        with trace_query("q", query_id="qid-safe"):
            pass
    finally:
        set_audit_sink(None)


# ----------------------------------------------------------------------
# Optional real DB round-trip
# ----------------------------------------------------------------------

def _audit_table_available() -> bool:
    try:
        store = PostgresAuditStore.from_env()
        conn = store._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM audit.query_audit LIMIT 1")
        finally:
            conn.close()
        return True
    except Exception:
        return False


requires_audit_db = pytest.mark.skipif(
    not _audit_table_available(), reason="audit.query_audit not reachable"
)


@requires_audit_db
def test_roundtrip_against_real_db():
    store = PostgresAuditStore.from_env()
    tc = _populated_trace()
    tc.query_id = "qid-roundtrip-test"
    store.persist_query(tc)

    conn = store._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, llm_calls, total_cost_usd, branch, fully_answered "
                "FROM audit.query_audit WHERE query_id = %s",
                (tc.query_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "analyst1"
            assert row[1] == 1

            cur.execute(
                "SELECT count(*) FROM audit.query_event WHERE query_id = %s AND kind = 'numeric_lineage'",
                (tc.query_id,),
            )
            assert cur.fetchone()[0] == 1
        # cleanup
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit.query_audit WHERE query_id = %s", (tc.query_id,))
    finally:
        conn.close()
