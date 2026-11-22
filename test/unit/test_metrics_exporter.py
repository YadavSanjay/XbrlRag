"""Tests for the Prometheus metrics exporter (WS-7 / B8).

Most tests drive :func:`render_exposition` with a fake in-memory reader so the
whole collector is exercised without a database. One round-trip test hits the
live ``audit.v_*`` views (skipped when the DB is unreachable), and one drives the
``GET /metrics`` FastAPI route to assert content-type + graceful degradation.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.observability import metrics_exporter as mx
from src.observability.metrics_exporter import Exposition, render_exposition

# ---------------------------------------------------------------------------
# Fake reader — maps a SQL fragment to canned rows.
# ---------------------------------------------------------------------------

def make_reader(table_rows: dict[str, list[dict[str, Any]]]):
    """Return a reader that matches on the view name appearing in the SQL."""

    def reader(sql: str) -> list[dict[str, Any]]:
        if "SELECT 1 AS up" in sql:
            return table_rows.get("__probe__", [{"up": 1}])
        for key, rows in table_rows.items():
            if key.startswith("__"):
                continue
            if key in sql:
                return rows
        return []

    return reader


FULL_FIXTURE: dict[str, list[dict[str, Any]]] = {
    "v_query_latency": [
        {"queries": 14, "avg_ms": 8200, "p50_ms": 6000, "p95_ms": 31000, "p99_ms": 45000, "max_ms": 60000}
    ],
    "v_stage_latency": [
        {"stage": "synthesizer", "p50_ms": 700, "p95_ms": 1500, "p99_ms": 2200},
        {"stage": "narrative", "p50_ms": 90000, "p95_ms": 145000, "p99_ms": 160000},
    ],
    "v_query_daily": [
        {"day": "2026-07-14", "queries": 10, "errored": 2, "degraded": 3, "fully_answered": 7,
         "cost_usd": 6.5, "tokens": 12000, "llm_calls": 40, "retries": 1, "p95_latency_ms": 31000},
    ],
    "v_branch_mix": [
        {"branch": "PLAN_GENERATOR", "intent": "NUMERIC_COMPUTE", "queries": 6, "degraded": 0, "avg_cost_usd": 0.0021},
        {"branch": "NARRATIVE_BRANCH", "intent": "NARRATIVE", "queries": 4, "degraded": 3, "avg_cost_usd": 0.0009},
    ],
    "v_llm_calls": [
        {"stage": "synthesizer", "model": "gpt-5-mini", "calls": 20, "errors": 1, "retries": 2,
         "cost_usd": 0.04, "p95_latency_ms": 1400},
    ],
    "v_model_cost": [
        {"model": "gpt-5-mini", "calls": 20, "prompt_tokens": 8000, "completion_tokens": 2000, "cost_usd": 0.04},
    ],
    "v_verifier_failures": [{"n": 2}],
    "v_ingestion_daily": [
        {"day": "2026-07-14", "pipeline": "arelle_xbrl_ingest", "succeeded": 5, "failed": 1, "skipped": 0},
        {"day": "2026-07-13", "pipeline": "arelle_xbrl_ingest", "succeeded": 3, "failed": 0, "skipped": 0},
    ],
    "v_ingestion_failures": [
        {"failure_class": "timeout", "retryability": "retryable", "failures": 1, "runs_affected": 1},
    ],
}


def _parse(text: str) -> dict[str, list]:
    """Very small exposition parser: metric line -> list of (labels, value)."""
    out: dict[str, list] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name_labels, _, value = line.rpartition(" ")
        if "{" in name_labels:
            name = name_labels[: name_labels.index("{")]
            labels = name_labels[name_labels.index("{"):]
        else:
            name, labels = name_labels, ""
        out.setdefault(name, []).append((labels, value))
    return out


# ---------------------------------------------------------------------------
# Formatter unit tests
# ---------------------------------------------------------------------------

def test_number_formatting():
    assert mx._fmt_number(5) == "5"
    assert mx._fmt_number(5.0) == "5"          # integral floats have no .0
    assert mx._fmt_number(0.25) == "0.25"
    assert mx._fmt_number(None) is None        # NULLs dropped
    assert mx._fmt_number("not a number") is None
    assert mx._fmt_number(True) == "1"


def test_label_escaping():
    val = mx._escape_label_value('a"b\\c\nd')
    assert val == 'a\\"b\\\\c\\nd'


def test_family_header_emitted_once():
    exp = Exposition()
    exp.family("thing", "help one", "gauge")
    exp.family("thing", "help two", "counter")  # ignored — same name
    exp.sample("thing", 1)
    text = exp.render()
    assert text.count("# TYPE arag_thing") == 1
    assert "help one" in text and "help two" not in text
    assert text.endswith("\n")


def test_null_sample_dropped():
    exp = Exposition()
    exp.family("thing", "h")
    exp.sample("thing", None)
    exp.sample("thing", 3)
    lines = [l for l in exp.render().splitlines() if l == "arag_thing 3"]
    assert lines == ["arag_thing 3"]


# ---------------------------------------------------------------------------
# End-to-end render tests with the fake reader
# ---------------------------------------------------------------------------

def test_render_full_fixture():
    text = render_exposition(make_reader(FULL_FIXTURE))
    parsed = _parse(text)

    # DB probe up
    assert parsed["arag_audit_db_up"][0][1] == "1"
    assert parsed["arag_metrics_collector_failures"][0][1] == "0"

    # latency percentiles present with quantile labels
    lat = dict(parsed["arag_query_latency_milliseconds"])
    assert lat['{quantile="0.95"}'] == "31000"
    assert lat['{quantile="0.5"}'] == "6000"

    # stage latency labelled by stage+quantile
    stage = dict(parsed["arag_stage_latency_milliseconds"])
    assert stage['{stage="narrative",quantile="0.95"}'] == "145000"

    # derived rates: 2/10 error, 3/10 degraded
    assert parsed["arag_query_error_rate"][0][1] == "0.2"
    assert parsed["arag_query_degradation_rate"][0][1] == "0.3"

    # cost passthrough
    assert parsed["arag_query_daily_cost_usd"][0][1] == "6.5"

    # branch mix
    branch = dict(parsed["arag_branch_queries"])
    assert branch['{branch="NARRATIVE_BRANCH",intent="NARRATIVE"}'] == "4"

    # llm + model cost
    assert dict(parsed["arag_llm_calls_total"])['{stage="synthesizer",model="gpt-5-mini"}'] == "20"
    assert dict(parsed["arag_model_cost_usd"])['{model="gpt-5-mini"}'] == "0.04"

    # verifier failures
    assert parsed["arag_verifier_failures_total"][0][1] == "2"

    # ingestion collapses to the most recent day per pipeline (succeeded=5, not 3)
    assert dict(parsed["arag_ingestion_succeeded"])['{pipeline="arelle_xbrl_ingest"}'] == "5"
    assert dict(parsed["arag_ingestion_failed"])['{pipeline="arelle_xbrl_ingest"}'] == "1"


def test_render_headers_wellformed():
    text = render_exposition(make_reader(FULL_FIXTURE))
    # Every metric that has a sample must have exactly one HELP and one TYPE line.
    parsed = _parse(text)
    for metric in parsed:
        assert text.count(f"# HELP {metric} ") == 1, metric
        assert text.count(f"# TYPE {metric} ") == 1, metric


def test_db_down_degrades():
    """Empty reader → audit_db_up 0, no crash, still a parseable body."""
    text = render_exposition(lambda sql: [])
    parsed = _parse(text)
    assert parsed["arag_audit_db_up"][0][1] == "0"
    assert "arag_query_latency_milliseconds" not in parsed  # no rows -> no family
    assert text.endswith("\n")


def test_zero_queries_no_divide_by_zero():
    fixture = {"v_query_daily": [{"queries": 0, "errored": 0, "degraded": 0}]}
    text = render_exposition(make_reader(fixture))
    parsed = _parse(text)
    # rate metrics omitted (guarded), daily count still emitted as 0
    assert "arag_query_error_rate" not in parsed
    assert parsed["arag_query_daily_queries"][0][1] == "0"


def test_reader_exceptions_swallowed():
    """A raising reader is absorbed by _rows -> empty families, no crash."""
    def bad_reader(sql: str):
        if "SELECT 1 AS up" in sql:
            return [{"up": 1}]
        raise RuntimeError("boom")

    text = render_exposition(bad_reader)
    parsed = _parse(text)
    assert parsed["arag_audit_db_up"][0][1] == "1"
    # collectors got [] (exception swallowed in _rows) -> no data families, and
    # the collector-failure counter stays 0 because nothing propagated up.
    assert parsed["arag_metrics_collector_failures"][0][1] == "0"
    assert "arag_query_latency_milliseconds" not in parsed


def test_collector_failure_counted(monkeypatch):
    """A collector that raises past _rows is counted, and render still succeeds."""
    def explode(exp, reader):
        raise RuntimeError("collector bug")

    monkeypatch.setattr(mx, "_COLLECTORS", (explode, mx._collect_query_latency))
    text = render_exposition(make_reader(FULL_FIXTURE))
    parsed = _parse(text)
    assert parsed["arag_metrics_collector_failures"][0][1] == "1"
    # the surviving collector still ran
    assert "arag_query_latency_milliseconds" in parsed


# ---------------------------------------------------------------------------
# FastAPI /metrics route
# ---------------------------------------------------------------------------

def test_metrics_endpoint_content_type_and_body():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from src.api.main import app

    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain; version=0.0.4")
    # arag_audit_db_up is always present regardless of DB state.
    assert "arag_audit_db_up" in resp.text


# ---------------------------------------------------------------------------
# Live DB round-trip (skips when audit views unreachable)
# ---------------------------------------------------------------------------

def _views_available() -> bool:
    try:
        from src.api import db

        # db.query is fail-soft: it returns [] (never None) when the DB is unreachable,
        # so the check must look at the row, not at None.
        rows = db.query("SELECT to_regclass('audit.v_query_latency') IS NOT NULL AS ok")
        return bool(rows and rows[0]["ok"])
    except Exception:
        return False


@pytest.mark.skipif(not _views_available(), reason="audit.* views not reachable")
def test_render_against_live_views():
    from src.api import db

    text = render_exposition(db.query)
    parsed = _parse(text)
    assert parsed["arag_audit_db_up"][0][1] == "1"
    assert parsed["arag_metrics_collector_failures"][0][1] == "0"
    # exposition parses as valid text: every sample line has a numeric value
    for name, samples in parsed.items():
        for _labels, value in samples:
            float(value.replace("+Inf", "inf"))  # raises if unparseable
