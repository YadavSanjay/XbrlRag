"""C3: ``aggregate`` (sum, median, count, rank) and ``cagr``.

Oracle: requirement 11 of docs/specs/hx-authoring.md and S4 of
docs/design/XbrlRag_Eval_Semantics.md (CAGR = (end / start) ** (1 / n) - 1). Every value
is computed by hand in the test.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.hx import aggregate, cagr


@pytest.mark.criterion("C3")
def test_sum_median_count_by_hand():
    # oracle: hand arithmetic — 3 + 1 + 2 = 6; sorted 1, 2, 3 -> median 2; 3 values.
    values = {"a": 3.0, "b": 1.0, "c": 2.0}
    assert aggregate("sum", values) == pytest.approx(6.0, abs=1e-12)  # exact small ints
    assert aggregate("median", values) == pytest.approx(2.0, abs=1e-12)  # exact
    assert aggregate("count", values) == pytest.approx(3.0, abs=1e-12)  # exact
    # oracle: even count — sorted 1, 2, 3, 10 -> (2 + 3) / 2 = 2.5
    even = {"a": 10.0, "b": 2.0, "c": 3.0, "d": 1.0}
    assert aggregate("median", even) == pytest.approx(2.5, abs=1e-12)  # exact halves


@pytest.mark.criterion("C3")
def test_rank_counts_strictly_greater_values_and_ties_share():
    # oracle: requirement 11 — rank = 1 + #values strictly greater than the subject's.
    values = {"a": 300.0, "b": 200.0, "c": 200.0, "d": 100.0}
    assert aggregate("rank", values, subject="a") == pytest.approx(1.0, abs=0)  # exact
    assert aggregate("rank", values, subject="b") == pytest.approx(2.0, abs=0)  # exact
    assert aggregate("rank", values, subject="c") == pytest.approx(2.0, abs=0)  # tie
    assert aggregate("rank", values, subject="d") == pytest.approx(4.0, abs=0)  # 3 above


@pytest.mark.criterion("C3")
def test_empty_inputs_and_errors():
    # oracle: requirement 11 — sum of nothing 0.0, median of nothing None, count 0.
    # a non-trivial sum first keeps every wrong-value stub failing
    assert aggregate("sum", {"x": 4.5, "y": 0.5}) == pytest.approx(5.0, abs=1e-12)  # exact
    assert aggregate("sum", {}) == pytest.approx(0.0, abs=0)  # exact: the empty sum
    assert aggregate("median", {}) is None
    assert aggregate("count", {}) == pytest.approx(0.0, abs=0)  # exact: no values
    with pytest.raises(ValueError):
        aggregate("rank", {"a": 1.0}, subject="z")
    with pytest.raises(ValueError):
        aggregate("rank", {"a": 1.0})
    with pytest.raises(ValueError):
        aggregate("mode", {"a": 1.0})


@pytest.mark.criterion("C3")
def test_cagr_by_hand_and_undefined_cases():
    # oracle: S4 — (121 / 100) ** (1 / 2) - 1 = 1.1 - 1 = 0.1
    assert cagr(100.0, 121.0, 2) == pytest.approx(0.1, abs=1e-12)  # float sqrt of 1.21
    # oracle: (50 / 200) ** (1 / 2) - 1 = 0.5 - 1 = -0.5 (a decline is still defined)
    assert cagr(200.0, 50.0, 2) == pytest.approx(-0.5, abs=1e-12)  # exact sqrt
    assert cagr(0.0, 121.0, 2) is None
    assert cagr(-100.0, 121.0, 2) is None
    assert cagr(100.0, 0.0, 2) is None
    assert cagr(100.0, -5.0, 2) is None
    assert cagr(100.0, 121.0, 0) is None


@pytest.mark.criterion("C3")
@given(values=st.lists(st.floats(min_value=-1e12, max_value=1e12, allow_nan=False),
                       min_size=1, max_size=12),
       seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_rank_of_max_is_one_and_median_is_order_free(values, seed):
    # oracle: requirement 11 — nothing is strictly greater than the maximum, so its rank
    # is 1; the median depends only on the multiset of values.
    named = {f"n{i}": v for i, v in enumerate(values)}
    top = max(named, key=lambda k: named[k])
    assert aggregate("rank", named, subject=top) == pytest.approx(1.0, abs=0)  # exact
    items = list(named.items())
    random.Random(seed).shuffle(items)
    shuffled = dict((f"m{i}", v) for i, (_, v) in enumerate(items))
    first = aggregate("median", named)
    assert first is not None
    assert aggregate("median", shuffled) == pytest.approx(first, rel=1e-12, abs=1e-6)  # same values
    assert aggregate("count", named) == pytest.approx(float(len(values)), abs=0)  # exact


@pytest.mark.criterion("C3")
@given(start=st.floats(min_value=1.0, max_value=1e12),
       growth=st.floats(min_value=-0.9, max_value=3.0),
       years=st.integers(min_value=1, max_value=10))
def test_cagr_inverts_compounding(start, growth, years):
    # oracle: S4 — end = start * (1 + g) ** n, so cagr(start, end, n) = g.
    end = start * (1 + growth) ** years
    # tolerance: a float pow round-trip
    assert cagr(start, end, years) == pytest.approx(growth, rel=1e-9, abs=1e-9)
