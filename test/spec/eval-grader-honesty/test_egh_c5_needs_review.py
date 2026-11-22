"""C5: a failing, unhedged breakdown with unexplained amounts is flagged for review."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_breakdown
from src.eval.core.report import Metrics


@pytest.mark.criterion("C5")
def test_flag_is_raised_only_for_unhedged_unexplained_failures(breakdown_case, answer, graded):
    # oracle: requirement 3 of docs/specs/eval-grader-honesty.md, hand-built cases against
    # components 60, 30, 10 (PASS_VALUE_COVERAGE is 0.8, so 0 of 3 matched fails)
    case = breakdown_case([60.0, 30.0, 10.0])

    stray = grade_breakdown(case, answer([45.0, 20.0]))  # 45 unexplained; 20 is not either
    assert stray.grade.value == "INCORRECT"
    assert getattr(stray, "needs_review", None) is True
    assert stray.silent_wrong is False

    hedged = grade_breakdown(case, answer([45.0, 20.0], hedged=True))
    assert getattr(hedged, "needs_review", None) is False

    coarser = grade_breakdown(case, answer([90.0]))  # 60 + 30: a coarser decomposition
    assert coarser.passed is False
    assert getattr(coarser, "needs_review", None) is False

    correct = grade_breakdown(case, answer([60.0, 30.0, 10.0]))
    assert correct.passed is True
    assert getattr(correct, "needs_review", None) is False

    results = [graded(case, answer([45.0, 20.0])), graded(case, answer([90.0]))]
    assert Metrics.compute(results).to_dict().get("n_needs_review") == 1


@pytest.mark.criterion("C5")
@given(
    components=st.lists(st.integers(min_value=10, max_value=10**9), min_size=2, max_size=6),
    excess=st.integers(min_value=1, max_value=10**9),
)
def test_hedging_clears_the_flag_and_silent_wrong_stays_off(
    breakdown_case, answer, components, excess
):
    # oracle: metamorphic — the same failing answer (one amount above the total, which no
    # subset sum can explain) is flagged when asserted and not when hedged; a breakdown is
    # never silent-wrong either way
    expected = [float(c) for c in components]
    case = breakdown_case(expected)
    stray = sum(expected) * 1.01 + float(excess)

    asserted = grade_breakdown(case, answer([stray]))
    assert asserted.passed is False
    assert getattr(asserted, "needs_review", None) is True
    assert asserted.silent_wrong is False

    hedged = grade_breakdown(case, answer([stray], hedged=True))
    assert getattr(hedged, "needs_review", None) is False
    assert hedged.silent_wrong is False
