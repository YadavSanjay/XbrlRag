"""C2: ``check_growth`` — a YoY compute equals (current - prior) / |prior| of two lookups.

Oracle: requirement 5 of docs/specs/keyfree-checks.md and S4 of
docs/design/XbrlRag_Eval_Semantics.md (growth is not meaningful when prior <= 0). The
Micron figures (operating income FY2023 -5,745 -> FY2024 1,304, $M) are the public case
already used by docs/specs/pct-change-nm.md.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.metamorphic import check_growth


def amt(value, unit="USD"):
    return {"value": value, "unit": unit}


def g(value):
    return {"value": value, "unit": "pure"}


@pytest.mark.criterion("C2")
def test_growth_matching_the_lookups_is_consistent():
    # oracle: (112 - 100) / |100| = 0.12
    v = check_growth(g(0.12), amt(112.0), amt(100.0))
    assert v["check"] == "growth"
    assert v["verdict"] == "consistent"
    assert v["reason"] == "match"
    # tolerance: closed form in floating point
    assert v["expected"] == pytest.approx(0.12, rel=1e-12)
    assert v["observed"] == pytest.approx(0.12, rel=1e-12)
    # tolerance: the default abs_tol 5e-4 is returned as given
    assert v["tolerance"] == pytest.approx(5e-4, rel=1e-12)


@pytest.mark.criterion("C2")
def test_growth_off_by_a_point_is_inconsistent():
    # oracle: expected 0.12; |0.13 - 0.12| = 0.01 > 5e-4
    v = check_growth(g(0.13), amt(112.0), amt(100.0))
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "mismatch"
    # tolerance: closed form in floating point
    assert v["expected"] == pytest.approx(0.12, rel=1e-12)


@pytest.mark.criterion("C2")
def test_growth_within_abs_tol_is_consistent():
    # oracle: |0.1204 - 0.12| = 0.0004 <= 5e-4; |0.1207 - 0.12| = 0.0007 > 5e-4
    assert check_growth(g(0.1204), amt(112.0), amt(100.0))["verdict"] == "consistent"
    assert check_growth(g(0.1207), amt(112.0), amt(100.0))["verdict"] == "inconsistent"


@pytest.mark.criterion("C2")
def test_decline_uses_abs_prior():
    # oracle: (80 - 100) / |100| = -0.2
    v = check_growth(g(-0.2), amt(80.0), amt(100.0))
    assert v["verdict"] == "consistent"
    # tolerance: closed form in floating point
    assert v["expected"] == pytest.approx(-0.2, rel=1e-12)


@pytest.mark.criterion("C2")
def test_negative_prior_declined_is_consistent_not_meaningful():
    # oracle: S4 — prior -5,745 <= 0: growth not meaningful; a None growth is the right
    # outcome
    v = check_growth(g(None), amt(1304.0), amt(-5745.0))
    assert v["check"] == "growth"
    assert v["verdict"] == "consistent"
    assert v["reason"] == "not_meaningful"


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("stated", [-1.227, 1.227, 0.0])
def test_negative_prior_with_a_number_is_inconsistent(stated):
    # oracle: S4 — stating any growth figure from a prior <= 0 is a defect, including the
    # (new - old)/old form (-1.227) and the |old| form (+1.227)
    v = check_growth(g(stated), amt(1304.0), amt(-5745.0))
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "not_meaningful"


@pytest.mark.criterion("C2")
def test_zero_prior_behaves_like_negative():
    # oracle: S4 — "not meaningful when prior <= 0" includes prior = 0
    assert check_growth(g(None), amt(50.0), amt(0.0))["verdict"] == "consistent"
    v = check_growth(g(0.5), amt(50.0), amt(0.0))
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "not_meaningful"


@pytest.mark.criterion("C2")
def test_positive_prior_with_no_growth_value_is_not_applicable():
    # oracle: requirement 5 — prior > 0 and growth None: nothing to compare
    v = check_growth(g(None), amt(112.0), amt(100.0))
    assert v["check"] == "growth"
    assert v["verdict"] == "not_applicable"
    assert v["reason"] == "missing_value"


_pos = st.integers(min_value=1, max_value=10**9)


@pytest.mark.criterion("C2")
@given(cur=st.integers(min_value=-10**9, max_value=10**9), prior=_pos)
def test_random_growth_computed_in_test_is_consistent(cur, prior):
    # oracle: requirement 5 — growth = (cur - prior) / prior computed here, prior > 0
    growth = (cur - prior) / prior
    v = check_growth(g(growth), amt(float(cur)), amt(float(prior)))
    assert v["verdict"] == "consistent"
    # tolerance: the same closed form evaluated twice in float
    assert v["expected"] == pytest.approx(growth, rel=1e-9, abs=1e-12)


@pytest.mark.criterion("C2")
@given(cur=st.integers(min_value=-10**9, max_value=10**9),
       prior=st.integers(min_value=-10**9, max_value=0),
       stated=st.floats(min_value=-100, max_value=100, allow_nan=False))
def test_any_growth_value_with_non_positive_prior_is_inconsistent(cur, prior, stated):
    # oracle: S4 — prior <= 0: any stated growth figure is inconsistent
    v = check_growth(g(stated), amt(float(cur)), amt(float(prior)))
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "not_meaningful"
