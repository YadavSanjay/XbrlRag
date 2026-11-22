"""Spec tests for period-disclosure C3: year lengths are disclosed when years are compared.

docs/specs/period-disclosure.md, requirement R3. Stored rows come from xbrl_db; the day
counts and stated periods come from SEC companyfacts start/end for the same accessions.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.period_disclosure import year_length_disclosure

KEYS = {"days_current", "days_prior", "current_period", "prior_period", "differs", "note"}
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def _cf_days(entry: dict) -> int:
    return (dt.date.fromisoformat(entry["end"]) - dt.date.fromisoformat(entry["start"])).days + 1


@pytest.mark.criterion("C3")
def test_aapl_fy2024_against_53_week_fy2023(fx):
    # oracle: companyfacts, 0000320193-24-000123: FY2024 2023-10-01..2024-09-28 (364 days),
    # FY2023 2022-09-25..2023-09-30 (371 days, a 53-week year); 7 days apart >= 6
    cur = fx.cf_entry("0000320193-24-000123", REV, "2023-10-01", "2024-09-28")
    pri = fx.cf_entry("0000320193-23-000106", REV, "2022-09-25", "2023-09-30")
    assert (_cf_days(cur), _cf_days(pri)) == (364, 371)
    out = year_length_disclosure(fx.fact(14262), fx.fact(12096))
    assert set(out) == KEYS
    assert (out["days_current"], out["days_prior"]) == (364, 371)
    assert out["current_period"] == (cur["start"], cur["end"])
    assert out["prior_period"] == (pri["start"], pri["end"])
    assert out["differs"] is True
    assert "364" in out["note"] and "371" in out["note"] and "53-week" in out["note"]


@pytest.mark.criterion("C3")
def test_aapl_fy2023_against_fy2022_differs(fx):
    # oracle: companyfacts 0000320193-22-000108: FY2022 2021-09-26..2022-09-24 (364 days)
    pri = fx.cf_entry("0000320193-22-000108", REV, "2021-09-26", "2022-09-24")
    out = year_length_disclosure(fx.fact(12096), fx.fact(10919))
    assert (out["days_current"], out["days_prior"]) == (371, _cf_days(pri))
    assert out["prior_period"] == (pri["start"], pri["end"])
    assert out["differs"] is True
    assert "53-week" in out["note"]


@pytest.mark.criterion("C3")
def test_msft_equal_years_need_no_note(fx):
    # oracle: companyfacts 0001193125-26-323660: FY2026 2025-07-01..2026-06-30 and FY2025
    # 2024-07-01..2025-06-30, 365 days each
    cur = fx.cf_entry("0001193125-26-323660", REV, "2025-07-01", "2026-06-30")
    pri = fx.cf_entry("0001193125-26-323660", REV, "2024-07-01", "2025-06-30")
    out = year_length_disclosure(fx.fact(105742), fx.fact(104052))
    assert (out["days_current"], out["days_prior"]) == (_cf_days(cur), _cf_days(pri)) == (365, 365)
    assert out["current_period"] == (cur["start"], cur["end"])
    assert out["differs"] is False
    assert out["note"] is None


_starts = st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31))
_lengths = st.integers(min_value=340, max_value=390)


def _row(start: dt.date, days: int) -> dict:
    return {"period_start": start.isoformat(),
            "period_end": (start + dt.timedelta(days=days)).isoformat()}


@pytest.mark.criterion("C3")
@given(s1=_starts, d1=_lengths, s2=_starts, d2=_lengths)
def test_differs_exactly_when_six_or_more_days_apart(s1, d1, s2, d2):
    # oracle: R3, differs = |days_current - days_prior| >= 6; the note names both counts
    out = year_length_disclosure(_row(s1, d1), _row(s2, d2))
    assert (out["days_current"], out["days_prior"]) == (d1, d2)
    assert out["differs"] is (abs(d1 - d2) >= 6)
    if out["differs"]:
        assert str(d1) in out["note"] and str(d2) in out["note"]
        assert ("53-week" in out["note"]) is (d1 in (370, 371, 372) or d2 in (370, 371, 372))
    else:
        assert out["note"] is None


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(s1=_starts, d1=_lengths, s2=_starts, d2=_lengths,
       shift=st.integers(min_value=-2000, max_value=2000))
def test_swap_and_shift(s1, d1, s2, d2, shift):
    # oracle: swapping the sides swaps the counts; shifting every date moves only the periods
    a, b = _row(s1, d1), _row(s2, d2)
    out = year_length_disclosure(a, b)
    swapped = year_length_disclosure(b, a)
    assert (swapped["days_current"], swapped["days_prior"]) == (d2, d1)
    assert swapped["differs"] is out["differs"]
    moved = dt.timedelta(days=shift)
    shifted = year_length_disclosure(_row(s1 + moved, d1), _row(s2 + moved, d2))
    assert (shifted["days_current"], shifted["days_prior"], shifted["differs"]) == (
        out["days_current"], out["days_prior"], out["differs"])
    assert shifted["current_period"][0] == (s1 + moved).isoformat()
