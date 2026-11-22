"""Observability primitives for the XBRL flow.

docs/design/XbrlRag_Observability_Tracing.md:
a per-query trace context, structured event emission, and per-stage timing.
"""
from src.observability.trace_context import (
    AuditSink,
    TraceContext,
    current_query_id,
    current_stage,
    current_trace,
    get_audit_sink,
    log_event,
    set_audit_sink,
    trace_query,
    traced_stage,
)

__all__ = [
    "TraceContext",
    "trace_query",
    "traced_stage",
    "log_event",
    "current_trace",
    "current_query_id",
    "current_stage",
    "AuditSink",
    "set_audit_sink",
    "get_audit_sink",
]
