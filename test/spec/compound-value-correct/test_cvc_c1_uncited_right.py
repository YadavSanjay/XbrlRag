"""C1: an uncited compound answer whose every graded part is right is value-correct; nothing
else about its grade changes (requirements 1 and 3)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.v2 import grade_v2


@pytest.mark.criterion("C1")
def test_hx_ranking_and_trend(kit):
    # oracle: fixture keys — DEVA 300m > DEVB 200m with DEVD excluded, DEVA FY2024 250m and
    # FY2025 300m; the answer states exactly these. Cited it is CORRECT (v2-grading req. 14);
    # uncited, requirement 1 makes it value-correct and requirement 3 keeps it PARTIAL.
    case = kit.trend_compound()
    cited = grade_v2(case, kit.hx_answer())
    assert cited.grade is Grade.CORRECT and cited.passed is True
    assert cited.value_correct is True
    uncited = grade_v2(case, kit.hx_answer(fact_ids=[]))
    assert uncited.value_correct is True
    assert uncited.grade is Grade.PARTIAL and uncited.passed is False
    assert uncited.confident_wrong is False


@pytest.mark.criterion("C1")
def test_hx_ranking_with_a_judged_text_part(kit):
    # oracle: compound-text-part requirement 2 — only the ranking is graded; it is right, so
    # the uncited answer is value-correct (requirement 1) and PARTIAL (requirement 3)
    g = grade_v2(kit.text_compound(), kit.hx_answer(trend=None, fact_ids=[]))
    assert g.value_correct is True
    assert g.grade is Grade.PARTIAL and g.passed is False
    assert g.confident_wrong is False


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("shape", ["rank_by_ratio", "leader_growth"])
def test_generated_shapes(kit, shape):
    # oracle: compound-shape C7 — the ranking DEVA 500m, DEVB 400m, DEVC 300m with DEVE
    # excluded, and DEVB's 30.0% operating margin / DEVA's 20.0% net income growth, is the
    # right answer; cited CORRECT, uncited value-correct and PARTIAL
    case = kit.generated(shape)
    cited = grade_case(case, kit.gen_answer(shape))
    assert cited.grade is Grade.CORRECT and cited.value_correct is True
    uncited = grade_case(case, kit.gen_answer(shape, fact_ids=[]))
    assert uncited.value_correct is True
    assert uncited.grade is Grade.PARTIAL and uncited.passed is False
    assert uncited.confident_wrong is False


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(shape=st.sampled_from(["rank_by_ratio", "leader_growth"]),
       cited=st.lists(st.sampled_from([1, 2, 3, 4, 5]), unique=True, max_size=5))
def test_generated_right_answers_are_value_correct_under_any_citation(kit, shape, cited):
    # oracle: requirements 1 and 2 — the right answer is value-correct whatever subset of
    # fact_ids it cites, including none
    g = grade_case(kit.generated(shape), kit.gen_answer(shape, fact_ids=cited))
    assert g.value_correct is True
    assert g.confident_wrong is False
