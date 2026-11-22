"""Unit tests for the per-query trace context (Phase 1 observability)."""
import json
import logging

import pytest

from src.observability.trace_context import (
    current_query_id,
    current_stage,
    current_trace,
    log_event,
    trace_query,
    traced_stage,
)


@pytest.fixture
def events():
    """Collect ``xbrlrag.events`` records directly.

    The ``xbrlrag.events`` logger has ``propagate=False`` in production config, so
    pytest's ``caplog`` (rooted handler) cannot see it — attach our own handler.
    """
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


def test_no_trace_active_by_default():
    assert current_trace() is None
    assert current_query_id() == "-"
    assert current_stage() == "-"


def test_trace_query_sets_and_clears_context():
    with trace_query("What was Apple's revenue?", user_id="u1") as tc:
        assert current_trace() is tc
        assert current_query_id() == tc.query_id
        assert tc.user_id == "u1"
    # context is restored after exit
    assert current_trace() is None
    assert current_query_id() == "-"


def test_explicit_query_id_is_honored():
    with trace_query("q", query_id="fixed-id-123") as tc:
        assert tc.query_id == "fixed-id-123"
        assert current_query_id() == "fixed-id-123"


def test_traced_stage_records_latency():
    with trace_query("q") as tc:
        with traced_stage("intent_router"):
            assert current_stage() == "intent_router"
        assert current_stage() == "-"
        assert "intent_router" in tc.stage_latency_ms
        assert tc.stage_latency_ms["intent_router"] >= 0.0


def test_traced_stage_accumulates_repeated_runs():
    with trace_query("q") as tc:
        with traced_stage("plan_generator"):
            pass
        first = tc.stage_latency_ms["plan_generator"]
        with traced_stage("plan_generator"):
            pass
        assert tc.stage_latency_ms["plan_generator"] >= first


def test_record_llm_call_accumulates_totals():
    with trace_query("q") as tc:
        tc.record_llm_call(prompt_tokens=100, completion_tokens=20, cost_usd=0.001, retries=1)
        tc.record_llm_call(prompt_tokens=50, completion_tokens=10, cost_usd=0.0005)
        assert tc.llm_calls == 2
        assert tc.total_prompt_tokens == 150
        assert tc.total_completion_tokens == 30
        assert tc.total_retries == 1
        assert abs(tc.total_cost_usd - 0.0015) < 1e-9


def test_traced_stage_without_trace_is_noop():
    # No active trace -> recording is skipped, no exception raised.
    with traced_stage("orphan_stage"):
        assert current_stage() == "orphan_stage"
    assert current_stage() == "-"


def test_log_event_includes_query_id_and_stage(events):
    with trace_query("q", query_id="qid-xyz"):
        with traced_stage("synthesizer"):
            log_event("custom", foo="bar")

    custom = [e for e in events if e["kind"] == "custom"]
    assert custom, "custom event was not emitted"
    assert custom[0]["query_id"] == "qid-xyz"
    assert custom[0]["stage"] == "synthesizer"
    assert custom[0]["foo"] == "bar"


def test_query_summary_emitted_on_exit(events):
    with trace_query("q") as tc:
        tc.record_llm_call(prompt_tokens=10, completion_tokens=5, cost_usd=0.002)
        tc.annotate(branch="PLAN_GENERATOR", fully_answered=True)

    summaries = [e for e in events if e["kind"] == "query_summary"]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["llm_calls"] == 1
    assert summary["total_cost_usd"] == 0.002
    assert summary["branch"] == "PLAN_GENERATOR"
    assert summary["fully_answered"] is True
    assert summary["error"] is None


def test_query_summary_records_error_on_exception(events):
    try:
        with trace_query("q"):
            raise ValueError("boom")
    except ValueError:
        pass

    summary = next(e for e in events if e["kind"] == "query_summary")
    assert "boom" in summary["error"]
