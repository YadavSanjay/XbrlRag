"""C8: calibration against human labels, and the reporting gate."""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.eval.core.graders.judge import calibration_report, cohen_kappa, scores_reportable

V1 = "v1:claude-opus-5-5"
V2 = "v2:claude-opus-5-5"


def _from_table(yy: int, yn: int, ny: int, nn: int) -> tuple[list[str], list[str]]:
    a = ["yes"] * (yy + yn) + ["no"] * (ny + nn)
    b = ["yes"] * yy + ["no"] * yn + ["yes"] * ny + ["no"] * nn
    return a, b


@pytest.mark.criterion("C8")
def test_worked_example_fails_the_threshold():
    # oracle: the Wikipedia worked example doubled (40, 10, 20, 30 of 100) keeps the same
    # proportions, so kappa = 0.4 < 0.7
    judge, human = _from_table(40, 10, 20, 30)
    report = calibration_report(judge, human, judge_version=V1)
    assert report.judge_version == V1
    assert report.n == 100
    # tolerance: float divisions of exact ratios; well within 1e-12
    assert report.kappa == pytest.approx(0.4, abs=1e-12)
    assert report.threshold == pytest.approx(0.7, abs=0.0)  # tolerance: the default, exact
    assert report.min_items == 100
    assert report.passed is False


@pytest.mark.criterion("C8")
def test_pass_needs_enough_items_and_agreement():
    # oracle: requirement 7 — passed = n >= min_items and kappa >= threshold (inclusive)
    labels = ["yes", "no"] * 50
    full = calibration_report(labels, list(labels), judge_version=V1)
    assert full.n == 100 and full.passed is True
    # tolerance: perfect agreement is exactly 1 up to one division
    assert full.kappa == pytest.approx(1.0, abs=1e-12)

    half = labels[:50]
    assert calibration_report(half, list(half), judge_version=V1).passed is False
    assert calibration_report(half, list(half), judge_version=V1, min_items=50).passed is True

    # kappa exactly 0: p_o = 0.5 = p_e
    a = [1, 1, 0, 0] * 25
    b = [1, 0, 1, 0] * 25
    assert calibration_report(a, b, judge_version=V1, threshold=0.0).passed is True
    assert calibration_report(a, b, judge_version=V1, threshold=0.01).passed is False


@pytest.mark.criterion("C8")
def test_gate_follows_the_last_report_for_the_version():
    # oracle: requirement 7's gate
    labels = ["yes", "no"] * 50
    good_v1 = calibration_report(labels, list(labels), judge_version=V1)
    bad_v1 = calibration_report(*_from_table(40, 10, 20, 30), judge_version=V1)
    good_v2 = calibration_report(labels, list(labels), judge_version=V2)

    assert scores_reportable(V1, []) is False
    assert scores_reportable(V1, [good_v2]) is False
    assert scores_reportable(V1, [good_v1]) is True
    assert scores_reportable(V1, [good_v1, bad_v1]) is False
    assert scores_reportable(V1, [bad_v1, good_v1]) is True
    assert scores_reportable(V1, [good_v1, good_v2]) is True
    assert scores_reportable(V2, [good_v2, bad_v1]) is True


_LABELS = st.sampled_from(["yes", "no"])


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(
    pairs=st.lists(st.tuples(_LABELS, _LABELS), min_size=1, max_size=60),
    threshold=st.floats(min_value=-1.0, max_value=1.0, allow_nan=False),
    min_items=st.integers(min_value=1, max_value=80),
)
def test_report_matches_its_definition(pairs, threshold, min_items):
    # oracle: requirement 7's definition of passed; kappa is cohen_kappa's, and swapping
    # the raters changes neither (kappa is symmetric)
    judge = [p[0] for p in pairs]
    human = [p[1] for p in pairs]
    assume(not (len(set(judge)) == 1 and set(judge) == set(human)))
    report = calibration_report(
        judge, human, judge_version=V1, threshold=threshold, min_items=min_items
    )
    swapped = calibration_report(
        human, judge, judge_version=V1, threshold=threshold, min_items=min_items
    )
    kappa = cohen_kappa(judge, human)
    # tolerance: the same computation; identical up to summation order
    assert report.kappa == pytest.approx(kappa, abs=1e-12)
    assert report.n == len(pairs)
    assert report.passed is (len(pairs) >= min_items and report.kappa >= threshold)
    assert swapped.passed is report.passed
    assert swapped.kappa == pytest.approx(report.kappa, abs=1e-12)
    assert scores_reportable(V1, [report]) is report.passed
