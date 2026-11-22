"""DB round-trip tests for the audit.* analyst views.

Seeds a query + an ingestion run via the production writers, asserts the views
compute the expected rollups, then cleans up. Skips entirely if the audit views
are not present in a reachable xbrl_db.

This is an integration test, not a unit test: what it checks is the SQL of the
views (scripts/ddl/audit/), which only a real Postgres can execute, and it
writes rows to the live `audit` schema. Moved from test/unit/ so that
`pytest test/unit` never touches a database.
"""
import pytest

from src.observability.audit_store import PostgresAuditStore
from src.observability.ingestion_audit import IngestionRunRecord, PostgresIngestionAuditStore
from src.observability.trace_context import TraceContext

QID = "qid-views-pytest"
RID = "ingest-views-pytest"
FACT = 424242

pytestmark = pytest.mark.integration


def _views_available() -> bool:
    try:
        conn = PostgresAuditStore.from_env()._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM audit.v_query_latency")
                cur.execute("SELECT 1 FROM audit.v_ingestion_failures")
        finally:
            conn.close()
        return True
    except Exception:
        return False


requires_views = pytest.mark.skipif(not _views_available(), reason="audit.* views not reachable")


def _seed():
    tc = TraceContext(query_id=QID, query="views pytest query", user_id="tester")
    tc.record_llm_call(prompt_tokens=500, completion_tokens=100, cost_usd=0.0009, retries=0)
    tc.record_stage("synthesizer", 700.0)
    tc.annotate(
        branch="PLAN_GENERATOR", intent="NUMERIC_COMPUTE", degraded=False, fully_answered=True
    )
    tc.total_latency_ms = 1500.0
    tc.events = [
        {"kind": "llm_call", "stage": "synthesizer", "model": "gpt-5-mini", "outcome": "ok",
         "prompt_tokens": 500, "completion_tokens": 100, "cost_usd": 0.0009,
         "latency_ms": 700, "retries": 0, "finish_reason": "stop"},
        {"kind": "numeric_lineage", "stage": "plan_exec", "op": "compute", "step_id": "g",
         "status": "OK", "expr": "a/b", "result": "0.5",
         "source_fact_ids": {"a": FACT, "b": 999}},
        {"kind": "verifier_error", "stage": "verifier", "status": "PARSE_ERROR",
         "error": "bad json"},
    ]
    PostgresAuditStore.from_env().persist_query(tc)

    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest", scope="views-pytest")
    rec.run_id = RID
    rec.add_item(accession_no="A1", status="success", attempts=1)
    rec.add_item(accession_no="A2", status="success", attempts=2)
    rec.add_item(
        accession_no="A3", status="failed", failure_class="timeout", returncode=-1, attempts=2
    )
    rec.add_item(accession_no="A4", status="failed", failure_class="validation_error", returncode=1)
    rec.set_counts(status="PARTIAL", counts={"succeeded": 2, "failed": 2})
    rec.finish()
    PostgresIngestionAuditStore.from_env().persist(rec)


def _cleanup(conn):
    with conn:
        with conn.cursor() as c:
            c.execute("DELETE FROM audit.query_audit WHERE query_id=%s", (QID,))
            c.execute("DELETE FROM audit.ingestion_run WHERE run_id=%s", (RID,))


@requires_views
def test_views_round_trip():
    _seed()
    conn = PostgresAuditStore.from_env()._connect()
    try:
        cur = conn.cursor()

        # numeric lineage flattened + fact-to-query lookup (the audit feature)
        cur.execute("SELECT op, expr FROM audit.v_numeric_lineage WHERE query_id=%s", (QID,))
        assert cur.fetchone() == ("compute", "a/b")
        cur.execute("SELECT query_id FROM audit.queries_for_fact(%s)", (FACT,))
        assert (QID,) in cur.fetchall()

        # verifier failure surfaced
        cur.execute(
            "SELECT verifier_status FROM audit.v_verifier_failures WHERE query_id=%s", (QID,)
        )
        assert cur.fetchone() == ("PARSE_ERROR",)

        # llm economics
        cur.execute(
            "SELECT calls, cost_usd FROM audit.v_llm_calls"
            " WHERE stage='synthesizer' AND model='gpt-5-mini'"
        )
        calls, cost = cur.fetchone()
        assert calls >= 1 and cost is not None

        # ingestion failure triage: timeout=retryable, validation=permanent
        cur.execute("SELECT failure_class, retryability FROM audit.v_ingestion_failures")
        triage = dict(cur.fetchall())
        assert triage.get("timeout") == "retryable"
        assert triage.get("validation_error") == "permanent"

        # retry effectiveness reflects our seeded items (A2 retried-success, A3 retried-fail)
        cur.execute(
            "SELECT items_retried, retried_then_succeeded, retried_still_failed"
            " FROM audit.v_ingestion_retries"
        )
        retried, ok_after, fail_after = cur.fetchone()
        assert retried >= 2 and ok_after >= 1 and fail_after >= 1

        cur.close()
    finally:
        _cleanup(conn)
        conn.close()
