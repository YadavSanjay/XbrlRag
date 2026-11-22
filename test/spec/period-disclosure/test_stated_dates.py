"""Spec tests for period-disclosure C6: every emitted date is a stated date.

docs/specs/period-disclosure.md, requirement R6. The store keeps SEC's end + 1 day (Arelle's
next-midnight date); what an answer states is SEC's last day.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.period_disclosure import derive_q4, year_length_disclosure

REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


@pytest.mark.criterion("C6")
def test_aapl_fy2024_stored_end_is_stated_one_day_earlier(fx):
    # oracle: companyfacts 0000320193-24-000123: FY2024 ends 2024-09-28; stored 2024-09-29
    row = fx.fact(14262)
    assert row["period_end"] == "2024-09-29"
    entry = fx.cf_entry("0000320193-24-000123", REV, "2023-10-01", "2024-09-28")
    out = year_length_disclosure(row, fx.fact(12096))
    assert out["current_period"] == (entry["start"], entry["end"])


@pytest.mark.criterion("C6")
def test_msft_q4_dates_are_stated(fx):
    # oracle: companyfacts: the 9M YTD ends 2026-03-31 and the year 2026-06-30
    # (0001193125-26-191507, -323660); Q4 is 2026-04-01..2026-06-30
    nine = fx.cf_entry("0001193125-26-191507", REV, "2025-07-01", "2026-03-31")
    year = fx.cf_entry("0001193125-26-323660", REV, "2025-07-01", "2026-06-30")
    out = derive_q4(fx.fact(105742), fx.fact(52104))
    q4_start = (dt.date.fromisoformat(nine["end"]) + dt.timedelta(days=1)).isoformat()
    assert (out["period_start"], out["period_end"]) == (q4_start, year["end"])


_starts = st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31))


@pytest.mark.criterion("C6")
@given(start=_starts, fy_days=st.integers(min_value=350, max_value=380),
       q4_days=st.integers(min_value=84, max_value=119), s2=_starts,
       d2=st.integers(min_value=340, max_value=390))
def test_emitted_dates_are_stored_minus_one_day(start, fy_days, q4_days, s2, d2):
    # oracle: the store convention (stored end = SEC end + 1 day), R6
    nm_days = fy_days - q4_days
    stored_fy_end = start + dt.timedelta(days=fy_days)
    stored_nm_end = start + dt.timedelta(days=nm_days)
    common = {"concept": "t:Revenue", "unit": "USD", "period_start": start.isoformat(),
              "decimals": -6}
    annual = {**common, "fact_id": 1, "value": Decimal(10), "accession_no": "a",
              "period_end": stored_fy_end.isoformat()}
    nine = {**common, "fact_id": 2, "value": Decimal(7), "accession_no": "b",
            "period_end": stored_nm_end.isoformat()}
    out = derive_q4(annual, nine)
    if 252 <= nm_days <= 280:
        assert out["period_end"] == (stored_fy_end - dt.timedelta(days=1)).isoformat()
        assert out["period_start"] == stored_nm_end.isoformat()
        span = (dt.date.fromisoformat(out["period_end"])
                - dt.date.fromisoformat(out["period_start"])).days + 1
        assert out["days"] == span == q4_days
    prior = {"period_start": s2.isoformat(), "period_end": (s2 + dt.timedelta(days=d2)).isoformat()}
    yl = year_length_disclosure(annual, prior)
    assert yl["current_period"] == (start.isoformat(),
                                    (stored_fy_end - dt.timedelta(days=1)).isoformat())
    assert yl["prior_period"] == (s2.isoformat(), (s2 + dt.timedelta(days=d2 - 1)).isoformat())
    for (a, b), days in ((yl["current_period"], yl["days_current"]),
                         (yl["prior_period"], yl["days_prior"])):
        assert (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1 == days
