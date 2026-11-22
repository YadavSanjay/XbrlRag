"""C2: one dimension's pass under its gate (requirement 3)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import DimensionAgreement, dimension_passed


def _agreement(n: int, p_o: float | None, kappa: float | None) -> DimensionAgreement:
    # p_e and the interval are never read by the gate; any consistent filler will do
    return DimensionAgreement(n=n, p_o=p_o, p_e=0.5, kappa=kappa, ci_low=None, ci_high=None)


def _passed(agreement, gate, *, threshold=0.7, bound=0.95, min_items=20):
    return dimension_passed(agreement, gate, threshold=threshold, agreement_bound=bound,
                            min_items=min_items)


@pytest.mark.criterion("C2")
def test_agreement_gate_by_hand():
    # oracle: requirement 3 - agreement gate: n >= min_items and p_o >= bound (inclusive);
    # p_o = 19/20 is exactly 0.95, 18/20 is below it
    assert _passed(_agreement(20, 19 / 20, 0.0), "agreement") is True
    assert _passed(_agreement(20, 18 / 20, 0.9), "agreement") is False
    # one item below the floor fails even at perfect agreement
    assert _passed(_agreement(19, 1.0, None), "agreement") is False
    assert _passed(_agreement(20, 1.0, None), "agreement") is True
    # kappa is not read by the agreement gate: None and 0 both pass on p_o
    assert _passed(_agreement(103, 98 / 103, None), "agreement") is True
    assert _passed(_agreement(103, 97 / 103, 0.0), "agreement") is False
    # no items: p_o None fails
    assert _passed(_agreement(0, None, None), "agreement", min_items=0) is False


@pytest.mark.criterion("C2")
def test_kappa_gate_by_hand():
    # oracle: requirement 3 - kappa gate: n >= min_items, kappa defined, kappa >= threshold
    assert _passed(_agreement(20, 0.85, 0.7), "kappa") is True
    assert _passed(_agreement(20, 0.85, 0.69), "kappa") is False
    assert _passed(_agreement(20, 1.0, None), "kappa") is False
    assert _passed(_agreement(19, 0.95, 0.9), "kappa") is False
    # p_o is not read by the kappa gate: a high p_o with kappa 0 fails (D1's case)
    assert _passed(_agreement(20, 19 / 20, 0.0), "kappa") is False
    assert _passed(_agreement(20, 0.5, 0.8), "kappa") is True
    with pytest.raises(ValueError):
        _passed(_agreement(20, 1.0, 1.0), "pabak")


@pytest.mark.criterion("C2")
@given(
    st.integers(0, 200),
    st.integers(0, 200),
    st.one_of(st.none(), st.integers(0, 40)),
    st.one_of(st.none(), st.integers(-40, 40)),
    st.sampled_from(["kappa", "agreement"]),
    st.sampled_from([0.0, 0.7, 0.8]),
    st.sampled_from([0.0, 0.9, 0.95, 1.0]),
)
def test_closed_form(n, min_items, p_o_40, kappa_40, gate, threshold, bound):
    # oracle: requirement 3 closed form; fields drawn on a grid of 1/40 so the comparisons
    # are exact
    p_o = None if p_o_40 is None else p_o_40 / 40
    kappa = None if kappa_40 is None else kappa_40 / 40
    if gate == "kappa":
        expected = n >= min_items and kappa is not None and kappa >= threshold
    else:
        expected = n >= min_items and p_o is not None and p_o >= bound
    got = dimension_passed(_agreement(n, p_o, kappa), gate, threshold=threshold,
                           agreement_bound=bound, min_items=min_items)
    assert got is expected
