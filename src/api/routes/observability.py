"""Observability endpoints — thin reads over the ``audit`` schema + analyst views.

All endpoints degrade gracefully: if the audit DB is not reachable, they return
empty result sets (the UI renders a "store not configured" state). The view and
column names follow ``scripts/ddl/audit/`` (query_audit / query_event /
ingestion_run / analyst_views).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter

from src.api import db
from src.api.config import log_file_path
from src.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["observability"])


# ----------------------------------------------------------------------------
# Traces (per-query request flow)
# ----------------------------------------------------------------------------

@router.get("/traces")
def list_traces(limit: int = 50, offset: int = 0):
    """Recent queries — header rows for the Trace Explorer list (B3)."""
    rows = db.query(
        """
        SELECT query_id, query, branch, intent, degraded, fully_answered,
               total_latency_ms, total_cost_usd, llm_calls, total_retries,
               error, started_at
        FROM audit.query_audit
        ORDER BY started_at DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )
    return {"items": rows}


@router.get("/traces/{query_id}")
def get_trace(query_id: str):
    """Full trace: the audit rollup row + ordered structured events."""
    header = db.query_one(
        "SELECT * FROM audit.query_audit WHERE query_id = %s", (query_id,)
    )
    events = db.query(
        """
        SELECT seq, kind, stage, payload
        FROM audit.query_event
        WHERE query_id = %s
        ORDER BY seq ASC
        """,
        (query_id,),
    )
    return {"header": header, "events": events}


# ----------------------------------------------------------------------------
# Numeric lineage & audit (B5)
# ----------------------------------------------------------------------------

@router.get("/lineage/{query_id}")
def lineage(query_id: str):
    """Flattened lookup + compute lineage for a query (every number -> source).

    Reads the raw ``numeric_lineage`` event payloads (which carry the full
    fields — value, unit, bindings, source_fact_ids, expr/result) rather than
    ``audit.v_numeric_lineage``, whose projection omits lookup ``value``/``unit``.
    """
    rows = db.query(
        """
        SELECT payload
        FROM audit.query_event
        WHERE query_id = %s AND kind = 'numeric_lineage'
        ORDER BY seq
        """,
        (query_id,),
    )
    return {"items": [r["payload"] for r in rows]}


@router.get("/facts/{fact_id}/queries")
def queries_for_fact(fact_id: int):
    """Reverse audit: which answers used fact N."""
    rows = db.query("SELECT * FROM audit.queries_for_fact(%s)", (fact_id,))
    return {"items": rows}


# ----------------------------------------------------------------------------
# Metrics (B1 overview, B4 LLM)
# ----------------------------------------------------------------------------

@router.get("/metrics/overview")
def metrics_overview():
    return {
        "daily": db.query("SELECT * FROM audit.v_query_daily ORDER BY day DESC LIMIT 30"),
        "latency": db.query_one("SELECT * FROM audit.v_query_latency"),
        "branch_mix": db.query("SELECT * FROM audit.v_branch_mix"),
        "stages": db.query("SELECT * FROM audit.v_stage_latency ORDER BY p95_ms DESC NULLS LAST"),
    }


@router.get("/metrics/llm")
def metrics_llm():
    return {
        "calls": db.query("SELECT * FROM audit.v_llm_calls"),
        "model_cost": db.query("SELECT * FROM audit.v_model_cost"),
    }


# ----------------------------------------------------------------------------
# Ingestion & data ops (B6)
# ----------------------------------------------------------------------------

@router.get("/ingestion/runs")
def ingestion_runs(limit: int = 50):
    runs = db.query(
        """
        SELECT run_id, pipeline, scope, dry_run, status, started_at, finished_at,
               duration_ms, items_total, items_succeeded, items_skipped,
               items_failed, host
        FROM audit.ingestion_run
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return {"items": runs}


@router.get("/ingestion/runs/{run_id}/items")
def ingestion_run_items(run_id: str):
    items = db.query(
        """
        SELECT seq, accession_no, ticker, fiscal_year, form_type, status,
               failure_class, returncode, attempts, error
        FROM audit.ingestion_run_item
        WHERE run_id = %s
        ORDER BY seq
        """,
        (run_id,),
    )
    return {"items": items}


@router.get("/ingestion/analytics")
def ingestion_analytics():
    """Failure-triage + retry-effectiveness rollups for the Ingestion Ops screen.

    - failures: counts per failure_class split retryable vs permanent
      (audit.v_ingestion_failures)
    - retries: retried→succeeded vs retried→still-failed (audit.v_ingestion_retries)
    - daily: per-pipeline throughput per day (audit.v_ingestion_daily)
    """
    return {
        "failures": db.query("SELECT * FROM audit.v_ingestion_failures"),
        "retries": db.query("SELECT * FROM audit.v_ingestion_retries"),
        "daily": db.query("SELECT * FROM audit.v_ingestion_daily ORDER BY day DESC LIMIT 30"),
    }


# ----------------------------------------------------------------------------
# Prompt registry (B4)
# ----------------------------------------------------------------------------

@router.get("/prompts")
def prompts():
    """List prompt templates with a short content hash (version)."""
    import hashlib

    root = os.path.join("prompts", "templates")
    items: list[dict[str, Any]] = []
    if not os.path.isdir(root):
        return {"items": items, "root": root}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith((".txt", ".jinja", ".j2", ".md")):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
            except Exception:
                text, version = "", "?"
            items.append({
                "name": os.path.relpath(path, root).replace(os.sep, "/"),
                "version": version,
                "chars": len(text),
            })
    items.sort(key=lambda x: x["name"])
    return {"items": items, "root": root}


# ----------------------------------------------------------------------------
# Logs (B7)
# ----------------------------------------------------------------------------

@router.get("/logs")
def logs(limit: int = 200, query_id: str = "", level: str = ""):
    """Tail the structured JSON log file, newest first, with simple filters."""
    import json

    path = log_file_path()
    if not os.path.isfile(path):
        return {"items": [], "path": path, "error": "log file not found"}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-2000:]
    except Exception as exc:
        return {"items": [], "path": path, "error": str(exc)}

    items: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            rec = {"message": line}
        if query_id and rec.get("query_id") != query_id:
            continue
        if level and str(rec.get("level", "")).upper() != level.upper():
            continue
        items.append(rec)
        if len(items) >= limit:
            break
    return {"items": items, "path": path}
