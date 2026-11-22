"""Unit tests for ingestion-side run lineage (Phase 3 / §4.8)."""
from dataclasses import dataclass
from typing import Optional

import pytest

from src.observability.ingestion_audit import (
    IngestionRunRecord,
    PostgresIngestionAuditStore,
    classify_arelle_failure,
    record_ingestion_run,
)

# ----------------------------------------------------------------------
# Failure classification (retryable vs permanent)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("rc,expected", [
    (0, None),            # success
    (-1, "timeout"),       # retryable
    (-2, "arelle_not_found"),
    (-3, "subprocess_error"),
    (1, "validation_error"),   # rc > 0 -> Arelle rejected the filing (permanent)
    (5, "validation_error"),
    (None, "unhandled"),
])
def test_classify_arelle_failure(rc, expected):
    assert classify_arelle_failure(rc) == expected


def test_timeout_is_the_only_retryable_class():
    # operationally important: timeouts are worth retrying, the rest are not
    retryable = {rc for rc in (-1, -2, -3, 1, 2) if classify_arelle_failure(rc) == "timeout"}
    assert retryable == {-1}


# ----------------------------------------------------------------------
# add_item_from_arelle (duck-typed IngestResult)
# ----------------------------------------------------------------------

@dataclass
class _FakeIngestResult:
    accession_no: str = ""
    ticker: str = "NVDA"
    year: str = "2026"
    form_type: str = "10-K"
    skipped_duplicate: bool = False
    dry_run: bool = False
    success: bool = False
    returncode: int | None = None
    error: str | None = None
    attempts: int = 1


def test_add_item_success():
    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest")
    rec.add_item_from_arelle(_FakeIngestResult(accession_no="A1", success=True, returncode=0))
    item = rec.items[0]
    assert item.status == "success"
    assert item.failure_class is None
    assert item.accession_no == "A1"
    assert item.ticker == "NVDA" and item.fiscal_year == "2026"


def test_add_item_timeout_failure_is_classified():
    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest")
    rec.add_item_from_arelle(_FakeIngestResult(returncode=-1, error="arelle exit code -1"))
    item = rec.items[0]
    assert item.status == "failed"
    assert item.failure_class == "timeout"  # retryable


def test_add_item_validation_failure_is_permanent():
    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest")
    rec.add_item_from_arelle(_FakeIngestResult(returncode=3, error="arelle exit code 3"))
    assert rec.items[0].failure_class == "validation_error"


def test_add_item_records_retry_attempts():
    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest")
    rec.add_item_from_arelle(_FakeIngestResult(success=True, returncode=0, attempts=2))
    assert rec.items[0].attempts == 2  # succeeded only after a retry


def test_add_item_skips_and_duplicates():
    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest")
    rec.add_item_from_arelle(_FakeIngestResult(skipped_duplicate=True))
    rec.add_item_from_arelle(_FakeIngestResult(error="No manifest.yaml found"))
    rec.add_item_from_arelle(_FakeIngestResult(error="Unsupported extension: .txt"))
    statuses = [i.status for i in rec.items]
    assert statuses == ["skipped_duplicate", "skipped_no_manifest", "skipped_unsupported"]
    assert all(i.failure_class is None for i in rec.items)


def test_tallies():
    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest")
    rec.add_item_from_arelle(_FakeIngestResult(success=True, returncode=0))
    rec.add_item_from_arelle(_FakeIngestResult(success=True, returncode=0))
    rec.add_item_from_arelle(_FakeIngestResult(skipped_duplicate=True))
    rec.add_item_from_arelle(_FakeIngestResult(returncode=-1, error="x"))
    assert rec.items_total == 4
    assert rec.items_succeeded == 2
    assert rec.items_skipped == 1
    assert rec.items_failed == 1


# ----------------------------------------------------------------------
# _build_rows (pure)
# ----------------------------------------------------------------------

def test_build_rows():
    rec = IngestionRunRecord(pipeline="load_to_vectordb", scope="ticker=NVDA", dry_run=True)
    rec.add_item(accession_no="A1", ticker="NVDA", fiscal_year="2026", form_type="10-K",
                 status="success")
    rec.set_counts(status="PARTIAL", counts={"targets_found": 3, "errors": ["x"]},
                   errors=["boom"])
    rec.finish()

    run_row, item_rows = PostgresIngestionAuditStore._build_rows(rec)
    assert run_row["pipeline"] == "load_to_vectordb"
    assert run_row["scope"] == "ticker=NVDA"
    assert run_row["dry_run"] is True
    assert run_row["status"] == "PARTIAL"
    assert run_row["items_total"] == 1
    assert run_row["items_succeeded"] == 1
    assert run_row["duration_ms"] is not None
    # non-scalar count values are filtered out
    assert run_row["counts"] == {"targets_found": 3}
    assert run_row["errors"] == ["boom"]

    assert len(item_rows) == 1
    # (run_id, seq, accession_no, ticker, fiscal_year, form_type, status,
    #  failure_class, rc, error, attempts)
    assert item_rows[0][2] == "A1"
    assert item_rows[0][6] == "success"
    assert item_rows[0][10] == 1  # attempts


# ----------------------------------------------------------------------
# record_ingestion_run context manager
# ----------------------------------------------------------------------

def test_record_ingestion_run_no_store_is_noop():
    with record_ingestion_run("load_to_vectordb", scope="all", store=None) as run:
        run.add_item(accession_no="A1", status="success")
    assert run.finished_at is not None
    assert run.duration_ms is not None


def test_record_ingestion_run_marks_error_on_exception():
    captured = {}

    class _RecordingStore:
        def persist(self, record):
            captured["record"] = record

    store = _RecordingStore()
    with pytest.raises(ValueError):
        with record_ingestion_run("arelle_xbrl_ingest", store=store) as run:
            run.add_item(accession_no="A1", status="success")
            raise ValueError("mid-run boom")

    rec = captured["record"]
    assert rec.status == "ERROR"
    assert any("boom" in e for e in rec.errors)
    assert rec.finished_at is not None  # still finalized + persisted


def test_persist_failure_does_not_break_run():
    class _BoomStore:
        def persist(self, record):
            raise RuntimeError("db down")

    # must complete cleanly despite the sink raising
    with record_ingestion_run("load_to_vectordb", store=_BoomStore()) as run:
        run.add_item(accession_no="A1", status="success")


# ----------------------------------------------------------------------
# persist against a fake connection
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

    def executemany(self, sql, seq):
        self._log.append(("executemany", sql, list(seq)))


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


def test_persist_writes_run_and_items(monkeypatch):
    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest", scope="all")
    rec.add_item(accession_no="A1", status="success")
    rec.add_item(accession_no="A2", status="failed", failure_class="timeout", returncode=-1)
    rec.set_counts(status="PARTIAL", counts={"succeeded": 1, "failed": 1})
    rec.finish()

    store = PostgresIngestionAuditStore(db_conn_params={})
    fake = _FakeConn()
    monkeypatch.setattr(store, "_connect", lambda: fake)
    store.persist(rec)

    assert fake.closed
    kinds = [c[0] for c in fake.calls]
    assert "execute" in kinds and "executemany" in kinds
    run_call = next(c for c in fake.calls if c[0] == "execute")
    assert "audit.ingestion_run" in run_call[1]
    assert run_call[2]["run_id"] == rec.run_id
    items_call = next(c for c in fake.calls if c[0] == "executemany")
    assert "audit.ingestion_run_item" in items_call[1]
    assert len(items_call[2]) == 2


# ----------------------------------------------------------------------
# Optional real DB round-trip
# ----------------------------------------------------------------------

def _audit_table_available() -> bool:
    try:
        store = PostgresIngestionAuditStore.from_env()
        conn = store._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM audit.ingestion_run LIMIT 1")
        finally:
            conn.close()
        return True
    except Exception:
        return False


requires_audit_db = pytest.mark.skipif(
    not _audit_table_available(), reason="audit.ingestion_run not reachable"
)


@requires_audit_db
def test_roundtrip_against_real_db():
    store = PostgresIngestionAuditStore.from_env()
    rec = IngestionRunRecord(pipeline="arelle_xbrl_ingest", scope="test")
    rec.run_id = "ingest-roundtrip-test"
    rec.add_item(accession_no="A1", ticker="NVDA", fiscal_year="2026", form_type="10-K",
                 status="failed", failure_class="timeout", returncode=-1, error="Timeout after 600s")
    rec.set_counts(status="ERROR", counts={"failed": 1}, errors=["A1: timeout"])
    rec.finish()
    store.persist(rec)

    conn = store._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pipeline, status, items_failed FROM audit.ingestion_run WHERE run_id = %s",
                (rec.run_id,),
            )
            assert cur.fetchone() == ("arelle_xbrl_ingest", "ERROR", 1)
            cur.execute(
                "SELECT failure_class FROM audit.ingestion_run_item WHERE run_id = %s",
                (rec.run_id,),
            )
            assert cur.fetchone()[0] == "timeout"
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit.ingestion_run WHERE run_id = %s", (rec.run_id,))
    finally:
        conn.close()
