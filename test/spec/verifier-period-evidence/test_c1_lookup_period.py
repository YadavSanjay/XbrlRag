"""C1: a lookup's period (Requirement 1)."""

from __future__ import annotations

import copy
import datetime as dt

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.retriever.xbrl.step_period import lookup_period

KEYS = {"fiscal_year", "fiscal_period", "ytd", "period_start", "period_end"}


@pytest.mark.criterion("C1")
def test_apple_fy2025_current_assets_instant(vpe):
    # oracle: the spec's C1 by hand: plan fy 2025, no fiscal_period -> "FY"; an instant has no
    # start; stored end 2025-09-28 is stated as 2025-09-27 (Apple's balance-sheet date)
    out = lookup_period(vpe.ca_plan_step(), None, "2025-09-28")
    assert out == {"fiscal_year": 2025, "fiscal_period": "FY", "ytd": False,
                   "period_start": None, "period_end": "2025-09-27"}
    assert out == vpe.CA_PERIOD


@pytest.mark.criterion("C1")
def test_duration_keeps_start_and_states_end():
    # oracle: Requirement 1 by hand: start passed through; stored end 2023-10-01 minus one
    # day is 2023-09-30
    plan = {"id": "rev", "op": "lookup", "ticker": "AAPL", "metric": "Revenue", "fy": 2023}
    out = lookup_period(plan, "2022-09-25", "2023-10-01")
    assert out == {"fiscal_year": 2023, "fiscal_period": "FY", "ytd": False,
                   "period_start": "2022-09-25", "period_end": "2023-09-30"}


@pytest.mark.criterion("C1")
def test_q2_ytd_keeps_fiscal_period_and_ytd():
    # oracle: Requirement 1 by hand: fiscal_period "Q2" kept, ytd True; stored end 2024-03-31
    # is stated as 2024-03-30
    plan = {"id": "rev", "op": "lookup", "ticker": "AAPL", "metric": "Revenue", "fy": 2024,
            "fiscal_period": "Q2", "ytd": True}
    out = lookup_period(plan, "2023-10-01", "2024-03-31")
    assert out == {"fiscal_year": 2024, "fiscal_period": "Q2", "ytd": True,
                   "period_start": "2023-10-01", "period_end": "2024-03-30"}


@pytest.mark.criterion("C1")
def test_odd_plan_values_fall_back():
    # oracle: Requirement 1 by hand: a bool fy is not a year; "" is not a fiscal period
    # ("FY"); a truthy ytd that is not True is False; month and leap-day boundaries by hand
    plan = {"id": "x", "op": "lookup", "fy": True, "fiscal_period": "", "ytd": "yes"}
    out = lookup_period(plan, "2023-03-01", "2024-03-01")
    assert out == {"fiscal_year": None, "fiscal_period": "FY", "ytd": False,
                   "period_start": "2023-03-01", "period_end": "2024-02-29"}
    year_end = lookup_period({"fy": 2023}, None, "2024-01-01")
    assert year_end is not None and year_end["period_end"] == "2023-12-31"


@pytest.mark.criterion("C1")
def test_none_only_when_nothing_is_known():
    # oracle: Requirement 1 by hand: None exactly when fiscal_year, start and end are all None
    assert lookup_period({"id": "x", "op": "lookup", "fy": None}, None, None) is None
    assert lookup_period({"id": "x", "op": "lookup", "fy": False}, None, None) is None
    assert lookup_period({"id": "x", "op": "lookup"}, None, None) is None
    out = lookup_period({"id": "x", "op": "lookup", "fy": None}, None, "2025-09-28")
    assert out == {"fiscal_year": None, "fiscal_period": "FY", "ytd": False,
                   "period_start": None, "period_end": "2025-09-27"}
    year_only = lookup_period({"id": "x", "op": "lookup", "fy": 2025}, None, None)
    assert year_only == {"fiscal_year": 2025, "fiscal_period": "FY", "ytd": False,
                         "period_start": None, "period_end": None}


@pytest.mark.criterion("C1")
def test_date_objects_are_accepted(vpe):
    # oracle: Requirement 1 by hand: a datetime.date start is written ISO; end 2025-09-28
    # minus one day is 2025-09-27
    out = lookup_period(vpe.ca_plan_step(), dt.date(2024, 9, 29), dt.date(2025, 9, 28))
    assert out == {"fiscal_year": 2025, "fiscal_period": "FY", "ytd": False,
                   "period_start": "2024-09-29", "period_end": "2025-09-27"}
    assert set(out) == KEYS


_dates = st.dates(min_value=dt.date(1990, 1, 2), max_value=dt.date(2100, 12, 31))
_plans = st.fixed_dictionaries(
    {"id": st.just("x"), "op": st.just("lookup")},
    optional={"fy": st.one_of(st.none(), st.booleans(), st.integers(1990, 2100), st.text()),
              "fiscal_period": st.one_of(st.none(), st.sampled_from(["", "FY", "Q1", "Q3"])),
              "ytd": st.one_of(st.none(), st.booleans(), st.just("yes"))})


@pytest.mark.criterion("C1")
@settings(max_examples=60)
@given(end=_dates, start=st.one_of(st.none(), _dates), as_str=st.booleans(), plan=_plans)
def test_end_is_one_day_before_and_plan_is_untouched(end, start, as_str, plan):
    # oracle: the one-day stated-end rule as a closed form (end - 1 day, ISO); Requirement 1:
    # the plan step is never mutated, the keys are exactly the five
    before = copy.deepcopy(plan)
    stored_end = end.isoformat() if as_str else end
    stored_start = start.isoformat() if (as_str and start is not None) else start
    out = lookup_period(plan, stored_start, stored_end)
    assert plan == before
    assert out is not None and set(out) == KEYS
    assert out["period_end"] == (end - dt.timedelta(days=1)).isoformat()
    assert out["period_start"] == (None if start is None else start.isoformat())
    fy = plan.get("fy")
    assert out["fiscal_year"] == (fy if isinstance(fy, int) and not isinstance(fy, bool)
                                  else None)
    assert out["ytd"] is (plan.get("ytd") is True)
