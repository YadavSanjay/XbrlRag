"""Unit tests for the WS-2 / A3 numeric-tolerance grader + golden set.

Pure logic only (no DB, no LLM): tolerance comparison, structured-value extraction
from a response, per-case grading, the prompt_matrix scorer adapter, and the
integrity of the committed golden-set file.
"""
import os

import pytest

from src.eval.xbrl.golden import (
    DEFAULT_GOLDEN_PATH,
    CaseResult,
    GoldenCase,
    GradeReport,
    NumericGrader,
    grade_response,
    load_golden_set,
    numeric_tolerance_scorer,
)
from src.eval.xbrl.numeric import (
    all_numeric_values,
    close_enough,
    compute_step_values,
    has_fact_citation,
    lookup_step_values,
)


def _resp(steps):
    """A minimal XbrlRagResponse-shaped dict (the extractors accept object or dict)."""
    return {"execution_result": {"steps": steps}}


def _lookup(value, fact_id=123, status="OK"):
    return {"op": "lookup", "status": status, "value": value, "citation": {"fact_id": fact_id}}


def _compute(value, status="OK", component_fact_ids=None):
    return {"op": "compute", "status": status, "value": value,
            "citation": {"fact_id": None}, "component_fact_ids": component_fact_ids or []}


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------

class TestCloseEnough:
    def test_near_exact(self):
        assert close_enough(215938000000.0, 215938000000.0)
        assert close_enough(100.0, 100.00001, rel=1e-4)
        assert not close_enough(100.0, 101.0, rel=1e-4)

    def test_zero_expected_uses_absolute_band(self):
        assert close_enough(0.4, 0.0)
        assert not close_enough(2.0, 0.0)

    def test_looser_tolerance_for_computes(self):
        assert close_enough(0.6547, 0.6547353, rel=1e-2)


# ---------------------------------------------------------------------------
# Structured extraction
# ---------------------------------------------------------------------------

class TestExtraction:
    def test_lookup_and_compute_values(self):
        resp = _resp([_lookup(215938000000.0), _compute(0.65, component_fact_ids=[1, 2])])
        assert lookup_step_values(resp) == [215938000000.0]
        assert compute_step_values(resp) == [0.65]
        assert set(all_numeric_values(resp)) == {215938000000.0, 0.65}

    def test_excludes_non_ok_and_non_numeric(self):
        resp = _resp([
            _lookup(1.0, status="AMBIGUOUS"),
            _lookup(None),
            {"op": "lookup", "status": "OK", "value": "x", "citation": {}},
        ])
        assert all_numeric_values(resp) == []

    def test_accepts_object_and_dict(self):
        class _Obj:
            execution_result = {"steps": [_lookup(5.0)]}
        assert all_numeric_values(_Obj()) == [5.0]
        assert all_numeric_values(_resp([_lookup(5.0)])) == [5.0]

    def test_citation_from_lookup(self):
        assert has_fact_citation(_resp([_lookup(5.0, fact_id=99)]))

    def test_citation_from_compute_components(self):
        assert has_fact_citation(_resp([_compute(0.5, component_fact_ids=[7])]))

    def test_no_citation(self):
        assert not has_fact_citation(_resp([_lookup(5.0, fact_id=None)]))


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

class TestGradeResponse:
    def test_pass_within_tolerance_and_cited(self):
        case = GoldenCase(id="c", query="q", expected_value=215938000000.0, rel_tol=1e-4)
        r = grade_response(case, _resp([_lookup(215938000000.0)]))
        assert r.passed and r.matched_value == 215938000000.0

    def test_fail_out_of_tolerance(self):
        case = GoldenCase(id="c", query="q", expected_value=215938000000.0, rel_tol=1e-4)
        r = grade_response(case, _resp([_lookup(200000000000.0)]))
        assert not r.passed and r.matched_value is None
        assert "no produced value" in r.reason

    def test_fail_when_matched_but_uncited(self):
        case = GoldenCase(id="c", query="q", expected_value=5.0, rel_tol=1e-4, require_citation=True)
        r = grade_response(case, _resp([_lookup(5.0, fact_id=None)]))
        assert not r.passed and r.matched_value == 5.0
        assert "no fact_id citation" in r.reason

    def test_citation_not_required(self):
        case = GoldenCase(id="c", query="q", expected_value=5.0, require_citation=False)
        r = grade_response(case, _resp([_lookup(5.0, fact_id=None)]))
        assert r.passed

    def test_alt_value_form_matches(self):
        # A compute answer returned as a percentage (65.47) matches the fraction
        # expected value (0.6547) via alt_values.
        case = GoldenCase(id="c", query="q", expected_value=0.6547353, rel_tol=1e-2,
                          alt_values=[65.47353])
        r = grade_response(case, _resp([_compute(65.47, component_fact_ids=[1])]))
        assert r.passed


class TestNumericGrader:
    def test_grade_over_run_fn(self):
        cases = [
            GoldenCase(id="a", query="qa", expected_value=100.0, rel_tol=1e-4),
            GoldenCase(id="b", query="qb", expected_value=200.0, rel_tol=1e-4),
        ]
        canned = {"qa": _resp([_lookup(100.0)]), "qb": _resp([_lookup(999.0)])}
        report = NumericGrader(cases).grade(lambda q: canned[q])
        assert report.total == 2 and report.passed == 1
        assert report.accuracy == 0.5

    def test_run_fn_exception_is_a_failed_case(self):
        cases = [GoldenCase(id="a", query="qa", expected_value=1.0)]

        def _boom(_q):
            raise RuntimeError("pipeline down")

        report = NumericGrader(cases).grade(_boom)
        assert report.total == 1 and report.passed == 0
        assert report.results[0].error and "pipeline down" in report.results[0].error


class TestScorerAdapter:
    def test_scorer_shape_and_unknown_query(self):
        cases = [GoldenCase(id="a", query="qa", expected_value=100.0, rel_tol=1e-4)]
        scorer = numeric_tolerance_scorer(cases)
        assert scorer("qa", _resp([_lookup(100.0)])) == {"correct": 1.0, "cited": 1.0}
        assert scorer("qa", _resp([_lookup(0.0)])) == {"correct": 0.0, "cited": 1.0}
        assert scorer("unknown query", _resp([_lookup(100.0)])) == {}


# ---------------------------------------------------------------------------
# Committed golden set integrity
# ---------------------------------------------------------------------------

class TestGoldenSetFile:
    def test_committed_golden_set_loads_and_is_wellformed(self):
        assert os.path.exists(DEFAULT_GOLDEN_PATH), DEFAULT_GOLDEN_PATH
        cases = load_golden_set(DEFAULT_GOLDEN_PATH)
        assert len(cases) >= 10
        ids = [c.id for c in cases]
        assert len(ids) == len(set(ids)), "case ids must be unique"
        for c in cases:
            assert c.query and c.expected_value is not None
            assert c.rel_tol > 0
            assert c.kind in ("lookup", "compute", "dimensional")
        # sanity: a couple of known headline values are present
        by_id = {c.id: c for c in cases}
        assert by_id["nvda-rev-2026"].expected_value == pytest.approx(215938000000.0)
