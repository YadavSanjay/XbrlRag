"""C4: ``check_q4`` — Q4 equals the full year minus nine months.

Oracle: requirement 7 of docs/specs/keyfree-checks.md: expected = FY - 9M, tolerance =
rel_tol * |FY|. Synthetic values; 10-Qs are not in the corpus yet.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.metamorphic import check_q4


def amt(value, unit="USD"):
    return {"value": value, "unit": unit}


@pytest.mark.criterion("C4")
def test_q4_equal_to_fy_minus_nine_months_is_consistent():
    # oracle: 400 - 290 = 110; tolerance = 1e-3 * 400 = 0.4
    v = check_q4(amt(110.0), amt(400.0), amt(290.0))
    assert v["check"] == "q4"
    assert v["verdict"] == "consistent"
    assert v["reason"] == "match"
    # tolerance: exact integer arithmetic in float; 1e-12 only for formatting
    assert v["expected"] == pytest.approx(110.0, abs=1e-12)
    assert v["observed"] == pytest.approx(110.0, abs=1e-12)
    # tolerance: 1e-3 * 400 in floating point
    assert v["tolerance"] == pytest.approx(0.4, rel=1e-12)


@pytest.mark.criterion("C4")
def test_q4_off_by_one_is_inconsistent_and_within_tolerance_is_consistent():
    # oracle: |111 - 110| = 1 > 0.4 -> mismatch; |110.3 - 110| = 0.3 <= 0.4 -> match
    v = check_q4(amt(111.0), amt(400.0), amt(290.0))
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "mismatch"
    assert check_q4(amt(110.3), amt(400.0), amt(290.0))["verdict"] == "consistent"


@pytest.mark.criterion("C4")
def test_negative_q4_from_a_loss():
    # oracle: FY net loss -50, 9M income 30 -> Q4 = -50 - 30 = -80
    assert check_q4(amt(-80.0), amt(-50.0), amt(30.0))["verdict"] == "consistent"
    assert check_q4(amt(80.0), amt(-50.0), amt(30.0))["verdict"] == "inconsistent"


@pytest.mark.criterion("C4")
@given(fy=st.integers(min_value=1000, max_value=10**10),
       nm=st.integers(min_value=-10**10, max_value=10**10),
       bump=st.floats(min_value=1.5, max_value=100.0))
def test_closed_form_is_consistent_and_a_bump_beyond_tolerance_is_not(fy, nm, bump):
    # oracle: requirement 7 — Q4 = FY - 9M exactly (integers) is consistent; adding
    # bump * tolerance (bump >= 1.5) to Q4 is inconsistent
    q4 = float(fy - nm)
    assert check_q4(amt(q4), amt(float(fy)), amt(float(nm)))["verdict"] == "consistent"
    tol = 1e-3 * fy
    v = check_q4(amt(q4 + bump * tol), amt(float(fy)), amt(float(nm)))
    assert v["verdict"] == "inconsistent"
