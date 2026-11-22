"""C1: confident-wrong is a wrong, undeclined answer, whatever it cites."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_case


def _cw(grade):
    return getattr(grade, "confident_wrong", None)


@pytest.mark.criterion("C1")
def test_wrong_scalar_is_confident_wrong_cited_or_not(scalar_case, answer, uncited):
    # oracle: requirement 1 of docs/specs/arm-neutral-grading.md, hand-built pairs:
    # expected 100; 250 is 150% off, far outside rel_tol=1e-4
    case = scalar_case(100.0)
    cited = grade_case(case, answer([250.0]))
    assert cited.grade.value == "INCORRECT"
    assert _cw(cited) is True
    assert cited.silent_wrong is True

    bare = grade_case(case, uncited(answer([250.0])))
    assert bare.grade.value == "INCORRECT"
    assert _cw(bare) is True
    assert bare.silent_wrong is False  # the gap this criterion closes


@pytest.mark.criterion("C1")
def test_declines_and_right_answers_are_not_confident_wrong(scalar_case, answer):
    # oracle: requirement 1 — HEDGED_MISS and REFUSED assert nothing false; a matching
    # value (100 vs expected 100) is right, cited or not
    case = scalar_case(100.0)
    hedged = grade_case(case, answer([250.0], hedged=True))
    assert hedged.grade.value == "HEDGED_MISS"
    assert _cw(hedged) is False
    assert _cw(grade_case(case, answer([], refused=True))) is False
    assert _cw(grade_case(case, answer([100.0]))) is False
    assert _cw(grade_case(case, answer([100.0], cited=False))) is False


@pytest.mark.criterion("C1")
def test_breakdown_relevance_miss_exempt_but_unexplained_amount_counts(breakdown_case, answer):
    # oracle: requirement 1 — expected components 60 and 40 (total 100, hand sums).
    # Answering only the total 100 is a different decomposition (explained by 60+40);
    # asserting 999 alongside 60 is an amount no component or subset sums to.
    case = breakdown_case([60.0, 40.0])
    total_only = grade_case(case, answer([100.0]))
    assert total_only.passed is False
    assert total_only.needs_review is False
    assert _cw(total_only) is False

    false_part = grade_case(case, answer([60.0, 999.0]))
    assert false_part.passed is False
    assert false_part.needs_review is True
    assert _cw(false_part) is True


@pytest.mark.criterion("C1")
def test_wrong_screen_ranking_is_confident_wrong(screen_case, answer):
    # oracle: requirement 1 — expected top filer AAA; an answer ranking ZZZ first fails
    # the top-1 check, so the ranking asserted is wrong
    case = screen_case()
    rows = [{"ticker": "ZZZ", "value": 5.0}, {"ticker": "YYY", "value": 4.0}]
    wrong = grade_case(case, answer(rows=rows, cited=False))
    assert wrong.grade.value == "INCORRECT"
    assert _cw(wrong) is True


@pytest.mark.criterion("C1")
@given(
    expected=st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
    factor=st.one_of(
        st.floats(min_value=0.01, max_value=0.99),
        st.floats(min_value=1.01, max_value=100.0),
    ),
    hedged=st.booleans(),
    refused=st.booleans(),
    cited=st.booleans(),
)
def test_confident_wrong_matches_its_definition(
    scalar_case, answer, expected, factor, hedged, refused, cited
):
    # oracle: C1's definition. A factor at least 1% away from 1 is far outside
    # rel_tol=1e-4, so the value never matches; confident-wrong then depends only on the
    # decline flags, never on citations.
    case = scalar_case(expected)
    grade = grade_case(
        case, answer([expected * factor], hedged=hedged, refused=refused, cited=cited)
    )
    assert _cw(grade) is (not hedged and not refused)
    # silent_wrong ignores `refused` (it stays as it was), so the implication holds
    # only for answers that weren't refused
    if grade.silent_wrong and not refused:
        assert _cw(grade) is True


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    expected=st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
    factor=st.floats(min_value=0.0, max_value=3.0),
    hedged=st.booleans(),
)
def test_removing_citations_never_changes_confident_wrong(
    scalar_case, answer, uncited, expected, factor, hedged
):
    # oracle: requirement 1 — the metric is arm-neutral, so citations cannot move it
    case = scalar_case(expected)
    ans = answer([expected * factor], hedged=hedged)
    with_ids = grade_case(case, ans)
    without = grade_case(case, uncited(ans))
    assert _cw(with_ids) is not None
    assert _cw(with_ids) == _cw(without)
