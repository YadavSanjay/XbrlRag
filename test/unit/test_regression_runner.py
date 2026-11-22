"""Unit tests for WS-2 / B10 — regression matrix runner + gate (no LLM/DB)."""
import pytest

from src.eval.regression.prompt_matrix import Arm
from src.eval.xbrl.golden import GoldenCase
from src.eval.xbrl.regression_runner import (
    _STAGE_PARAM,
    _build_stage,
    regression_gate,
    run_regression,
)


def _resp(value, fact_id=1):
    return {"execution_result": {"steps": [
        {"op": "lookup", "status": "OK", "value": value, "citation": {"fact_id": fact_id}},
    ]}}


CASES = [
    GoldenCase(id="a", query="qa", expected_value=100.0, rel_tol=1e-4),
    GoldenCase(id="b", query="qb", expected_value=200.0, rel_tol=1e-4),
]


class TestRunRegression:
    def test_baseline_all_correct(self):
        canned = {"qa": _resp(100.0), "qb": _resp(200.0)}
        run_fn = lambda arm, q: canned[q]  # noqa: E731
        result = run_regression([Arm(label="baseline")], CASES, run_fn=run_fn)
        summ = {s.arm: s for s in result.summaries}["baseline"]
        assert summ.scores["correct"] == 1.0
        assert summ.scores["cited"] == 1.0

    def test_partial_accuracy_and_multiple_arms(self):
        # baseline gets both right; 'variant' gets qb wrong.
        responses = {
            ("baseline", "qa"): _resp(100.0), ("baseline", "qb"): _resp(200.0),
            ("variant", "qa"): _resp(100.0), ("variant", "qb"): _resp(999.0),
        }
        run_fn = lambda arm, q: responses[(arm.label, q)]  # noqa: E731
        arms = [Arm(label="baseline"), Arm(label="variant")]
        result = run_regression(arms, CASES, run_fn=run_fn)
        summ = {s.arm: s for s in result.summaries}
        assert summ["baseline"].scores["correct"] == 1.0
        assert summ["variant"].scores["correct"] == 0.5

    def test_run_fn_error_recorded_not_raised(self):
        def run_fn(arm, q):
            if q == "qb":
                raise RuntimeError("boom")
            return _resp(100.0)
        result = run_regression([Arm(label="baseline")], CASES, run_fn=run_fn)
        summ = {s.arm: s for s in result.summaries}["baseline"]
        assert summ.errors == 1


class TestRegressionGate:
    def _result(self, correct):
        run_fn = lambda arm, q: _resp(100.0 if (q == "qa" and correct) else (100.0 if correct else 0.0))  # noqa: E731
        # simpler: build responses so accuracy == correct fraction
        responses = {"qa": _resp(100.0), "qb": _resp(200.0 if correct else 0.0)}
        return run_regression([Arm(label="baseline")], CASES, run_fn=lambda a, q: responses[q])

    def test_pass_above_threshold(self):
        result = self._result(correct=True)   # 2/2 = 1.0
        gate = regression_gate(result, "baseline", min_score=0.8)
        assert gate.passed and gate.score == 1.0

    def test_fail_below_threshold(self):
        result = self._result(correct=False)  # 1/2 = 0.5
        gate = regression_gate(result, "baseline", min_score=0.8)
        assert not gate.passed and gate.score == 0.5

    def test_missing_baseline_arm_fails(self):
        result = self._result(correct=True)
        gate = regression_gate(result, "nonexistent", min_score=0.8)
        assert not gate.passed


class TestStageBuilder:
    def test_unsupported_stage_raises(self):
        with pytest.raises(ValueError, match="cannot override stage"):
            _build_stage("intent_router", {})

    def test_supported_stages_map_to_pipeline_params(self):
        assert set(_STAGE_PARAM) == {
            "query_decomposer", "plan_generator", "synthesizer", "faithfulness_verifier"}
        assert _STAGE_PARAM["synthesizer"] == "synthesizer"
