"""C1: the human majority share and the gate it picks (requirements 1-2)."""

from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import dimension_gate, human_majority_share

T, F = True, False


@pytest.mark.criterion("C1")
def test_majority_share_by_hand():
    # oracle: hand counts - 3 of 4, 5 of 5, 1 of 2, 99 of 100; no labels -> None
    # tolerance: one exact division of small integers
    assert human_majority_share([T, T, T, F]) == pytest.approx(3 / 4, abs=1e-15)
    assert human_majority_share([F] * 5) == pytest.approx(1.0, abs=1e-15)
    assert human_majority_share([T, F]) == pytest.approx(1 / 2, abs=1e-15)
    assert human_majority_share([F] + [T] * 99) == pytest.approx(99 / 100, abs=1e-15)
    assert human_majority_share([]) is None


@pytest.mark.criterion("C1")
def test_gate_at_the_cut_off():
    # oracle: requirement 2 - "agreement" iff the share is >= the cut-off (inclusive);
    # default cut-off 0.99, so 99/100 and 100/100 are one class and 98/100 is not
    assert dimension_gate([T] * 99 + [F]) == "agreement"
    assert dimension_gate([T] * 100) == "agreement"
    assert dimension_gate([F] * 100) == "agreement"
    assert dimension_gate([T] * 98 + [F] * 2) == "kappa"
    assert dimension_gate([F] * 98 + [T] * 2) == "kappa"
    # a passed cut-off: 19/20 = 0.95 is at it, 18/20 below
    assert dimension_gate([T] * 19 + [F], one_class_share=0.95) == "agreement"
    assert dimension_gate([T] * 18 + [F] * 2, one_class_share=0.95) == "kappa"
    # the real E3 shares (spec table): correct 143/150 stays on kappa, supported 103/103
    # takes agreement, covered 109/213 stays on kappa
    assert dimension_gate([T] * 143 + [F] * 7) == "kappa"
    assert dimension_gate([T] * 103) == "agreement"
    assert dimension_gate([T] * 109 + [F] * 104) == "kappa"
    # no labels: nothing to call one class
    assert dimension_gate([]) == "kappa"


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    st.lists(st.booleans(), min_size=0, max_size=120),
    st.sampled_from([0.6, 0.9, 0.95, 0.99, 1.0]),
    st.data(),
)
def test_gate_closed_form_and_invariance(human, cut, data):
    # oracle: requirements 1-2 closed form in exact fractions; the gate depends on the
    # label counts only, so permuting or flipping every label leaves it unchanged
    n = len(human)
    if n == 0:
        expected_share, expected_gate = None, "kappa"
    else:
        share = Fraction(max(sum(human), n - sum(human)), n)
        expected_share = share
        expected_gate = "agreement" if share >= Fraction(str(cut)) else "kappa"
    got = human_majority_share(human)
    if expected_share is None:
        assert got is None
    else:
        # tolerance: one division of integers below 121
        assert got == pytest.approx(float(expected_share), abs=1e-15)
    assert dimension_gate(human, one_class_share=cut) == expected_gate
    shuffled = data.draw(st.permutations(human))
    assert dimension_gate(shuffled, one_class_share=cut) == expected_gate
    assert dimension_gate([not h for h in human], one_class_share=cut) == expected_gate
