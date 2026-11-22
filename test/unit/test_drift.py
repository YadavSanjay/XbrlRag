"""Unit tests for WS-2 / A4 — drift detection (no LLM/DB)."""
import json
import logging

import pytest

from src.eval.xbrl.drift import (
    Baseline,
    DriftDetector,
    _cited_rate,
    per_tag_accuracy,
)
from src.eval.xbrl.golden import GoldenCase


def _resp(value, fact_id=1):
    return {"execution_result": {"steps": [
        {"op": "lookup", "status": "OK", "value": value, "citation": {"fact_id": fact_id}},
    ]}}


def _uncited(value):
    return {"execution_result": {"steps": [
        {"op": "lookup", "status": "OK", "value": value, "citation": {"fact_id": None}},
    ]}}


CASES = [
    GoldenCase(id="a", query="qa", expected_value=100.0, rel_tol=1e-4, tags=["revenue"]),
    GoldenCase(id="b", query="qb", expected_value=200.0, rel_tol=1e-4, tags=["revenue"]),
    GoldenCase(id="c", query="qc", expected_value=300.0, rel_tol=1e-4, tags=["compute"]),
]


@pytest.fixture
def events():
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)


class TestBaseline:
    def test_roundtrip(self):
        b = Baseline(accuracy=0.923, cited_rate=0.85, per_tag={"revenue": 1.0})
        d = b.to_dict()
        b2 = Baseline.from_dict(d)
        assert b2.accuracy == 0.923 and b2.per_tag["revenue"] == 1.0


class TestHelpers:
    def test_per_tag_accuracy(self):
        from src.eval.xbrl.golden import NumericGrader
        run_fn = lambda q: {"qa": _resp(100.0), "qb": _resp(200.0), "qc": _resp(0.0)}[q]  # noqa: E731
        report = NumericGrader(CASES).grade(run_fn)
        tags = per_tag_accuracy(CASES, report.results)
        assert tags["revenue"] == 1.0     # a, b pass
        assert tags["compute"] == 0.0     # c fails

    def test_cited_rate(self):
        from src.eval.xbrl.golden import NumericGrader
        run_fn = lambda q: {"qa": _resp(100.0), "qb": _uncited(200.0), "qc": _resp(300.0)}[q]  # noqa: E731
        report = NumericGrader(CASES).grade(run_fn)
        assert _cited_rate(report.results) == pytest.approx(2 / 3)


class TestDriftDetector:
    def _baseline(self):
        return Baseline(accuracy=1.0, cited_rate=1.0, per_tag={"revenue": 1.0, "compute": 1.0})

    def test_no_drift_when_stable(self):
        run_fn = lambda q: {"qa": _resp(100.0), "qb": _resp(200.0), "qc": _resp(300.0)}[q]  # noqa: E731
        report = DriftDetector(CASES, self._baseline()).check(run_fn)
        assert not report.drifted and report.current_accuracy == 1.0

    def test_accuracy_drop_triggers_drift(self, events):
        # two of three regress -> accuracy 1/3, drop 0.67 > 0.1 tolerance
        run_fn = lambda q: {"qa": _resp(100.0), "qb": _resp(0.0), "qc": _resp(0.0)}[q]  # noqa: E731
        report = DriftDetector(CASES, self._baseline(), accuracy_tolerance=0.1).check(run_fn)
        assert report.drifted
        assert any("accuracy" in r for r in report.reasons)
        assert any(e["kind"] == "drift_alert" for e in events)

    def test_citation_drop_triggers_drift(self):
        # all values correct but none cited -> cited_rate 0, drop 1.0
        run_fn = lambda q: {"qa": _uncited(100.0), "qb": _uncited(200.0), "qc": _uncited(300.0)}[q]  # noqa: E731
        base = Baseline(accuracy=0.0, cited_rate=1.0)  # accuracy tolerant, citations strict
        report = DriftDetector(CASES, base, accuracy_tolerance=1.0, cited_tolerance=0.1).check(run_fn)
        # uncited answers fail grading (require_citation default True), so accuracy also 0,
        # but the citation-rate reason must be present.
        assert report.drifted
        assert any("citation rate" in r for r in report.reasons)

    def test_small_drop_within_tolerance_is_ok(self):
        # one of three regresses -> accuracy 2/3, drop 0.33; tolerance 0.4 absorbs it
        run_fn = lambda q: {"qa": _resp(100.0), "qb": _resp(200.0), "qc": _resp(0.0)}[q]  # noqa: E731
        report = DriftDetector(CASES, self._baseline(), accuracy_tolerance=0.4).check(run_fn)
        assert not report.drifted
