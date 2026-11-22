"""C2: value-correct says whether the numbers were right, ignoring citations."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_case


def _vc(grade):
    return getattr(grade, "value_correct", None)


@pytest.mark.criterion("C2")
def test_scalar_right_number_is_value_correct_with_or_without_citation(scalar_case, answer):
    # oracle: requirement 2 of docs/specs/arm-neutral-grading.md — expected 100,
    # produced 100. Cited: CORRECT. Uncited: PARTIAL (citation required), same number.
    case = scalar_case(100.0)
    cited = grade_case(case, answer([100.0]))
    assert cited.grade.value == "CORRECT"
    assert _vc(cited) is True

    bare = grade_case(case, answer([100.0], cited=False))
    assert bare.grade.value == "PARTIAL"
    assert _vc(bare) is True


@pytest.mark.criterion("C2")
def test_wrong_grounding_still_value_correct(scalar_case, answer):
    # oracle: requirement 2 — expected fact 1, cited fact 99: grounding wrong (PARTIAL),
    # but the number 100 equals the expected 100
    case = scalar_case(100.0, expected_fact_ids=[1])
    grade = grade_case(case, answer([100.0], fact_ids=[99]))
    assert grade.grade.value == "PARTIAL"
    assert _vc(grade) is True


@pytest.mark.criterion("C2")
def test_not_value_correct_when_numbers_are_wrong_or_absent(scalar_case, answer):
    # oracle: requirement 2 — 250 vs 100 is wrong; a refusal or hedged miss states no
    # right number; a case with no expected value has nothing to be right about
    case = scalar_case(100.0)
    assert _vc(grade_case(case, answer([250.0]))) is False
    assert _vc(grade_case(case, answer([250.0], hedged=True))) is False
    assert _vc(grade_case(case, answer([], refused=True))) is False
    no_truth = scalar_case(100.0)
    no_truth.expected_value = None
    assert grade_case(no_truth, answer([100.0])).grade.value == "PARTIAL"
    assert _vc(grade_case(no_truth, answer([100.0]))) is False
    assert _vc(grade_case(case, answer([100.0]))) is True


@pytest.mark.criterion("C2")
def test_screen_and_breakdown_value_correct_without_citations(
    screen_case, breakdown_case, answer
):
    # oracle: requirement 2 — the exact expected ranking (AAA 300, BBB 200, CCC 100)
    # and the exact components (60, 40) are right; uncited they grade PARTIAL
    screen = grade_case(
        screen_case(),
        answer(
            rows=[
                {"ticker": "AAA", "value": 300.0},
                {"ticker": "BBB", "value": 200.0},
                {"ticker": "CCC", "value": 100.0},
            ],
            cited=False,
        ),
    )
    assert screen.grade.value == "PARTIAL"
    assert _vc(screen) is True

    breakdown = grade_case(breakdown_case([60.0, 40.0]), answer([60.0, 40.0], cited=False))
    assert breakdown.grade.value == "PARTIAL"
    assert _vc(breakdown) is True

    wrong = grade_case(screen_case(), answer(rows=[{"ticker": "ZZZ", "value": 1.0}]))
    assert _vc(wrong) is False


@pytest.mark.criterion("C2")
@given(
    expected=st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
    right=st.booleans(),
    hedged=st.booleans(),
)
def test_value_correct_is_whether_the_number_matches(
    scalar_case, answer, expected, right, hedged
):
    # oracle: C2's definition — produced equals expected exactly (right) or is 2x off
    # (wrong, far outside rel_tol=1e-4); hedging does not change a right number
    case = scalar_case(expected)
    produced = expected if right else expected * 2.0
    grade = grade_case(case, answer([produced], hedged=hedged))
    assert _vc(grade) is right
    if grade.grade.value == "CORRECT":
        assert _vc(grade) is True


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    expected=st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
    factor=st.sampled_from([1.0, 0.5, 2.0, 1.00001]),
)
def test_stripping_citations_never_changes_value_correct(
    scalar_case, answer, uncited, expected, factor
):
    # oracle: requirement 2 — the measure ignores citations by definition
    # (factor 1.00001 is inside rel_tol=1e-4, so it still matches)
    case = scalar_case(expected)
    ans = answer([expected * factor])
    with_ids = grade_case(case, ans)
    without = grade_case(case, uncited(ans))
    assert _vc(with_ids) is not None
    assert _vc(with_ids) == _vc(without)
