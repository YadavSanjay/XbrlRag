"""C5: keys stay in the reporting unit at full value; a unit mismatch is an error (S6).

Oracle: requirements 4 and 8 of docs/specs/sec-keys.md and policy S6, read off the fixtures.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.eval.keys.core import (
    UnitMismatchError,
    as_reported_key,
    calendar_year_key,
    fiscal_year_key,
    latest_filed_key,
)


@pytest.mark.criterion("C5")
def test_full_value_in_reporting_unit(kit):
    # oracle: fixture — the 20-F's FY2024 revenue is 2_894_307_699_000 TWD, not a scaled
    # figure (2_894_307.699 millions) and not a USD translation
    doc = kit.companyfacts(kit.TWD_CIK)
    key = latest_filed_key(doc, "ifrs-full:Revenue", "TWD", "2024-01-01", "2024-12-31")
    assert key["value"] == 2_894_307_699_000
    assert key["unit"] == "TWD"
    rate = latest_filed_key(kit.companyfacts(kit.SEP_CIK),
                            "us-gaap:EffectiveIncomeTaxRateContinuingOperations", "pure",
                            *kit.SEP_FY24)
    # tolerance: the fixture's 0.155 is copied as a float; rel=1e-12 is float idiom
    assert rate["value"] == pytest.approx(0.155, rel=1e-12)
    assert rate["unit"] == "pure"


@pytest.mark.criterion("C5")
def test_unit_mismatch_in_each_builder(kit):
    # oracle: requirement 4 — ifrs-full:Revenue has only TWD; the tax rate only "pure" (no
    # alias to "Rate"); requirement 8 — the revenue frame's uom is USD
    twd = kit.companyfacts(kit.TWD_CIK)
    with pytest.raises(UnitMismatchError):
        latest_filed_key(twd, "ifrs-full:Revenue", "USD", "2024-01-01", "2024-12-31")
    with pytest.raises(UnitMismatchError):
        as_reported_key(twd, "ifrs-full:Revenue", "USD", "2024-01-01", "2024-12-31")
    with pytest.raises(UnitMismatchError):
        fiscal_year_key(twd, "ifrs-full:Revenue", "USD", 2024)
    with pytest.raises(UnitMismatchError):
        latest_filed_key(kit.companyfacts(kit.SEP_CIK),
                         "us-gaap:EffectiveIncomeTaxRateContinuingOperations", "Rate",
                         *kit.SEP_FY24)
    with pytest.raises(UnitMismatchError):
        calendar_year_key(kit.revenue_frame(), kit.SEP_CIK, "EUR")


@pytest.mark.criterion("C5")
@given(unit=st.text(alphabet=st.characters(min_codepoint=65, max_codepoint=90),
                    min_size=1, max_size=6))
def test_any_missing_unit_raises(kit, unit):
    # oracle: S6 — a unit the concept lacks is an error, never a converted key
    assume(unit != "USD")
    doc = kit.companyfacts(kit.SEP_CIK)
    with pytest.raises(UnitMismatchError):
        latest_filed_key(doc, "us-gaap:Revenues", unit, *kit.SEP_FY24)
    with pytest.raises(UnitMismatchError):
        fiscal_year_key(doc, "us-gaap:Revenues", unit, 2024)
    with pytest.raises(UnitMismatchError):
        calendar_year_key(kit.revenue_frame(), kit.SEP_CIK, unit)
