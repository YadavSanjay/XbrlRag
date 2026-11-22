"""Ingestion-side run lineage for the XBRL flow (Phase 3 / §4.8).

Records every ingestion run — the Arelle xbrl_db load and the Qdrant
vector-DB load — to ``audit.ingestion_run`` (one rollup row) +
``audit.ingestion_run_item`` (one row per filing), the schema in
``scripts/ddl/audit/ingestion_runs.sql``.

The ingestion pipelines have no LLM calls, so this is *operational* lineage
(run id, timestamps, durations, per-filing outcome), not cost. Crucially it
classifies each failure as **retryable** (Arelle timeout) vs **permanent**
(validation / config), so a run isn't a single opaque "N failed" tally.

Usage::

    with record_ingestion_run("arelle_xbrl_ingest", scope="ticker=NVDA") as run:
        for r in results:
            run.add_item_from_arelle(r)
        run.set_counts(status="PARTIAL", counts={...}, errors=[...])
    # persisted on exit (best-effort; a DB outage never breaks the run)
"""

from __future__ import annotations

import datetime
import os
import socket
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.logging_config import get_logger

logger = get_logger(__name__)


# Arelle return-code -> failure class. _run_arelle encodes:
#   0   success            -1  subprocess timeout       -2  arelleCmdLine not found
#   -3  other exception    >0  arelle validation/processing error
def classify_arelle_failure(returncode: int | None) -> str | None:
    """Map an Arelle return code to a failure class (None when it succeeded).

    ``timeout`` is the only *retryable* class; the rest are effectively
    permanent (re-running won't help without intervention).
    """
    if returncode is None:
        return "unhandled"
    if returncode == 0:
        return None
    if returncode == -1:
        return "timeout"  # retryable
    if returncode == -2:
        return "arelle_not_found"  # config / environment — permanent
    if returncode == -3:
        return "subprocess_error"
    return "validation_error"  # rc > 0: Arelle rejected the filing — permanent


@dataclass
class IngestionRunItem:
    seq: int
    accession_no: str | None = None
    ticker: str | None = None
    fiscal_year: str | None = None
    form_type: str | None = None
    status: str = "success"  # success | skipped_* | failed | dry_run | error
    failure_class: str | None = None
    returncode: int | None = None
    error: str | None = None
    attempts: int = 1  # number of processing attempts (>1 => a retry happened)


_SKIPPED_STATUSES = {"skipped_duplicate", "skipped_no_manifest", "skipped_unsupported"}
_FAILED_STATUSES = {"failed", "error"}


@dataclass
class IngestionRunRecord:
    """Mutable accumulator for one ingestion run; persisted on completion."""

    pipeline: str
    scope: str | None = None
    dry_run: bool = False
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat()
    )
    host: str = field(default_factory=lambda: _safe_hostname())
    items: list[IngestionRunItem] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    status: str = "OK"
    finished_at: str | None = None
    duration_ms: float | None = None

    _t0: float = field(default_factory=time.perf_counter, repr=False)

    # -- item recording -------------------------------------------------

    def add_item(self, **kwargs: Any) -> IngestionRunItem:
        item = IngestionRunItem(seq=len(self.items), **kwargs)
        self.items.append(item)
        return item

    def add_item_from_arelle(self, result: Any) -> IngestionRunItem:
        """Build an item from an Arelle ``IngestResult`` (duck-typed)."""
        status, failure_class = _arelle_status(result)
        return self.add_item(
            accession_no=getattr(result, "accession_no", None) or None,
            ticker=getattr(result, "ticker", None),
            fiscal_year=str(getattr(result, "year", "") or "") or None,
            form_type=getattr(result, "form_type", None),
            status=status,
            failure_class=failure_class,
            returncode=getattr(result, "returncode", None),
            error=getattr(result, "error", None),
            attempts=int(getattr(result, "attempts", 1) or 1),
        )

    # -- finalize -------------------------------------------------------

    def set_counts(
        self,
        status: str | None = None,
        counts: dict[str, Any] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        if status is not None:
            self.status = status
        if counts is not None:
            self.counts = {
                k: v
                for k, v in counts.items()
                if isinstance(v, (int, float, bool, str))
            }
        if errors is not None:
            self.errors = list(errors)

    def finish(self) -> None:
        self.finished_at = datetime.datetime.now(datetime.UTC).isoformat()
        self.duration_ms = round((time.perf_counter() - self._t0) * 1000.0, 1)

    # -- derived tallies ------------------------------------------------

    @property
    def items_total(self) -> int:
        return len(self.items)

    @property
    def items_succeeded(self) -> int:
        return sum(1 for i in self.items if i.status == "success")

    @property
    def items_skipped(self) -> int:
        return sum(1 for i in self.items if i.status in _SKIPPED_STATUSES)

    @property
    def items_failed(self) -> int:
        return sum(1 for i in self.items if i.status in _FAILED_STATUSES)


def _safe_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def _arelle_status(result: Any) -> tuple[str, str | None]:
    """Map an Arelle IngestResult to (item_status, failure_class)."""
    if getattr(result, "skipped_duplicate", False):
        return "skipped_duplicate", None
    if getattr(result, "dry_run", False):
        return "dry_run", None
    if getattr(result, "success", False):
        return "success", None
    err = (getattr(result, "error", None) or "").lower()
    if "no manifest" in err:
        return "skipped_no_manifest", None
    if "unsupported" in err:
        return "skipped_unsupported", None
    return "failed", classify_arelle_failure(getattr(result, "returncode", None))


# ----------------------------------------------------------------------
# Postgres store
# ----------------------------------------------------------------------

_INSERT_RUN = """
    INSERT INTO audit.ingestion_run (
        run_id, pipeline, scope, dry_run, status, started_at, finished_at,
        duration_ms, items_total, items_succeeded, items_skipped, items_failed,
        counts, errors, host
    ) VALUES (
        %(run_id)s, %(pipeline)s, %(scope)s, %(dry_run)s, %(status)s, %(started_at)s, %(finished_at)s,
        %(duration_ms)s, %(items_total)s, %(items_succeeded)s, %(items_skipped)s, %(items_failed)s,
        %(counts)s, %(errors)s, %(host)s
    )
    ON CONFLICT (run_id) DO UPDATE SET
        status          = EXCLUDED.status,
        finished_at     = EXCLUDED.finished_at,
        duration_ms     = EXCLUDED.duration_ms,
        items_total     = EXCLUDED.items_total,
        items_succeeded = EXCLUDED.items_succeeded,
        items_skipped   = EXCLUDED.items_skipped,
        items_failed    = EXCLUDED.items_failed,
        counts          = EXCLUDED.counts,
        errors          = EXCLUDED.errors
"""

_INSERT_ITEM = """
    INSERT INTO audit.ingestion_run_item (
        run_id, seq, accession_no, ticker, fiscal_year, form_type,
        status, failure_class, returncode, error, attempts
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (run_id, seq) DO NOTHING
"""


class PostgresIngestionAuditStore:
    """Writes an :class:`IngestionRunRecord` to the ``audit`` schema."""

    def __init__(self, db_conn_params: dict | None = None):
        self._db_conn_params = db_conn_params or {}

    @classmethod
    def from_env(
        cls, dotenv_path: str | None = None
    ) -> PostgresIngestionAuditStore:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path)
        from src.db.conn import conn_params

        return cls(db_conn_params=conn_params())

    def _connect(self):
        import psycopg2

        return psycopg2.connect(**self._db_conn_params)

    @staticmethod
    def _build_rows(record: IngestionRunRecord) -> tuple[dict[str, Any], list[tuple]]:
        """Pure ``(run_row, item_rows)`` build — no DB / psycopg2 types."""
        run_row = {
            "run_id": record.run_id,
            "pipeline": record.pipeline,
            "scope": record.scope,
            "dry_run": record.dry_run,
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "duration_ms": record.duration_ms,
            "items_total": record.items_total,
            "items_succeeded": record.items_succeeded,
            "items_skipped": record.items_skipped,
            "items_failed": record.items_failed,
            "counts": record.counts,
            "errors": record.errors,
            "host": record.host,
        }
        item_rows = [
            (
                record.run_id,
                i.seq,
                i.accession_no,
                i.ticker,
                i.fiscal_year,
                i.form_type,
                i.status,
                i.failure_class,
                i.returncode,
                i.error,
                i.attempts,
            )
            for i in record.items
        ]
        return run_row, item_rows

    def persist(self, record: IngestionRunRecord) -> None:
        from psycopg2.extras import Json

        run_row, item_rows = self._build_rows(record)
        run_row = dict(run_row)
        run_row["counts"] = Json(run_row["counts"])
        run_row["errors"] = Json(run_row["errors"])

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_RUN, run_row)
                    if item_rows:
                        cur.executemany(_INSERT_ITEM, item_rows)
        finally:
            conn.close()
        logger.debug(
            "persisted ingestion run %s (%d items)", record.run_id, len(item_rows)
        )


@contextmanager
def record_ingestion_run(
    pipeline: str,
    scope: str | None = None,
    dry_run: bool = False,
    store: Any = "auto",
) -> Iterator[IngestionRunRecord]:
    """Wrap an ingestion run, persisting its lineage on exit (best-effort).

    ``store="auto"`` constructs a :class:`PostgresIngestionAuditStore` from env;
    pass an explicit store, or ``None`` to skip persistence (e.g. tests).
    Persistence failures are swallowed — they must never fail the ingest.
    """
    record = IngestionRunRecord(pipeline=pipeline, scope=scope, dry_run=dry_run)
    error: BaseException | None = None
    try:
        yield record
    except BaseException as exc:  # ensure we still record a failed run
        error = exc
        if not record.errors:
            record.errors = [repr(exc)]
        record.status = "ERROR"
        raise
    finally:
        record.finish()
        _persist_record(record, store)
        if error is None:
            logger.info(
                "ingestion run %s (%s) %s in %.0fms: total=%d ok=%d skipped=%d failed=%d",
                record.run_id,
                record.pipeline,
                record.status,
                record.duration_ms or 0,
                record.items_total,
                record.items_succeeded,
                record.items_skipped,
                record.items_failed,
            )


def _persist_record(record: IngestionRunRecord, store: Any) -> None:
    if store is None:
        return
    try:
        if store == "auto":
            store = PostgresIngestionAuditStore.from_env()
        store.persist(record)
    except Exception:  # never let persistence break the ingest run
        logger.exception("ingestion audit persist failed for run_id=%s", record.run_id)
