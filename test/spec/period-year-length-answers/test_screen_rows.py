"""Spec tests for period-year-length-answers C4: a growth screen names the members whose two
compared years differ in length (docs/specs/period-year-length-answers.md, Requirement 3).

Rows are shaped like `XbrlFactstoreScreen._query_derived` rows of `RevenueGrowthPct`
(`mv_derived_metrics.component_fact_ids`); periods are period-disclosure C3's.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.year_length_answers import screen_year_lengths

ENTRY_KEYS = {"ticker", "fact_ids", "days_current", "days_prior", "current_period",
              "prior_period", "note"}


def _row(ticker, cur, pri):
    return {"ticker": ticker, "value": 0.02, "fact_id": f"RevenueGrowthPct:1:{ticker}",
            "component_fact_ids": {"revenue_current": cur, "revenue_prior": pri}}


@pytest.mark.criterion("C4")
def test_only_aapl_is_named(kit):
    # oracle: C3 periods by hand: AAPL 364 against 371 (7 apart); MSFT 365 and 365; the other
    # rows have no comparable pair (Requirement 3)
    rows = [
        _row("MSFT", 105742, 104052),
        _row("AAPL", 14262, 12096),
        _row("GONE", 14262, 777),  # prior fact not in periods
        {"ticker": "LIST", "component_fact_ids": {"revenue_current": [14262],
                                                  "revenue_prior": 12096}},
        {"ticker": "TWO", "component_fact_ids": {"revenue_current": 14262,
                                                 "net_income_current": 14262,
                                                 "revenue_prior": 12096}},
        {"ticker": "NONE", "component_fact_ids": None},
    ]
    out = screen_year_lengths(rows, kit.period_map())
    assert len(out) == 1
    e = out[0]
    assert set(e) == ENTRY_KEYS
    assert e["ticker"] == "AAPL"
    assert list(e["fact_ids"]) == [14262, 12096]
    assert (e["days_current"], e["days_prior"]) == (364, 371)
    assert tuple(e["current_period"]) == ("2023-10-01", "2024-09-28")
    assert tuple(e["prior_period"]) == ("2022-09-25", "2023-09-30")
    assert "53-week" in e["note"]


@pytest.mark.criterion("C4")
def test_digit_string_ids_read_the_same(kit):
    # oracle: Requirement 3, an id is an int or a string of digits; jsonb may give either
    out = screen_year_lengths([_row("AAPL", "14262", "12096"), _row("BOOL", True, 12096)],
                              kit.period_map())
    assert [(e["ticker"], list(e["fact_ids"])) for e in out] == [("AAPL", [14262, 12096])]


@pytest.mark.criterion("C4")
@given(lengths=st.lists(st.tuples(st.integers(340, 390), st.integers(340, 390)),
                        min_size=1, max_size=8),
       start=st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31)))
def test_entry_exactly_when_six_or_more_apart_in_row_order(lengths, start):
    # oracle: period-disclosure R3 rule worked here; entries keep the rows' order
    rows, periods, expected = [], {}, []
    for i, (dc, dp) in enumerate(lengths):
        cur_id, pri_id = 2 * i + 1, 2 * i + 2
        prior_start = start + dt.timedelta(days=400 * i)
        cur_start = prior_start + dt.timedelta(days=dp)
        periods[cur_id] = {"period_start": cur_start.isoformat(),
                           "period_end": (cur_start + dt.timedelta(days=dc)).isoformat()}
        periods[pri_id] = {"period_start": prior_start.isoformat(),
                           "period_end": cur_start.isoformat()}
        rows.append(_row(f"T{i}", cur_id, pri_id))
        if abs(dc - dp) >= 6:
            expected.append((f"T{i}", dc, dp))
    out = screen_year_lengths(rows, periods)
    assert [(e["ticker"], e["days_current"], e["days_prior"]) for e in out] == expected


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(slots=st.lists(st.integers(0, 10), max_size=5))
def test_rows_without_a_pair_never_change_the_result(kit, slots):
    # oracle: Requirement 3, "any other row adds nothing"
    base = [_row("MSFT", 105742, 104052), _row("AAPL", 14262, 12096)]
    expected = screen_year_lengths(base, kit.period_map())
    assert [e["ticker"] for e in expected] == ["AAPL"]
    junk = [_row("GONE", 14262, 777), {"ticker": "NONE", "component_fact_ids": None},
            {"ticker": "ONE", "component_fact_ids": {"revenue_current": 14262}},
            _row("MSFT2", 105742, 104052), _row("BOOL", False, 12096)]
    rows = list(base)
    for i, slot in enumerate(slots):
        rows.insert(slot % (len(rows) + 1), junk[i % len(junk)])
    assert screen_year_lengths(rows, kit.period_map()) == expected
