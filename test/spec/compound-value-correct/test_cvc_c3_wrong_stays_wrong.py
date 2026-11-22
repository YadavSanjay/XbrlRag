"""C3: a value-wrong, refused or text-only compound is not value-correct, cited or not; grade
and confident-wrong are as before (requirements 1, 3 and 4).

Each test contrasts the wrong answer with the uncited right answer to the same case, so a fix
that made every uncited compound value-correct fails here just as the unfixed grader does.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.v2 import grade_v2


@pytest.mark.criterion("C3")
def test_reversed_ranking_is_wrong_and_confident_wrong(kit):
    # oracle: fixture key DEVA > DEVB; DEVB first is a wrong ranking — INCORRECT, unrefused
    # and unhedged, so confident-wrong (arm-neutral-grading C1), and never value-correct;
    # the uncited right answer to the same case is value-correct (requirement 1)
    for case, trend in ((kit.trend_compound(), "right"), (kit.text_compound(), None)):
        assert grade_v2(case, kit.hx_answer(trend=trend, fact_ids=[])).value_correct is True
        for fids in (kit.HX_FACT_IDS, []):
            g = grade_v2(case, kit.hx_answer(rank_ok=False, trend=trend, fact_ids=fids))
            assert g.value_correct is False
            assert g.grade is Grade.INCORRECT and g.confident_wrong is True


@pytest.mark.criterion("C3")
def test_wrong_trend_value(kit):
    # oracle: fixture key DEVA FY2024 250m; 330m is wrong, so the trend part is not
    # value-correct and neither is the compound; 250m uncited is value-correct
    case = kit.trend_compound()
    assert grade_v2(case, kit.hx_answer(fact_ids=[])).value_correct is True
    for fids in (kit.HX_FACT_IDS, []):
        g = grade_v2(case, kit.hx_answer(trend="wrong", fact_ids=fids))
        assert g.value_correct is False
        assert g.grade is Grade.INCORRECT


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("shape", ["rank_by_ratio", "leader_growth"])
def test_wrong_generated_dependent(kit, shape):
    # oracle: compound-shape C7 — DEVC at 25.0% is not the highest margin (DEVB at 30.0%
    # is); 35.0% is not DEVA's 20.0% growth; the right sentence uncited is value-correct
    case = kit.generated(shape)
    assert grade_case(case, kit.gen_answer(shape, fact_ids=[])).value_correct is True
    for fids in (kit.GEN_FACT_IDS, []):
        g = grade_case(case, kit.gen_answer(shape, right=False, fact_ids=fids))
        assert g.value_correct is False
        assert g.grade is Grade.INCORRECT


@pytest.mark.criterion("C3")
def test_refusal_and_text_only(kit):
    # oracle: requirement 4 — a text-only compound has no graded part; a refusal states no
    # value; neither is value-correct, while the ranking + text compound answered right and
    # uncited is (requirement 1)
    assert grade_v2(kit.text_compound(),
                    kit.hx_answer(trend=None, fact_ids=[])).value_correct is True
    g = grade_v2(kit.text_only_compound(), kit.hx_answer(trend=None, fact_ids=[]))
    assert g.value_correct is False
    assert g.grade is Grade.PARTIAL
    r = grade_v2(kit.trend_compound(), kit.refusal())
    assert r.value_correct is False
    assert r.grade is Grade.REFUSED and r.confident_wrong is False


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(shape=st.sampled_from(["rank_by_ratio", "leader_growth"]),
       cited=st.lists(st.sampled_from([1, 2, 3, 4, 5]), unique=True, max_size=5))
def test_citations_never_make_a_wrong_answer_right(kit, shape, cited):
    # oracle: requirement 2 — under the same citations, the right answer is value-correct
    # and the wrong one is not; citations change neither
    case = kit.generated(shape)
    assert grade_case(case, kit.gen_answer(shape, fact_ids=cited)).value_correct is True
    g = grade_case(case, kit.gen_answer(shape, right=False, fact_ids=cited))
    assert g.value_correct is False
    assert g.grade is Grade.INCORRECT
