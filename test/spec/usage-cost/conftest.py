"""Fixtures for the usage-cost spec tests (docs/specs/usage-cost.md)."""

from __future__ import annotations

import pytest

from src.observability.trace_context import AuditSink, get_audit_sink, set_audit_sink


class RecordingSink(AuditSink):
    """Audit sink fake (the DB boundary): records the query ids it is asked to persist."""

    def __init__(self) -> None:
        self.persisted: list[str] = []

    def persist_query(self, trace) -> None:
        self.persisted.append(trace.query_id)


@pytest.fixture
def sink():
    previous = get_audit_sink()
    fake = RecordingSink()
    set_audit_sink(fake)
    try:
        yield fake
    finally:
        set_audit_sink(previous)
