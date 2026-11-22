"""Postgres-backed durable sink for per-query observability (Phase 3).

Persists each completed :class:`~src.observability.trace_context.TraceContext`
to ``audit.query_audit`` (one rollup row) + ``audit.query_event`` (one row per
structured event), the schema defined in ``scripts/ddl/audit/query_audit.sql``.

Registered process-wide via
:func:`src.observability.trace_context.set_audit_sink`; the XBRL pipeline does
this from env config. Writes are best-effort — a DB outage degrades to
"events still in the log stream", never an error in the request path.

Connection params come from the same ``XBRL_DB_*`` env vars as
:class:`~src.retriever.xbrl.entity_extractor.EntityExtractor`.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from src.db.scrub import scrub_nul
from src.logging_config import get_logger
from src.observability.trace_context import AuditSink, TraceContext

logger = get_logger(__name__)


# Events that are NOT persisted as query_event rows: query_summary is the
# audit.query_audit rollup itself, so storing it again would be redundant.
_SUMMARY_EVENT_KINDS = {"query_summary"}

_INSERT_QUERY_AUDIT = """
    INSERT INTO audit.query_audit (
        query_id, user_id, query, started_at, total_latency_ms, llm_calls,
        total_prompt_tokens, total_completion_tokens, total_cost_usd,
        total_retries, branch, intent, degraded, fully_answered,
        stage_latency_ms, error, answer
    ) VALUES (
        %(query_id)s, %(user_id)s, %(query)s, %(started_at)s, %(total_latency_ms)s, %(llm_calls)s,
        %(total_prompt_tokens)s, %(total_completion_tokens)s, %(total_cost_usd)s,
        %(total_retries)s, %(branch)s, %(intent)s, %(degraded)s, %(fully_answered)s,
        %(stage_latency_ms)s, %(error)s, %(answer)s
    )
    ON CONFLICT (query_id) DO UPDATE SET
        total_latency_ms        = EXCLUDED.total_latency_ms,
        llm_calls               = EXCLUDED.llm_calls,
        total_prompt_tokens     = EXCLUDED.total_prompt_tokens,
        total_completion_tokens = EXCLUDED.total_completion_tokens,
        total_cost_usd          = EXCLUDED.total_cost_usd,
        total_retries           = EXCLUDED.total_retries,
        branch                  = EXCLUDED.branch,
        intent                  = EXCLUDED.intent,
        degraded                = EXCLUDED.degraded,
        fully_answered          = EXCLUDED.fully_answered,
        stage_latency_ms        = EXCLUDED.stage_latency_ms,
        error                   = EXCLUDED.error,
        answer                  = EXCLUDED.answer
"""

_INSERT_QUERY_EVENT = """
    INSERT INTO audit.query_event (query_id, seq, kind, stage, payload)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (query_id, seq) DO NOTHING
"""


class PostgresAuditStore(AuditSink):
    """Writes a completed query's trace to the ``audit`` schema."""

    def __init__(self, db_conn_params: dict | None = None):
        self._db_conn_params = db_conn_params or {}

    @classmethod
    def from_env(cls, dotenv_path: str | None = None) -> PostgresAuditStore:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path)
        from src.db.conn import conn_params

        return cls(db_conn_params=conn_params())

    def _connect(self):
        import psycopg2

        return psycopg2.connect(**self._db_conn_params)

    # ------------------------------------------------------------------

    @staticmethod
    def _build_rows(trace: TraceContext) -> tuple[dict[str, Any], list[tuple]]:
        """Build the ``(query_audit_row, query_event_rows)`` for *trace*.

        Pure (no DB / psycopg2 types) so it is unit-testable in isolation.
        ``stage_latency_ms`` stays a dict and event ``payload`` stays a dict;
        :meth:`persist_query` wraps them as JSONB at write time.
        """
        ann = trace.annotations
        audit_row: dict[str, Any] = {
            "query_id": trace.query_id,
            "user_id": trace.user_id,
            "query": trace.query,
            "started_at": trace.started_wall,
            "total_latency_ms": trace.total_latency_ms,
            "llm_calls": trace.llm_calls,
            "total_prompt_tokens": trace.total_prompt_tokens,
            "total_completion_tokens": trace.total_completion_tokens,
            "total_cost_usd": round(trace.total_cost_usd, 6),
            "total_retries": trace.total_retries,
            "branch": ann.get("branch"),
            "intent": ann.get("intent"),
            "degraded": ann.get("degraded"),
            "fully_answered": ann.get("fully_answered"),
            "stage_latency_ms": {
                k: round(v, 1) for k, v in trace.stage_latency_ms.items()
            },
            "error": trace.error,
            "answer": trace.answer,
        }

        event_rows: list[tuple] = []
        seq = 0
        for event in trace.events:
            if event.get("kind") in _SUMMARY_EVENT_KINDS:
                continue
            event_rows.append(
                (
                    trace.query_id,
                    seq,
                    event.get("kind"),
                    event.get("stage"),
                    event,  # full payload; wrapped as Json in persist_query
                )
            )
            seq += 1
        return audit_row, event_rows

    def persist_query(self, trace: TraceContext) -> None:
        from psycopg2.extras import Json

        audit_row, event_rows = self._build_rows(trace)
        # Postgres rejects U+0000 in text and jsonb, and the write below is atomic: one
        # stray NUL (query, user_id, an LLM response in a payload) would lose the whole
        # trace. Scrub every parameter here, before Json wrapping, so new columns and
        # payload fields are covered too.
        audit_row = scrub_nul(audit_row)
        event_rows = scrub_nul(event_rows)
        audit_row["stage_latency_ms"] = Json(audit_row["stage_latency_ms"])
        audit_row["answer"] = (
            Json(audit_row["answer"]) if audit_row.get("answer") is not None else None
        )
        event_params = [
            (qid, seq, kind, stage, Json(payload))
            for (qid, seq, kind, stage, payload) in event_rows
        ]

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_QUERY_AUDIT, audit_row)
                    if event_params:
                        cur.executemany(_INSERT_QUERY_EVENT, event_params)
        finally:
            conn.close()
        logger.debug(
            "persisted audit for query_id=%s (%d events)",
            trace.query_id,
            len(event_params),
        )


def enable_audit_persistence() -> None:
    """Registers the Postgres audit sink process-wide, unless a sink is already set.

    From then on every completed ``trace_query`` is written to ``audit.query_audit``
    and ``audit.query_event``. Best-effort: if the store can't be built, the caller
    still runs and its telemetry stays in the ``xbrlrag.events`` stream.
    """
    from src.observability.trace_context import get_audit_sink, set_audit_sink

    if get_audit_sink() is not None:
        return
    try:
        set_audit_sink(PostgresAuditStore.from_env())
        logger.info("XBRL audit persistence enabled (audit.query_audit / query_event)")
    except Exception:  # isolation point: persistence is optional telemetry
        logger.exception("could not enable Postgres audit persistence; continuing without it")
