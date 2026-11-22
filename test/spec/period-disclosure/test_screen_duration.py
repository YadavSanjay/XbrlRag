"""Spec tests for period-disclosure C2: segment and geographic screens pin the duration.

docs/specs/period-disclosure.md, requirement R2. The database is faked at `psycopg2.connect`
(conftest.py) with the views' stored rows; the screen, its SQL text, its ranking and its
disclosure run for real. Expected values come from the filings' iXBRL contexts and FSDS.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_screen import ScreenStatus

US = {"geography": "united states"}


def _ranked(result) -> list[tuple]:
    return [(r["ticker"], r["value"], r["fact_id"]) for r in result.rows]


@pytest.mark.criterion("C2")
def test_q2_geographic_screen_ranks_the_quarter(fx):
    # oracle: GOOGL f-599 (2026-04-01..06-30, 60,846M) and TSLA f-1188 (2026-04-01..06-30,
    # 13,208M) are the second quarter; f-603 / f-1190 (from 2026-01-01) are the half year
    googl = fx.ix_fact("0001652044-26-000071", "f-599")
    tsla = fx.ix_fact("0001628280-26-049270", "f-1188")
    assert (googl["start"], tsla["start"]) == ("2026-04-01", "2026-04-01")
    result = fx.run_screen(US, "Revenue", 2026, "Q2")
    assert result.status is ScreenStatus.OK
    assert _ranked(result) == [("GOOGL", float(googl["value"]), "101632"),
                               ("TSLA", float(tsla["value"]), "111045")]
    assert {r["value"] for r in result.rows}.isdisjoint({114821000000.0, 23885000000.0})
    # with ytd: f-603 and f-1190, contexts 2026-01-01..2026-06-30 (six months)
    googl_h = fx.ix_fact("0001652044-26-000071", "f-603")
    tsla_h = fx.ix_fact("0001628280-26-049270", "f-1190")
    assert (googl_h["start"], tsla_h["start"]) == ("2026-01-01", "2026-01-01")
    ytd = fx.run_screen(US, "Revenue", 2026, "Q2", ytd=True)
    assert ytd.status is ScreenStatus.OK
    assert _ranked(ytd) == [("GOOGL", float(googl_h["value"]), "101636"),
                            ("TSLA", float(tsla_h["value"]), "111047")]


@pytest.mark.criterion("C2")
def test_q3_geographic_screen_pins_quarter_or_nine_months(fx):
    # oracle: FSDS num for 0001193125-26-191507, Geographical=US at 2026-03-31:
    # qtrs 1 = 42,336,000,000 (the quarter), qtrs 3 = 123,823,000,000 (nine months)
    for ytd, qtrs, fact_id in ((False, 1, "52120"), (True, 3, "52122")):
        expected = fx.fsds_value("0001193125-26-191507",
                                 "RevenueFromContractWithCustomerExcludingAssessedTax",
                                 "20260331", qtrs, "Geographical=US;")
        result = fx.run_screen(US, "Revenue", 2026, "Q3", ytd=ytd)
        assert result.status is ScreenStatus.OK
        assert _ranked(result) == [("MSFT", float(expected), fact_id)]


@pytest.mark.criterion("C2")
def test_fy_segment_screen_never_ranks_a_quarter(fx):
    # oracle: iXBRL f-906 of mcd-20231231.htm: "Other Restructuring" 35.0 (scale 6) has
    # context 2023-10-01..2023-12-31, a quarter; no annual value for that member is filed,
    # so a FY2023 screen has nothing to rank
    ix = fx.ix_fact("0000063908-24-000072", "f-906")
    assert (ix["start"], ix["end"], ix["value"]) == ("2023-10-01", "2023-12-31", 35000000)
    result = fx.run_screen({"segment": "other restructuring"}, "RestructuringCharges", 2023, "FY")
    assert result.status is ScreenStatus.NOT_FOUND
    assert result.rows == []
    assert "MCD" not in (result.disclosure or {}).get("ranked", [])
    # control: MCD FY2023 "U.S. Market" restructuring 62,400,000 is an annual row of the
    # stored view (fact 215844, duration_kind annual), so it still ranks
    row = next(r for r in fx.SEG_ROWS if r["fact_id"] == 215844)
    assert row["duration_kind"] == "annual"
    annual = fx.run_screen({"segment": "u.s. market"}, "RestructuringCharges", 2023, "FY")
    assert annual.status is ScreenStatus.OK
    assert _ranked(annual) == [("MCD", 62400000.0, "215844")]


TICKERS = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]
OTHER = {"FY": ["quarter", "half", "nine_month", "other"],
         "Q2": ["half", "annual", "nine_month", "other"],
         "Q3": ["nine_month", "half", "annual", "other"]}
REQUIRED = {"FY": "annual", "Q2": "quarter", "Q3": "quarter"}


def _geo_row(ticker: str, fp: str, kind: str, value: int, fact_id: int) -> dict:
    from decimal import Decimal

    return {"ticker": ticker, "cik": f"{TICKERS.index(ticker) + 1:010d}", "fiscal_year": 2026,
            "fiscal_period": fp, "duration_kind": kind, "concept_family": "Revenue",
            "value": Decimal(value), "unit": "USD", "pct_of_total": None, "geo_code": "US",
            "geo_name": None, "section_path": "Revenue by geography", "fact_id": fact_id}


@st.composite
def _screen_case(draw):
    """Valued rows of the required kind, plus distractor rows of other kinds."""
    fp = draw(st.sampled_from(sorted(REQUIRED)))
    tickers = draw(st.lists(st.sampled_from(TICKERS), min_size=1, max_size=len(TICKERS),
                            unique=True))
    values = draw(st.lists(st.integers(min_value=1, max_value=10**12), min_size=len(tickers),
                           max_size=len(tickers), unique=True))
    base = [_geo_row(t, fp, REQUIRED[fp], v, 1000 + i)
            for i, (t, v) in enumerate(zip(tickers, values, strict=True))]
    n = draw(st.integers(min_value=1, max_value=8))
    distractors = [
        _geo_row(draw(st.sampled_from(TICKERS)), fp, draw(st.sampled_from(OTHER[fp])),
                 draw(st.integers(min_value=1, max_value=10**13)), 5000 + i)
        for i in range(n)
    ]
    order = draw(st.permutations(base + distractors))
    return fp, base, list(order)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(case=_screen_case(), direction=st.sampled_from(["asc", "desc"]))
def test_other_durations_never_change_the_ranking(fx, case, direction):
    # oracle: R2 by hand: only rows of the requested kind are ranked, so adding rows of any
    # other kind (with any value, in any order) gives the ranking of the base rows
    fp, base, mixed = case
    expected = sorted(((r["ticker"], float(r["value"]), str(r["fact_id"])) for r in base),
                      key=lambda x: x[1], reverse=direction == "desc")
    alone = fx.run_screen(US, "Revenue", 2026, fp, geo=base, seg=[], order=direction)
    with_others = fx.run_screen(US, "Revenue", 2026, fp, geo=mixed, seg=[], order=direction)
    assert _ranked(alone) == expected
    assert _ranked(with_others) == expected
