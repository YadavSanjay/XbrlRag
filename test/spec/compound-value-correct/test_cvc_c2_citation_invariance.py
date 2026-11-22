"""C2: a compound's value_correct is (ranking rows right) and (dependent values right), and
removing fact_ids never changes it (requirements 1 and 2)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import grade_v2

FACT_SUBSETS = st.lists(st.sampled_from([1, 2, 34]), unique=True, max_size=3)


@pytest.mark.criterion("C2")
def test_silent_ranking_is_value_correct_cited_or_not(kit):
    # oracle: arm-neutral-grading C2 — a screen whose rows are right but which does not state
    # its universe or exclusions is value-correct; requirement 1 carries that to the part, so
    # the compound with a right trend is value-correct, cited or not
    case = kit.trend_compound()
    for fids in (kit.HX_FACT_IDS, []):
        g = grade_v2(case, kit.hx_answer(disclosed=False, fact_ids=fids))
        assert g.value_correct is True, fids


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(rank_ok=st.booleans(), disclosed=st.booleans(), trend_ok=st.booleans(),
       cited=FACT_SUBSETS)
def test_value_correct_is_the_parts_and_ignores_citations(kit, rank_ok, disclosed, trend_ok,
                                                           cited):
    # oracle: requirement 1 by hand — the ranking part is value-correct iff its rows are in
    # the right order (disclosure aside), the trend part iff both years' values are right;
    # requirement 2 — the same answer with no fact_ids grades the same
    case = kit.trend_compound()
    trend = "right" if trend_ok else "wrong"
    expected = rank_ok and trend_ok
    with_ids = grade_v2(case, kit.hx_answer(rank_ok, disclosed, trend, cited))
    without = grade_v2(case, kit.hx_answer(rank_ok, disclosed, trend, []))
    assert with_ids.value_correct is expected
    assert without.value_correct is expected
