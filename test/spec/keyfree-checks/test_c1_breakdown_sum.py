"""C1: ``check_breakdown_sum`` — breakdown rows sum to the lookup total.

Oracle: requirement 4 of docs/specs/keyfree-checks.md. observed = sum of rows, expected =
total, tolerance = rel_tol * |total|; the arithmetic is shown in each test.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.metamorphic import check_breakdown_sum


def amt(value, unit="USD"):
    return {"value": value, "unit": unit}


@pytest.mark.criterion("C1")
def test_rows_summing_to_total_are_consistent():
    # oracle: 60 + 30 + 10 = 100 = total; tolerance = 1e-3 * 100 = 0.1
    v = check_breakdown_sum([amt(60.0), amt(30.0), amt(10.0)], amt(100.0), rel_tol=1e-3)
    assert v["check"] == "breakdown_sum"
    assert v["verdict"] == "consistent"
    assert v["reason"] == "match"
    # tolerance: exact sums of small integers; 1e-12 only absorbs float formatting
    assert v["observed"] == pytest.approx(100.0, abs=1e-12)
    assert v["expected"] == pytest.approx(100.0, abs=1e-12)
    # tolerance: 1e-3 * 100 computed in floating point
    assert v["tolerance"] == pytest.approx(0.1, rel=1e-12)


@pytest.mark.criterion("C1")
def test_total_off_by_more_than_tolerance_is_inconsistent():
    # oracle: |100 - 100.2| = 0.2 > 1e-3 * 100.2 = 0.1002 -> mismatch
    v = check_breakdown_sum([amt(60.0), amt(30.0), amt(10.0)], amt(100.2), rel_tol=1e-3)
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "mismatch"
    # tolerance: closed form above, float rounding only
    assert v["expected"] == pytest.approx(100.2, rel=1e-12)
    assert v["tolerance"] == pytest.approx(0.1002, rel=1e-9)


@pytest.mark.criterion("C1")
def test_total_within_tolerance_is_consistent():
    # oracle: |100 - 100.05| = 0.05 <= 1e-3 * 100.05 = 0.10005 -> match
    v = check_breakdown_sum([amt(60.0), amt(30.0), amt(10.0)], amt(100.05), rel_tol=1e-3)
    assert v["verdict"] == "consistent"
    assert v["reason"] == "match"


@pytest.mark.criterion("C1")
def test_default_tolerance_is_one_in_a_thousand():
    # oracle: requirement 4 default rel_tol=1e-3: 1000 vs 1000.9 -> 0.9 <= 1.0009 (match);
    # 1000 vs 1001.2 -> 1.2 > 1.0012 (mismatch)
    rows = [amt(700.0), amt(300.0)]
    assert check_breakdown_sum(rows, amt(1000.9))["verdict"] == "consistent"
    assert check_breakdown_sum(rows, amt(1001.2))["verdict"] == "inconsistent"


@pytest.mark.criterion("C1")
def test_one_row_is_not_applicable():
    # oracle: requirement 4 — fewer than 2 rows -> not_applicable "too_few_rows", even when
    # the single row equals the total
    v = check_breakdown_sum([amt(100.0)], amt(100.0))
    assert v["check"] == "breakdown_sum"
    assert v["verdict"] == "not_applicable"
    assert v["reason"] == "too_few_rows"


_int_rows = st.lists(st.integers(min_value=1, max_value=10**9), min_size=2, max_size=12)


@pytest.mark.criterion("C1")
@given(values=_int_rows)
def test_random_rows_with_total_set_to_their_sum_are_consistent(values):
    # oracle: requirement 4 — total = sum(rows) exactly (integers), so |observed-expected| = 0
    v = check_breakdown_sum([amt(float(x)) for x in values], amt(float(sum(values))))
    assert v["verdict"] == "consistent"
    # tolerance: integer sums below 2**53 are exact in float
    assert v["observed"] == pytest.approx(float(sum(values)), abs=0.0)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(values=_int_rows, k=st.sampled_from([0.5, 2.0, 4.0, 1024.0]),
       off=st.sampled_from([0.0, 0.0005, 0.002, -0.003]), data=st.data())
def test_permuting_and_scaling_leave_the_verdict_unchanged(values, k, off, data):
    # oracle: requirement 4 — the test is relative (|sum - T| <= rel_tol * |T|), so
    # scaling every value by k > 0 (a power of two: exact) and permuting the rows keep the
    # verdict. off moves the total by a fraction of the sum well inside (0, 5e-4) or well
    # outside (2e-3, 3e-3) the 1e-3 tolerance, so the expected verdict is known up front
    s = float(sum(values))
    total = s * (1.0 + off)
    expected = "consistent" if abs(off) < 1e-3 else "inconsistent"
    base = check_breakdown_sum([amt(float(x)) for x in values], amt(total))
    assert base["verdict"] == expected
    perm = data.draw(st.permutations(values))
    scaled = check_breakdown_sum([amt(float(x) * k) for x in perm], amt(total * k))
    assert scaled["verdict"] == expected
