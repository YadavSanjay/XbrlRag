"""C2: a hedged answer with numbers but no match is HEDGED_MISS, not INCORRECT."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_scalar


@pytest.mark.criterion("C2")
def test_hedged_non_match_is_hedged_miss_and_the_rest_of_the_ladder_holds(scalar_case, answer):
    # oracle: requirement 2 of docs/specs/eval-grader-honesty.md, hand-built pairs:
    # expected 100; 250 is 150% off, far outside rel_tol=1e-4
    case = scalar_case(100.0)

    unhedged = grade_scalar(case, answer([250.0]))
    assert unhedged.grade.value == "INCORRECT"
    assert unhedged.silent_wrong is True

    hedged = grade_scalar(case, answer([250.0], hedged=True))
    assert hedged.grade.value == "HEDGED_MISS"
    assert hedged.passed is False
    assert hedged.silent_wrong is False

    assert grade_scalar(case, answer([100.0], hedged=True)).grade.value == "CORRECT"
    assert grade_scalar(case, answer([], hedged=True)).grade.value == "REFUSED"
    assert grade_scalar(case, answer([], refused=True)).grade.value == "REFUSED"


@pytest.mark.criterion("C2")
@given(
    expected=st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
    factor=st.one_of(
        st.floats(min_value=0.01, max_value=0.99),
        st.floats(min_value=1.01, max_value=100.0),
    ),
)
def test_hedging_flips_incorrect_to_hedged_miss(scalar_case, answer, expected, factor):
    # oracle: metamorphic — the same wrong number (at least 1% off, 100x the 1e-4
    # tolerance) is INCORRECT when asserted and HEDGED_MISS when hedged; nothing else
    # about the answer changes the verdict
    case = scalar_case(expected)
    produced = [expected * factor]

    assert grade_scalar(case, answer(produced)).grade.value == "INCORRECT"
    hedged = grade_scalar(case, answer(produced, hedged=True))
    assert hedged.grade.value == "HEDGED_MISS"
    assert hedged.silent_wrong is False
