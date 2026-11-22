"""Spec tests for plan-cagr-and-ratios C1 (`cagr_value`) and C2 (`cagr_years`).

docs/specs/plan-cagr-and-ratios.md, Requirements 1 and 2. Pure functions.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.cagr import cagr_value, cagr_years

# The closed form via exp/log, a different computation from a power; the two agree to a few
# ulps, so 1e-9 relative (and 1e-12 absolute near a zero rate) is float rounding only.
REL, ABS = 1e-9, 1e-12


def _closed_form(end, start, years):
    """S4: (end / start)^(1/n) - 1, computed as exp(log(end / start) / n) - 1."""
    return math.exp(math.log(end / start) / years) - 1


positive = st.floats(min_value=1e3, max_value=1e13, allow_nan=False, allow_infinity=False)
spans = st.integers(min_value=1, max_value=10)
# end/start bounded for the round-trip identities: near g = -1, (1 + g) carries an absolute
# error of ~1e-16, so for end/start below ~1e-7 no float g satisfies start*(1+g)^n = end to
# rel 1e-9 (dispute 2026-11-11). 1e-3..1e3 covers any real reported-amount growth.
ratios = st.floats(min_value=1e-3, max_value=1e3, allow_nan=False, allow_infinity=False)


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_cagr_point_values():
    # oracle: Notes — 106.48e9 / 80e9 = 1.331 = 1.1^3, so 3 years give 10%; 121e9 / 100e9 =
    # 1.21 = 1.1^2, so 2 years give 10%; a decline 100 -> 64 over 2 years is 0.8 - 1 = -20%;
    # one year is simple growth; each also via the closed form computed here
    r = cagr_value(106_480_000_000.0, 80_000_000_000.0, 3)
    assert r["status"] == "OK" and r["error"] is None
    assert r["value"] == pytest.approx(0.1, rel=REL, abs=ABS)
    assert r["value"] == pytest.approx(_closed_form(106.48e9, 80e9, 3), rel=REL, abs=ABS)
    r = cagr_value(121_000_000_000.0, 100_000_000_000.0, 2)
    assert r["status"] == "OK"
    assert r["value"] == pytest.approx(0.1, rel=REL, abs=ABS)
    r = cagr_value(64.0, 100.0, 2)
    assert r["status"] == "OK"
    assert r["value"] == pytest.approx(-0.2, rel=REL, abs=ABS)
    r = cagr_value(88_000_000_000.0, 80_000_000_000.0, 1)
    assert r["value"] == pytest.approx(88 / 80 - 1, rel=REL, abs=ABS)
    r = cagr_value(5, 4, 1)  # ints are real numbers
    assert r["status"] == "OK"
    assert r["value"] == pytest.approx(0.25, rel=REL, abs=ABS)


@pytest.mark.criterion("C1")
def test_cagr_not_meaningful_and_errors():
    # oracle: Requirement 1 — non-finite or non-number inputs ERROR naming the argument;
    # years not an int >= 1 ERROR naming years; a non-positive input NOT_MEANINGFUL (S4 "only
    # when both are positive"); value None unless OK
    for end, start, word in [(100.0, 0.0, "start"), (100.0, -50.0, "start"),
                             (0.0, 100.0, "end"), (-10.0, 100.0, "end")]:
        r = cagr_value(end, start, 2)
        assert r["status"] == "NOT_MEANINGFUL", (end, start)
        assert r["value"] is None
        assert r["error"] and word in r["error"]
    for end, start, word in [(math.nan, 100.0, "end"), (100.0, math.nan, "start"),
                             (math.inf, 100.0, "end"), (100.0, -math.inf, "start"),
                             (None, 100.0, "end"), (100.0, None, "start"),
                             (True, 100.0, "end"), (100.0, "80", "start")]:
        r = cagr_value(end, start, 2)
        assert r["status"] == "ERROR", (end, start)
        assert r["value"] is None
        assert r["error"] and word in r["error"]
    for years in (0, -1, 2.0, True, None):
        r = cagr_value(121.0, 100.0, years)
        assert r["status"] == "ERROR", years
        assert r["value"] is None
        assert "years" in r["error"]


@pytest.mark.criterion("C1")
@given(positive, ratios, spans)
def test_cagr_round_trips_and_matches_the_closed_form(start, ratio, years):
    # oracle: S4 definition — start * (1 + g)^n = end; closed form computed in the test
    end = start * ratio
    r = cagr_value(end, start, years)
    assert r["status"] == "OK"
    g = r["value"]
    assert g == pytest.approx(_closed_form(end, start, years), rel=REL, abs=ABS)
    assert start * (1 + g) ** years == pytest.approx(end, rel=REL)


@pytest.mark.criterion("C1")
@given(positive, positive, st.floats(min_value=1e-3, max_value=1e3), spans)
def test_cagr_is_scale_invariant(start, end, k, years):
    # oracle: a ratio of two amounts in one unit does not depend on the unit's scale
    a = cagr_value(end, start, years)["value"]
    b = cagr_value(end * k, start * k, years)["value"]
    assert b == pytest.approx(a, rel=REL, abs=ABS)


@pytest.mark.criterion("C1")
@given(positive, ratios, ratios, spans, spans)
def test_consecutive_spans_compound_to_the_whole(a, r_ab, r_bc, m, n):
    # oracle: (1 + g_ac)^(m+n) = (1 + g_ab)^m (1 + g_bc)^n, from the definition
    b = a * r_ab
    c = b * r_bc
    g_ab = cagr_value(b, a, m)["value"]
    g_bc = cagr_value(c, b, n)["value"]
    g_ac = cagr_value(c, a, m + n)["value"]
    assert (1 + g_ac) ** (m + n) == pytest.approx((1 + g_ab) ** m * (1 + g_bc) ** n, rel=REL)


@pytest.mark.criterion("C1")
@given(positive, st.one_of(st.floats(0.05, 0.99), st.floats(1.01, 20.0)), spans)
def test_cagr_sign_follows_the_change_and_stays_above_minus_one(start, factor, years):
    # oracle: (x)^(1/n) - 1 has the sign of x - 1 and exceeds -1 for x > 0
    g = cagr_value(start * factor, start, years)["value"]
    assert g > -1
    assert (g > 0) == (factor > 1)
    assert cagr_value(start, start, years)["value"] == pytest.approx(0.0, abs=ABS)


@pytest.mark.criterion("C1")
@given(st.floats(min_value=-1e12, max_value=0.0), positive, spans, st.booleans())
def test_any_non_positive_input_is_not_meaningful(bad, good, years, bad_is_start):
    # oracle: S4 "only when both are positive"
    end, start = (good, bad) if bad_is_start else (bad, good)
    r = cagr_value(end, start, years)
    assert r["status"] == "NOT_MEANINGFUL"
    assert r["value"] is None
    assert r["error"]


# --------------------------------------------------------------------------- C2
def _step(fy, **kw):
    return {"id": f"r{fy}", "op": "lookup", "fy": fy, **kw}


@pytest.mark.criterion("C2")
def test_cagr_years_points():
    # oracle: Requirement 2 applied by hand — fy difference with matching period kinds
    assert cagr_years(_step(2022), _step(2025)) == 3
    assert cagr_years(_step(2023, fiscal_period="Q2"), _step(2025, fiscal_period="Q2")) == 2
    assert cagr_years(_step(2023), _step(2025, fiscal_period="FY")) == 2
    assert cagr_years(_step(2023, fiscal_period="fy"), _step(2024, fiscal_period=None)) == 1
    assert cagr_years(_step(2023, ytd=None), _step(2024, ytd=False)) == 1
    assert cagr_years(_step(2025), _step(2025)) is None
    assert cagr_years(_step(2025), _step(2022)) is None
    assert cagr_years(_step("2022"), _step(2025)) is None
    assert cagr_years(_step(True), _step(2025)) is None
    assert cagr_years({"id": "x", "op": "lookup"}, _step(2025)) is None
    assert cagr_years(_step(2022, fiscal_period="Q1"), _step(2025, fiscal_period="Q2")) is None
    assert cagr_years(_step(2022, fiscal_period="FY"), _step(2025, fiscal_period="Q4")) is None
    assert cagr_years(_step(2022, fiscal_period="Q2", ytd=True),
                      _step(2025, fiscal_period="Q2")) is None


@pytest.mark.criterion("C2")
@given(st.integers(1990, 2040), st.integers(1, 20),
       st.sampled_from([None, "FY", "Q1", "Q2", "Q3", "Q4"]), st.booleans())
def test_cagr_years_is_the_label_difference(a, gap, period, ytd):
    # oracle: Requirement 2 — b - a for a < b with one period kind; reversed is None
    b = a + gap
    s, e = _step(a, fiscal_period=period, ytd=ytd), _step(b, fiscal_period=period, ytd=ytd)
    assert cagr_years(s, e) == gap
    assert cagr_years(e, s) is None
