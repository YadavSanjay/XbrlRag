"""Spec tests for period-disclosure C1: a duration is classed by its length, in fixed bands.

docs/specs/period-disclosure.md, requirement R1. Stored rows come from xbrl_db; each class is
checked against an independent source: FSDS `num` `qtrs` for the same accession and value,
the iXBRL context, or SEC companyfacts start and end dates, with the spec's band table.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.period_disclosure import duration_class

# FSDS `qtrs` ("count of the number of quarters represented by the data value, rounded").
QTRS_KIND = {1: "quarter", 2: "half", 3: "nine_month", 4: "annual"}
# The spec's band table (R1), inclusive days.
BANDS = {"quarter": (84, 119), "half": (168, 189), "nine_month": (252, 280), "annual": (350, 380)}

# (fact_id, FSDS adsh, tag, ddate, qtrs) for the rows whose duration FSDS states.
FSDS_CASES = [
    (94704, "0001730168-26-000054", "RevenueFromContractWithCustomerExcludingAssessedTax",
     "20260430", 1, "Geographical=Americas;"),
    (94728, "0001730168-26-000054", "RevenueFromContractWithCustomerExcludingAssessedTax",
     "20260430", 2, "Geographical=Americas;"),
    (52104, "0001193125-26-191507", "RevenueFromContractWithCustomerExcludingAssessedTax",
     "20260331", 3, ""),
    (50640, "0001193125-26-191507", "RevenueFromContractWithCustomerExcludingAssessedTax",
     "20260331", 1, ""),
    (105742, "0001193125-26-323660", "RevenueFromContractWithCustomerExcludingAssessedTax",
     "20260630", 4, ""),
    (12096, "0000320193-23-000106", "RevenueFromContractWithCustomerExcludingAssessedTax",
     "20230930", 4, ""),
]


def _band_kind(days: int) -> str:
    for kind, (lo, hi) in BANDS.items():
        if lo <= days <= hi:
            return kind
    return "other"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("fact_id,adsh,tag,ddate,qtrs,segments", FSDS_CASES)
def test_stored_durations_match_fsds_qtrs(fx, fact_id, adsh, tag, ddate, qtrs, segments):
    # oracle: FSDS num line of the same accession, tag, member and value; its qtrs names
    # the duration (1 quarter, 2 half, 3 nine months, 4 a year)
    row = fx.fact(fact_id)
    assert fx.fsds_value(adsh, tag, ddate, qtrs, segments) == row["value"]
    assert duration_class(row["period_start"], row["period_end"]) == QTRS_KIND[qtrs]


@pytest.mark.criterion("C1")
def test_mcd_restructuring_member_is_a_quarter(fx):
    # oracle: iXBRL f-906 of mcd-20231231.htm, context 2023-10-01..2023-12-31 (92 days,
    # FSDS's rounding gives 1 quarter); stored end is SEC's end + 1 day
    row = fx.fact(215829)
    ix = fx.ix_fact("0000063908-24-000072", "f-906")
    assert ix["value"] == row["value"] == 35000000
    assert row["period_start"] == ix["start"]
    assert dt.date.fromisoformat(row["period_end"]) == (
        dt.date.fromisoformat(ix["end"]) + dt.timedelta(days=1))
    assert duration_class(row["period_start"], row["period_end"]) == "quarter"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("fact_id,accn,concept", [
    (116516, "0001868275-23-000014", "us-gaap:ProfitLoss"),  # CEG 2022-02-01..12-31 stub
    (217103, "0000063908-25-000012", "us-gaap:RestructuringAndRelatedCostIncurredCost"),
    (218467, "0000063908-26-000035", "us-gaap:RestructuringAndRelatedCostIncurredCost"),
    (135373, "0001692819-23-000005", "us-gaap:CommonStockDividendsPerShareCashPaid"),
])
def test_stubs_multi_year_and_monthly_periods_are_other(fx, fact_id, accn, concept):
    # oracle: SEC companyfacts start/end of the same accession and value give 334, 731,
    # 1,096 and 31 days, outside every band of the spec's table, so "other"
    row = fx.fact(fact_id)
    end = (dt.date.fromisoformat(row["period_end"]) - dt.timedelta(days=1)).isoformat()
    entry = fx.cf_entry(accn, concept, row["period_start"], end)
    assert entry["val"] == pytest.approx(float(row["value"]), rel=1e-12)  # same fact; exact
    days = (dt.date.fromisoformat(entry["end"]) - dt.date.fromisoformat(entry["start"])).days + 1
    assert _band_kind(days) == "other"
    assert duration_class(row["period_start"], row["period_end"]) == "other"


@pytest.mark.criterion("C1")
def test_ceg_stub_is_not_the_year_but_the_full_year_is(fx):
    # oracle: companyfacts lists CEG 2022 ProfitLoss twice in 0001868275-23-000014:
    # 2022-01-01..12-31 (-167M, 365 days, CEG's year) and 2022-02-01..12-31 (-318M, stub)
    year = fx.cf_entry("0001868275-23-000014", "us-gaap:ProfitLoss", "2022-01-01", "2022-12-31")
    stub = fx.cf_entry("0001868275-23-000014", "us-gaap:ProfitLoss", "2022-02-01", "2022-12-31")
    assert (year["val"], stub["val"]) == (-167000000, -318000000)
    assert duration_class("2022-01-01", "2023-01-01") == "annual"
    assert duration_class("2022-02-01", "2023-01-01") == "other"


@pytest.mark.criterion("C1")
def test_an_instant_is_instant(fx):
    # oracle: R1, a row without a start is an instant (AAPL total assets 11022)
    row = fx.fact(11022)
    assert row["period_start"] is None
    assert duration_class(None, row["period_end"]) == "instant"
    assert duration_class(None, dt.date(2022, 9, 25)) == "instant"


_starts = st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31))


@pytest.mark.criterion("C1")
@given(start=_starts, days=st.integers(min_value=1, max_value=4000))
def test_class_is_the_band_of_the_day_count(start, days):
    # oracle: the spec's band table, stated in this test (BANDS)
    end = start + dt.timedelta(days=days)
    assert duration_class(start, end) == _band_kind(days)
    assert duration_class(start.isoformat(), end.isoformat()) == _band_kind(days)


@pytest.mark.criterion("C1")
@given(kind=st.sampled_from(sorted(BANDS)), data=st.data(), start=_starts)
def test_inside_a_band_the_class_is_fsds_rounded_quarters(kind, data, start):
    # oracle: FSDS qtrs = round(days * 4 / 365.25) for every length inside a band
    lo, hi = BANDS[kind]
    days = data.draw(st.integers(min_value=lo, max_value=hi))
    assert QTRS_KIND[round(days * 4 / 365.25)] == kind
    assert duration_class(start, start + dt.timedelta(days=days)) == kind


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(start=_starts, days=st.integers(min_value=1, max_value=4000),
       shift=st.integers(min_value=-3000, max_value=3000))
def test_shifting_both_dates_keeps_the_class(start, days, shift):
    # oracle: the class depends on the length only (R1)
    end = start + dt.timedelta(days=days)
    moved = dt.timedelta(days=shift)
    assert duration_class(start + moved, end + moved) == duration_class(start, end)
