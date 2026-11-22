"""C3: peer-relative grading with a count phrase (docs/specs/peer-rank-count-phrases.md,
requirement 3, and peer-rank-grading requirement 4)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2


@pytest.mark.criterion("C3")
def test_count_then_right_rank_is_right(kit):
    # oracle: the key's rank is 17 and the first stated rank, the count dropped, is 17
    g = grade_v2(kit.mcd_case(), kit.answer(kit.MCD_TEXT, fact_ids=[1]))
    assert g.checks["rank"] is True
    assert g.grade is not Grade.INCORRECT


@pytest.mark.criterion("C3")
def test_count_then_wrong_rank_is_wrong(kit):
    # oracle: the first stated rank is 16, not the key's 17; the count 32 is never read
    text = kit.MCD_TEXT.replace("17th", "16th")
    g = grade_v2(kit.mcd_case(), kit.answer(text, fact_ids=[1]))
    assert g.checks["rank"] is False
    assert g.grade is Grade.INCORRECT
    assert "16" in g.reason
    assert "32" not in g.reason


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(stated=st.integers(min_value=1, max_value=33),
       n=st.integers(min_value=1, max_value=999),
       subject=st.sampled_from(["I", "We", "We have"]))
def test_count_prefix_never_changes_the_grade(kit, stated, n, subject):
    # oracle: requirement 3 — the prefix adds no rank, so the grade and checks are those of
    # the text without it; the rank check is right exactly when the stated rank is 17
    rest = f"McDonald's is No. {stated} of the 33 covered companies."
    prefix = f"{subject} ranked {n} of the 33 covered companies. "
    case = kit.mcd_case()
    with_prefix = grade_v2(case, kit.answer(prefix + rest, fact_ids=[1]))
    without = grade_v2(case, kit.answer(rest, fact_ids=[1]))
    assert with_prefix.grade is without.grade
    assert with_prefix.checks == without.checks
    assert with_prefix.checks["rank"] is (stated == 17)
