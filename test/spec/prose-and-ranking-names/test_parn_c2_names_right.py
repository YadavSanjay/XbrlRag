"""C2: a generated compound's ranking is right when its rows name the key's top three, in any
order and with any figures (requirements 2 and 3)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("shape", ["rank_by_ratio", "leader_growth"])
def test_rows_holding_the_second_steps_figures(kit, shape):
    # oracle: fixture key — the top three by revenue are DEVA, DEVB and DEVC. The rows name
    # exactly them, ordered by and carrying the second step's figures (margins 30.0 > 26.0 >
    # 22.0; growth 31.0 > 20.0 > 4.0), as the tools agents' answers did. Requirement 2: the
    # ranking part is right; requirement 3: cited CORRECT, uncited PARTIAL and value-correct
    order = {"rank_by_ratio": ("DEVB", "DEVC", "DEVA"),
             "leader_growth": ("DEVB", "DEVA", "DEVC")}[shape]
    rows = kit.step2_rows(shape, order)
    case = kit.generated(shape)
    cited = grade_case(case, kit.gen_answer(shape, rows=rows))
    assert cited.grade is Grade.CORRECT and cited.passed is True
    assert cited.value_correct is True
    assert cited.checks["ranking"] is True
    uncited = grade_case(case, kit.gen_answer(shape, rows=rows, cited=False))
    assert uncited.value_correct is True
    assert uncited.grade is Grade.PARTIAL and uncited.passed is False
    assert uncited.confident_wrong is False


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("shape", ["rank_by_ratio", "leader_growth"])
def test_reordered_revenue_rows(kit, shape):
    # oracle: requirement 2 — DEVC, DEVA, DEVB is the key's set in another order; order is
    # not compared, so the ranking part is right and the cited compound CORRECT
    rows = kit.revenue_rows(("DEVC", "DEVA", "DEVB"))
    g = grade_case(kit.generated(shape), kit.gen_answer(shape, rows=rows))
    assert g.grade is Grade.CORRECT and g.value_correct is True


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(shape=st.sampled_from(["rank_by_ratio", "leader_growth"]),
       order=st.permutations(["DEVA", "DEVB", "DEVC"]),
       values=st.lists(st.floats(min_value=1e3, max_value=1e12, allow_nan=False),
                       min_size=3, max_size=3),
       cited=st.booleans())
def test_any_order_and_any_figures(kit, shape, order, values, cited):
    # oracle: requirements 2 and 3 — the key's three names in any order, with any finite
    # figures (drawn far from the dependent's 30.0 / 20.0, so they cannot match it), give a
    # right ranking part; with the right dependent the compound is value-correct, CORRECT
    # exactly when cited
    rows = [{"entity": t, "value": v} for t, v in zip(order, values, strict=True)]
    g = grade_case(kit.generated(shape), kit.gen_answer(shape, rows=rows, cited=cited))
    assert g.value_correct is True
    assert (g.grade is Grade.CORRECT) is cited
    assert g.confident_wrong is False
