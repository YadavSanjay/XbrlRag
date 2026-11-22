"""C6: the judge summary shows means only for a calibrated judge version."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import CaseGrade, Grade
from src.eval.core.graders.judge import CalibrationReport
from src.eval.core.judge_wiring import judge_summary
from src.eval.core.report import CaseResult, RunReport

VERSION = "v1:claude-opus-5-5"
KEYS = ("correctness", "completeness", "citation_support", "unsupported_claims")


def _report(make_case, scores: dict, version: str | None = VERSION) -> RunReport:
    results = []
    for cid, s in scores.items():
        case = make_case(cid, kind="narrative", expected_text="Ref.", key_points=["p"])
        grade = CaseGrade(case_id=cid, grade=Grade.CORRECT, passed=True, reason="ok",
                          judge_scores=s)
        results.append(CaseResult(case=case, grade=grade))
    config = {"split": "legacy"}
    if version is not None:
        config["judge_version"] = version
    return RunReport(run_id="r", suite="s", results=results, n_planned=len(results),
                     config=config)


def _calibration(version: str, passed: bool) -> CalibrationReport:
    return CalibrationReport(judge_version=version, n=120, kappa=0.8 if passed else 0.5,
                             threshold=0.7, min_items=100, passed=passed)


def _s(c, m, s, u) -> dict:
    return {"correctness": c, "completeness": m, "citation_support": s,
            "unsupported_claims": u}


SCORES = {
    "r1": _s(1.0, 0.5, 1.0, 0),
    "r2": _s(0.5, 1.0, 0.5, 1),
    "r3": _s(0.0, 0.0, 0.0, 2),
    "r4": dict.fromkeys(KEYS),  # judge_error
    "r5": None,  # not judged
}


@pytest.mark.criterion("C6")
def test_calibrated_summary(make_case):
    # oracle: requirement 8, by hand — means over r1..r3: correctness (1+0.5+0)/3 = 0.5,
    # completeness (0.5+1+0)/3 = 0.5, support (1+0.5+0)/3 = 0.5, unsupported (0+1+2)/3 = 1
    summary = judge_summary(_report(make_case, SCORES), [_calibration(VERSION, True)])
    assert summary["judge_version"] == VERSION
    assert summary["reportable"] is True
    assert summary["status"] == "calibrated"
    assert summary["n_judged"] == 4
    assert summary["n_judge_error"] == 1
    assert summary["judge_error_case_ids"] == ["r4"]
    means = summary["means"]
    assert set(means) == set(KEYS)
    # tolerance: means of three small decimals; any correct sum/3 is within 1e-12
    assert means["correctness"] == pytest.approx(0.5, abs=1e-12)
    assert means["completeness"] == pytest.approx(0.5, abs=1e-12)
    assert means["citation_support"] == pytest.approx(0.5, abs=1e-12)
    assert means["unsupported_claims"] == pytest.approx(1.0, abs=1e-12)


@pytest.mark.criterion("C6")
def test_uncalibrated_withholds_means(make_case):
    # oracle: requirement 8 and scores_reportable — the last report for the version decides
    report = _report(make_case, SCORES)
    for calibration in (
        [],
        [_calibration(VERSION, False)],
        [_calibration("v1:gpt-5.6-luna", True)],
        [_calibration(VERSION, True), _calibration(VERSION, False)],
    ):
        summary = judge_summary(report, calibration)
        assert summary["reportable"] is False
        assert summary["status"] == "uncalibrated"
        assert summary["means"] is None
        assert summary["n_judged"] == 4
        assert summary["n_judge_error"] == 1
        assert summary["judge_error_case_ids"] == ["r4"]


@pytest.mark.criterion("C6")
def test_no_judge(make_case):
    # oracle: requirement 8 — no judge version: status none, nothing reportable
    report = _report(make_case, {"r1": None, "r2": None}, version=None)
    summary = judge_summary(report, [_calibration(VERSION, True)])
    assert summary["judge_version"] is None
    assert summary["reportable"] is False
    assert summary["status"] == "none"
    assert summary["n_judged"] == 0
    assert summary["n_judge_error"] == 0
    assert summary["judge_error_case_ids"] == []
    assert summary["means"] is None


_FRACTION = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_ROW = st.one_of(
    st.none(),
    st.just("error"),
    st.tuples(_FRACTION, _FRACTION, _FRACTION, st.integers(min_value=0, max_value=9)),
)


@pytest.mark.criterion("C6")
@given(rows=st.lists(_ROW, min_size=1, max_size=8), passed=st.booleans())
def test_means_follow_the_closed_form(make_case, rows, passed):
    # oracle: requirement 8, evaluated here on the generated scores
    scores = {}
    for i, row in enumerate(rows):
        if row is None:
            scores[f"r{i}"] = None
        elif row == "error":
            scores[f"r{i}"] = dict.fromkeys(KEYS)
        else:
            scores[f"r{i}"] = _s(*row)
    summary = judge_summary(_report(make_case, scores), [_calibration(VERSION, passed)])
    scored = [s for s in scores.values() if s is not None and s["correctness"] is not None]
    errors = sorted(cid for cid, s in scores.items()
                    if s is not None and s["correctness"] is None)
    assert summary["n_judged"] == len(scored) + len(errors)
    assert summary["judge_error_case_ids"] == errors
    assert summary["status"] == ("calibrated" if passed else "uncalibrated")
    if not passed:
        assert summary["means"] is None
        return
    for key in KEYS:
        if not scored:
            assert summary["means"][key] is None
        else:
            expected = sum(s[key] for s in scored) / len(scored)
            # tolerance: a mean of at most eight floats in [0, 9]; summation order may
            # differ, which moves the result by a few ulps
            assert summary["means"][key] == pytest.approx(expected, abs=1e-12)
