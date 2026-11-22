"""Prometheus scrape endpoint — ``GET /metrics`` (WS-7 / B8).

Exposes the ``audit.v_*`` analyst views as Prometheus metrics for scraping by a
Prometheus server (see ``configs/monitoring/prometheus.yml``). Deliberately
mounted at the root ``/metrics`` (not ``/api/metrics``) to match the Prometheus
convention and to keep it distinct from the BFF's JSON ``/api/metrics/*`` reads.

Degrades gracefully: :func:`render_exposition` emits ``arag_audit_db_up 0`` (and
otherwise-empty metrics) when the audit DB is unreachable, so a scrape always
succeeds with a parseable body — the AragAuditDBDown alert then fires on the gauge.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from src.logging_config import get_logger
from src.observability.metrics_exporter import render_exposition

logger = get_logger(__name__)
router = APIRouter(tags=["metrics"])

# Prometheus text exposition format content type (v0.0.4).
_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus scrape target over the audit analyst views."""
    try:
        body = render_exposition()
    except Exception as exc:  # pragma: no cover - render already defensive
        logger.warning("metrics render failed: %s", exc)
        body = (
            "# HELP arag_audit_db_up 1 if the audit DB answered the metrics probe, else 0.\n"
            "# TYPE arag_audit_db_up gauge\n"
            "arag_audit_db_up 0\n"
        )
    return PlainTextResponse(content=body, media_type=_CONTENT_TYPE)
