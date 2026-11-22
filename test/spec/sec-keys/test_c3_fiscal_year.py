"""C3: ``fiscal_year_key`` answers the year the filer itself labels N (S2), with S1 values.

Oracle: requirement 7 of docs/specs/sec-keys.md, read off the fixtures by hand. Day counts
for the 350-380 day window are computed in the comments.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, fiscal_year_key


@pytest.mark.criterion("C3")
def test_sep_fy2023_is_the_53_week_year_with_the_recast_value(kit):
    # oracle: fixture — the fy=2023 10-K (A23) reports periods ending 2023-09-30, 2022-09-24 and
    # 2021-09-25; the greatest end is 2023-09-30, start 2022-09-25 (370 days, in the window).
    # The latest filed entry for that period is A24's recast 340_750_000_000 (A23 had 340bn).
    doc = kit.companyfacts(kit.SEP_CIK)
    key = fiscal_year_key(doc, "us-gaap:Revenues", "USD", 2023)
    assert set(key) == kit.KEY_FIELDS
    assert (key["period_start"], key["period_end"]) == ("2022-09-25", "2023-09-30")
    assert key["value"] == 340_750_000_000
    assert key["accession"] == kit.A24
    assert key["filed"] == "2024-11-01"
    assert key["fiscal_year"] == 2023
    assert key["calendar_year"] is None
    assert key["policies"] == ["S1", "S2", "S6"]
    assert key["selection"] == "latest_filed"
    assert key["key_source"] == "sec_companyfacts"
    assert key["status"] == "single-source"


@pytest.mark.criterion("C3")
def test_sep_fy2022_ignores_the_quarter_tagged_fp_fy(kit):
    # oracle: fixture — A22 (fy=2022) tags FY2022 (2021-09-26..2022-09-24, 363 days) and a
    # 3-month 2022-06-26..2022-09-24 (90 days) both with fp=FY and the same end. Only the
    # 363-day entry is annual, so the start is 2021-09-26; the value is A24's 321_500_000_000.
    doc = kit.companyfacts(kit.SEP_CIK)
    key = fiscal_year_key(doc, "us-gaap:Revenues", "USD", 2022)
    assert (key["period_start"], key["period_end"]) == kit.SEP_FY22
    assert key["value"] == 321_500_000_000
    assert key["fiscal_year"] == 2022


@pytest.mark.criterion("C3")
def test_sep_fy2024_and_instant_year_end_balance(kit):
    # oracle: fixture — FY2024 revenue 360_000_000_000 (A24, 2023-10-01..2024-09-28). Assets:
    # A23 (fy=2023) instants end 2023-09-30 and 2022-09-24 -> 2023-09-30; the latest filed
    # value at 2023-09-30 is A24's 351_000_000_000.
    doc = kit.companyfacts(kit.SEP_CIK)
    rev = fiscal_year_key(doc, "us-gaap:Revenues", "USD", 2024)
    assert (rev["period_start"], rev["period_end"]) == kit.SEP_FY24
    assert rev["value"] == 360_000_000_000
    assets = fiscal_year_key(doc, "us-gaap:Assets", "USD", 2023)
    assert assets["period_start"] is None
    assert assets["period_end"] == "2023-09-30"
    assert assets["value"] == 351_000_000_000
    assert assets["accession"] == kit.A24
    assert assets["fiscal_year"] == 2023


@pytest.mark.criterion("C3")
def test_june_filer_and_20f_filer(kit):
    # oracle: fixture — June filer: J24 (fy=2024) ends 2024-06-30 (2023-07-01 start, 365 days);
    # J25 restates it unchanged at 245_000_000_000 and is filed later (2025-07-30), so J25.
    # TWD filer: 20-F (fy=2024) 2024-01-01..2024-12-31, 2_894_307_699_000 TWD.
    jun = fiscal_year_key(kit.companyfacts(kit.JUN_CIK), "us-gaap:Revenues", "USD", 2024)
    assert (jun["period_start"], jun["period_end"]) == ("2023-07-01", "2024-06-30")
    assert jun["value"] == 245_000_000_000
    assert jun["accession"] == kit.J25
    assert jun["cik"] == kit.JUN_CIK
    twd = fiscal_year_key(kit.companyfacts(kit.TWD_CIK), "ifrs-full:Revenue", "TWD", 2024)
    assert (twd["period_start"], twd["period_end"]) == ("2024-01-01", "2024-12-31")
    assert twd["value"] == 2_894_307_699_000
    assert twd["form"] == "20-F"
    assert twd["accession"] == kit.T25
    assert twd["unit"] == "TWD"


@pytest.mark.criterion("C3")
def test_years_without_an_annual_filing_labelled_n(kit):
    # oracle: requirement 7 — fy=2025 exists only on a 10-Q (fp=Q1); no filing is labelled
    # fy=2021 although the FY2021 period is reported as a comparative
    doc = kit.companyfacts(kit.SEP_CIK)
    with pytest.raises(KeyNotFoundError):
        fiscal_year_key(doc, "us-gaap:Revenues", "USD", 2025)
    with pytest.raises(KeyNotFoundError):
        fiscal_year_key(doc, "us-gaap:Revenues", "USD", 2021)
    with pytest.raises(KeyNotFoundError):
        fiscal_year_key(doc, "us-gaap:Assets", "USD", 2025)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(year=st.sampled_from([2022, 2023, 2024]),
       v=st.integers(min_value=-10**12, max_value=10**13))
def test_a_later_recast_moves_the_value_not_the_period(kit, year, v):
    # oracle: requirement 7 step 3 — a 10-K filed 2026-11-01 (fy=2026) recasting year N to v
    # becomes the latest filed entry for N's period; the period comes from the fy=N filing
    periods = {2022: kit.SEP_FY22, 2023: kit.SEP_FY23, 2024: kit.SEP_FY24}
    doc = kit.companyfacts(kit.SEP_CIK)
    start, end = periods[year]
    kit.units_of(doc, "us-gaap", "Revenues")["USD"].append(
        {"start": start, "end": end, "val": v, "accn": "0000900001-26-000040", "fy": 2026,
         "fp": "FY", "form": "10-K", "filed": "2026-11-01"})
    key = fiscal_year_key(doc, "us-gaap:Revenues", "USD", year)
    assert (key["period_start"], key["period_end"]) == (start, end)
    assert key["value"] == v
    assert key["accession"] == "0000900001-26-000040"
    assert key["fiscal_year"] == year
