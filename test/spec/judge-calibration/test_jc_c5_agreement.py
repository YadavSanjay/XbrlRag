"""C5: per-dimension agreement - Cohen's kappa and its approximate interval."""

from __future__ import annotations

import math
from fractions import Fraction

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import dimension_agreement


def _table(tt: int, tf: int, ft: int, ff: int) -> tuple[list[bool], list[bool]]:
    """Judge and human labels from a 2x2 table (judge's label first)."""
    judge = [True] * (tt + tf) + [False] * (ft + ff)
    human = [True] * tt + [False] * tf + [True] * ft + [False] * ff
    return judge, human


def _closed_form(judge: list[bool], human: list[bool]):
    """p_o, p_e, kappa (None when p_e == 1) and the Cohen (1960) interval, by hand."""
    n = len(judge)
    p_o = Fraction(sum(a == b for a, b in zip(judge, human, strict=True)), n)
    pj, ph = Fraction(sum(judge), n), Fraction(sum(human), n)
    p_e = pj * ph + (1 - pj) * (1 - ph)
    if p_e == 1:
        return float(p_o), float(p_e), None, None, None
    kappa = float((p_o - p_e) / (1 - p_e))
    se = math.sqrt(float(p_o * (1 - p_o)) / (n * float(1 - p_e) ** 2))
    return float(p_o), float(p_e), kappa, max(-1.0, kappa - 1.96 * se), min(1.0, kappa + 1.96 * se)


@pytest.mark.criterion("C5")
def test_wikipedia_worked_example():
    # oracle: Wikipedia, "Cohen's kappa" - worked example (20 yes/yes, 5 yes/no, 10 no/yes,
    # 15 no/no): p_o = 0.7, p_e = 0.5*0.6 + 0.5*0.4 = 0.5, kappa = 0.4; its standard error
    # sqrt(p_o(1-p_o)/(N(1-p_e)^2)) = sqrt(0.21/12.5), interval kappa +/- 1.96 se
    got = dimension_agreement(*_table(20, 5, 10, 15))
    se = math.sqrt(0.21 / 12.5)
    assert got.n == 50
    # tolerance: a handful of float operations on exact ratios
    assert got.p_o == pytest.approx(0.7, abs=1e-12)
    assert got.p_e == pytest.approx(0.5, abs=1e-12)
    assert got.kappa == pytest.approx(0.4, abs=1e-12)
    assert got.ci_low == pytest.approx(0.4 - 1.96 * se, abs=1e-12)
    assert got.ci_high == pytest.approx(0.4 + 1.96 * se, abs=1e-12)


@pytest.mark.criterion("C5")
def test_hand_computed_tables():
    # oracle: hand calculation - (4, 1, 1, 4): n = 10, p_o = 0.8, p_e = 0.5, kappa = 0.6,
    # se = sqrt(0.16/2.5) = sqrt(0.064); upper end 0.6 + 1.96*0.253 > 1 is clipped to 1
    got = dimension_agreement(*_table(4, 1, 1, 4))
    se = math.sqrt(0.064)
    # tolerance: a handful of float operations on exact ratios
    assert got.kappa == pytest.approx(0.6, abs=1e-12)
    assert got.ci_low == pytest.approx(0.6 - 1.96 * se, abs=1e-12)
    assert got.ci_high == pytest.approx(1.0, abs=1e-12)
    # identical labels using both values: p_o = 1, se = 0, kappa = 1, interval (1, 1)
    same = dimension_agreement([True, False, True], [True, False, True])
    assert (same.kappa, same.ci_low, same.ci_high) == pytest.approx((1.0, 1.0, 1.0), abs=1e-12)
    # one shared constant label: p_e = 1, kappa undefined
    const = dimension_agreement([True] * 4, [True] * 4)
    assert const.n == 4
    assert const.kappa is None and const.ci_low is None and const.ci_high is None
    assert const.p_e == pytest.approx(1.0, abs=1e-12)


@pytest.mark.criterion("C5")
def test_empty_and_mismatch():
    # oracle: requirement 8
    empty = dimension_agreement([], [])
    assert empty.n == 0
    assert (empty.p_o, empty.p_e, empty.kappa, empty.ci_low, empty.ci_high) == (None,) * 5
    with pytest.raises(ValueError):
        dimension_agreement([True], [True, False])


_PAIRS = st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=60)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(_PAIRS, st.data())
def test_closed_form_and_invariances(pairs, data):
    # oracle: requirement 8 closed form computed in the test; kappa depends only on the
    # joint table, symmetrically, and not on which boolean means "yes"
    judge, human = [p[0] for p in pairs], [p[1] for p in pairs]
    p_o, p_e, kappa, lo, hi = _closed_form(judge, human)
    assume(kappa is not None)
    got = dimension_agreement(judge, human)
    # tolerance: the same exact table, different float summation order
    assert got.n == len(pairs)
    assert got.p_o == pytest.approx(p_o, abs=1e-9)
    assert got.p_e == pytest.approx(p_e, abs=1e-9)
    assert got.kappa == pytest.approx(kappa, abs=1e-9)
    assert got.ci_low == pytest.approx(lo, abs=1e-9)
    assert got.ci_high == pytest.approx(hi, abs=1e-9)
    assert -1.0 <= got.ci_low <= got.kappa + 1e-12
    assert got.kappa <= got.ci_high + 1e-12 <= 1.0 + 2e-12
    order = data.draw(st.permutations(list(range(len(pairs)))))
    permuted = dimension_agreement([judge[i] for i in order], [human[i] for i in order])
    assert permuted.kappa == pytest.approx(kappa, abs=1e-9)
    assert dimension_agreement(human, judge).kappa == pytest.approx(kappa, abs=1e-9)
    flipped = dimension_agreement([not x for x in judge], [not x for x in human])
    assert flipped.kappa == pytest.approx(kappa, abs=1e-9)
