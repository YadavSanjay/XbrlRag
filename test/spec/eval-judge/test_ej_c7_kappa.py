"""C7: Cohen's kappa."""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.eval.core.graders.judge import cohen_kappa


def _from_table(yy: int, yn: int, ny: int, nn: int) -> tuple[list[str], list[str]]:
    """Two raters' labels from a 2x2 table (rater A's label first)."""
    a = ["yes"] * (yy + yn) + ["no"] * (ny + nn)
    b = ["yes"] * yy + ["no"] * yn + ["yes"] * ny + ["no"] * nn
    return a, b


@pytest.mark.criterion("C7")
def test_wikipedia_worked_example():
    # oracle: Wikipedia, "Cohen's kappa", worked example — 50 items, 20 yes/yes, 5 yes/no,
    # 10 no/yes, 15 no/no: p_o = 0.7, p_e = 0.5*0.6 + 0.5*0.4 = 0.5, kappa = 0.4
    a, b = _from_table(20, 5, 10, 15)
    # tolerance: a few float divisions of exact ratios; well within 1e-12
    assert cohen_kappa(a, b) == pytest.approx(0.4, abs=1e-12)


@pytest.mark.criterion("C7")
def test_hand_computed_examples():
    # oracle: hand calculations
    # 2x2 (4, 1, 1, 4): p_o = 8/10, p_e = 0.5*0.5 + 0.5*0.5 = 0.5, kappa = 0.3/0.5 = 0.6
    a, b = _from_table(4, 1, 1, 4)
    # tolerance: float divisions of exact ratios; well within 1e-12
    assert cohen_kappa(a, b) == pytest.approx(0.6, abs=1e-12)
    # 3 labels: agreements at items 0, 1, 3, 5 -> p_o = 4/6; A has A3 B2 C1, B has A2 B2 C2,
    # p_e = (3*2 + 2*2 + 1*2)/36 = 1/3; kappa = (2/3 - 1/3)/(2/3) = 0.5
    assert cohen_kappa(list("AAABBC"), list("AABBCC")) == pytest.approx(0.5, abs=1e-12)
    # disjoint constant raters: p_o = 0, p_e = 0, kappa = 0
    assert cohen_kappa(["y"] * 5, ["n"] * 5) == pytest.approx(0.0, abs=1e-12)
    # one agreeing item each way: [1,0] vs [1,0] -> p_o = 1, p_e = 0.5, kappa = 1
    assert cohen_kappa([1, 0], [1, 0]) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.criterion("C7")
def test_invalid_input_raises():
    # oracle: requirement 7 — undefined or ill-formed comparisons are refused
    with pytest.raises(ValueError):
        cohen_kappa(["y", "n"], ["y"])
    with pytest.raises(ValueError):
        cohen_kappa([], [])
    with pytest.raises(ValueError):
        cohen_kappa(["y"] * 4, ["y"] * 4)


_LABELS = st.sampled_from(["yes", "no", "partly"])
_PAIRS = st.lists(st.tuples(_LABELS, _LABELS), min_size=1, max_size=40)


def _split(pairs):
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _degenerate(a, b) -> bool:
    return len(set(a)) == 1 and set(a) == set(b)


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(_PAIRS, st.data())
def test_symmetry_relabelling_and_permutation(pairs, data):
    # oracle: kappa depends only on the joint label table, symmetrically in the raters
    a, b = _split(pairs)
    assume(not _degenerate(a, b))
    k = cohen_kappa(a, b)
    rename = {"yes": 7, "no": "N", "partly": (1, 2)}
    order = data.draw(st.permutations(list(range(len(a)))))
    # tolerance: the same table summed in another order; float error well under 1e-9
    assert cohen_kappa(b, a) == pytest.approx(k, abs=1e-9)
    assert cohen_kappa([rename[x] for x in a], [rename[x] for x in b]) == pytest.approx(k, abs=1e-9)
    assert cohen_kappa([a[i] for i in order], [b[i] for i in order]) == pytest.approx(k, abs=1e-9)
    assert -1.0 - 1e-12 <= k <= 1.0 + 1e-12


@pytest.mark.criterion("C7")
@given(st.lists(_LABELS, min_size=2, max_size=40))
def test_self_agreement_is_one(labels):
    # oracle: p_o = 1 with p_e < 1 gives kappa = 1
    assume(len(set(labels)) >= 2)
    # tolerance: (1 - p_e)/(1 - p_e) in floats; within 1e-12
    assert cohen_kappa(labels, list(labels)) == pytest.approx(1.0, abs=1e-12)
