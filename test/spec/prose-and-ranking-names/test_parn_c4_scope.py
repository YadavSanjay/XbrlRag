"""C4: names-only applies to the generated shapes' ranking part only; H-X compounds keep their
order, and disclosure checks still apply (requirements 3 and 4).

Each test pairs the unchanged behaviour with a generated compound's reordered right set, which
is right only under requirement 2.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.v2 import grade_v2

REORDERED = ("DEVC", "DEVB", "DEVA")


@pytest.mark.criterion("C4")
def test_hx_compound_keeps_its_order(kit):
    # oracle: requirement 4 — an H-X compound is graded as before: DEVB ahead of DEVA
    # reverses the key's DEVA 300m > DEVB 200m, so it is INCORRECT, not value-correct and,
    # cited, confident-wrong (compound-value-correct C3); the generated compound's reversed
    # right set is right (requirement 2)
    case = kit.hx_compound()
    assert grade_v2(case, kit.hx_answer()).value_correct is True
    for fids in (kit.HX_FACT_IDS, []):
        g = grade_v2(case, kit.hx_answer(reversed_rank=True, fact_ids=fids))
        assert g.grade is Grade.INCORRECT and g.value_correct is False
    assert grade_v2(case, kit.hx_answer(reversed_rank=True)).confident_wrong is True
    rows = kit.revenue_rows(REORDERED)
    gen = grade_case(kit.generated("rank_by_ratio"), kit.gen_answer("rank_by_ratio", rows=rows))
    assert gen.value_correct is True


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("shape", ["rank_by_ratio", "leader_growth"])
def test_disclosure_checks_still_apply(kit, shape):
    # oracle: requirement 3 — a names-only match is graded as a matched screen, whose
    # disclosure checks apply: the cited answer that does not state its universe is not
    # CORRECT and fails ranking.universe, yet is value-correct (a disclosure is not a value)
    rows = kit.revenue_rows(REORDERED)
    g = grade_case(kit.generated(shape), kit.gen_answer(shape, rows=rows, universe=False))
    assert g.grade is not Grade.CORRECT and g.passed is False
    assert g.checks["ranking.universe"] is False
    assert g.value_correct is True


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@settings(max_examples=30)
@given(cited=st.lists(st.sampled_from([1, 2, 34]), unique=True, max_size=3),
       order=st.permutations(["DEVA", "DEVB", "DEVC"]))
def test_hx_reversed_never_right_generated_reordered_always(kit, cited, order):
    # oracle: requirements 2 and 4 — whatever it cites, the H-X compound's reversed ranking
    # is never value-correct; the generated compound's key set in any order always is
    g = grade_v2(kit.hx_compound(), kit.hx_answer(reversed_rank=True, fact_ids=cited))
    assert g.value_correct is False
    rows = kit.revenue_rows(tuple(order))
    gen = grade_case(kit.generated("leader_growth"),
                     kit.gen_answer("leader_growth", rows=rows, cited=bool(cited)))
    assert gen.value_correct is True
