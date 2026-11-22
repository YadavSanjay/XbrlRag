"""C1: ``shape_quotas`` apportions intents by the plan's dev mix (35/15/10).

Oracle: requirement 6 of docs/specs/v2-suite-build.md, worked by hand below.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.v2_suite import shape_quotas


def _expected(single, cross_filer, cross_period):
    return dict(zip(
        ("lookup", "compute.ratio",
         "screen.top", "screen.bottom", "screen.sum", "screen.median", "peer_relative",
         "compute.yoy_growth", "compute.difference", "compute.cagr", "trend", "point_in_time"),
        (*single, *cross_filer, *cross_period), strict=True))


@pytest.mark.criterion("C1")
def test_sixty_splits_exactly():
    # oracle: 60 * 35/60 = 35 -> lookup 18, ratio 17; 60 * 15/60 = 15 -> 3 per shape;
    # 60 * 10/60 = 10 -> 2 per shape. No remainders anywhere.
    assert shape_quotas(60) == _expected((18, 17), (3, 3, 3, 3, 3), (2, 2, 2, 2, 2))


@pytest.mark.criterion("C1")
def test_ten_uses_largest_remainders():
    # oracle: 10*35 = 350 -> 5 r 50; 10*15 = 150 -> 2 r 30; 10*10 = 100 -> 1 r 40. Two units
    # left go to remainders 50 (single) and 40 (cross-period): classes 6 / 2 / 2.
    # single 6 over 2 shapes: 3, 3. cross-filer 2 over 5: 1, 1, 0, 0, 0.
    # cross-period 2 over 5: 1, 1, 0, 0, 0.
    assert shape_quotas(10) == _expected((3, 3), (1, 1, 0, 0, 0), (1, 1, 0, 0, 0))


@pytest.mark.criterion("C1")
def test_one_and_seven():
    # oracle: total 1: 35 -> 0 r 35, 15 -> 0 r 15, 10 -> 0 r 10: the unit goes to single
    # (largest remainder) and then to lookup. Total 7: 245 -> 4 r 5; 105 -> 1 r 45;
    # 70 -> 1 r 10: one unit left -> cross-filer (45): classes 4 / 2 / 1.
    assert shape_quotas(1) == _expected((1, 0), (0, 0, 0, 0, 0), (0, 0, 0, 0, 0))
    assert shape_quotas(7) == _expected((2, 2), (1, 1, 0, 0, 0), (1, 0, 0, 0, 0))


@pytest.mark.criterion("C1")
def test_zero_and_bad_totals():
    # oracle: requirement 6 — total 0 gives every shape 0; a negative or non-int total is
    # a ValueError
    assert shape_quotas(0) == _expected((0, 0), (0, 0, 0, 0, 0), (0, 0, 0, 0, 0))
    for bad in (-1, 2.5, "10"):
        with pytest.raises(ValueError):
            shape_quotas(bad)


@pytest.mark.criterion("C1")
@given(total=st.integers(min_value=0, max_value=10_000))
def test_apportionment_properties(kit, total):
    # oracle: requirement 6 — largest remainder keeps each class within 1 of its exact
    # share, and an even split within a class differs by at most 1, earlier shapes first
    q = shape_quotas(total)
    assert set(q) == set(kit.SHAPES)
    assert sum(q.values()) == total
    for _name, weight, shapes in kit.CLASSES:
        got = sum(q[s] for s in shapes)
        assert abs(got * 60 - total * weight) < 60
        values = [q[s] for s in shapes]
        assert values == sorted(values, reverse=True)
        assert max(values) - min(values) <= 1
