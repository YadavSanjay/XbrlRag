"""C3: an aggregate's period, from the screen it aggregates (Requirement 3)."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.retriever.xbrl.step_period import aggregate_period


@pytest.mark.criterion("C3")
def test_semiconductor_median_fiscal_2023(vpe):
    # oracle: the spec's C3 by hand: the screen's disclosure fiscal year 2023, the seven
    # stated period ends of the aggregate, ends differ
    out = aggregate_period(vpe.ca_screen(), vpe.ca_median_aggregate())
    assert out == {"fiscal_year": 2023,
                   "period_ends": {"INTC": "2023-12-30", "NVDA": "2023-01-29",
                                   "QCOM": "2023-09-24", "MU": "2023-08-31",
                                   "AVGO": "2023-10-29", "AMD": "2023-12-30",
                                   "TXN": "2023-12-31"},
                   "period_ends_differ": True}
    assert out == vpe.CA_MEDIAN_PERIOD


@pytest.mark.criterion("C3")
def test_fiscal_year_from_rows_without_disclosure(vpe):
    # oracle: Requirement 3 by hand: no disclosure, every row fiscal_year 2023 -> 2023
    screen = vpe.ca_screen()
    del screen["disclosure"]
    out = aggregate_period(screen, vpe.ca_median_aggregate())
    assert out == vpe.CA_MEDIAN_PERIOD


@pytest.mark.criterion("C3")
def test_bool_disclosure_year_falls_back_to_rows(vpe):
    # oracle: Requirement 3 by hand: a bool is not a year, so the rows' common 2023 is used
    screen = vpe.ca_screen()
    screen["disclosure"]["fiscal_year"] = True
    out = aggregate_period(screen, vpe.ca_median_aggregate())
    assert out is not None and out["fiscal_year"] == 2023


@pytest.mark.criterion("C3")
def test_mixed_row_years_give_no_fiscal_year(vpe):
    # oracle: Requirement 3 by hand: no disclosure and rows of 2023 and 2022 -> None; the
    # period ends still come from the aggregate
    screen = vpe.ca_screen()
    screen["disclosure"] = None
    screen["rows"][0]["fiscal_year"] = 2022
    out = aggregate_period(screen, vpe.ca_median_aggregate())
    assert out == {"fiscal_year": None, "period_ends": dict(vpe.CA_ENDS),
                   "period_ends_differ": True}


@pytest.mark.criterion("C3")
def test_missing_period_ends_and_non_screens(vpe):
    # oracle: Requirement 3 by hand: absent period_ends -> {}, absent differ -> False; a
    # lookup step or None as screen_step -> None
    agg = vpe.ca_median_aggregate()
    del agg["period_ends"]
    del agg["period_ends_differ"]
    assert aggregate_period(vpe.ca_screen(), agg) == {"fiscal_year": 2023, "period_ends": {},
                                                      "period_ends_differ": False}
    nulls = vpe.ca_median_aggregate()
    nulls["period_ends"] = None
    nulls["period_ends_differ"] = None
    assert aggregate_period(vpe.ca_screen(), nulls) == {"fiscal_year": 2023, "period_ends": {},
                                                        "period_ends_differ": False}
    assert aggregate_period(vpe.ca_step(), vpe.ca_median_aggregate()) is None
    assert aggregate_period(None, vpe.ca_median_aggregate()) is None


_ends = st.dictionaries(st.sampled_from(["INTC", "NVDA", "QCOM", "MU", "AVGO", "AMD", "TXN"]),
                        st.dates().map(lambda d: d.isoformat()), max_size=7)


@pytest.mark.criterion("C3")
@settings(max_examples=50)
@given(ends=_ends, differ=st.one_of(st.none(), st.booleans(), st.integers(0, 2)),
       year=st.integers(1990, 2100))
def test_result_never_aliases_its_inputs(vpe, ends, differ, year):
    # oracle: Requirement 3: period_ends is a dict copy (equal, not the same object),
    # period_ends_differ is bool(...); mutating the result leaves both inputs unchanged
    screen = vpe.ca_screen()
    screen["disclosure"]["fiscal_year"] = year
    agg = vpe.ca_median_aggregate()
    agg["period_ends"] = ends
    agg["period_ends_differ"] = differ
    screen_before, agg_before = copy.deepcopy(screen), copy.deepcopy(agg)
    out = aggregate_period(screen, agg)
    assert out is not None
    assert out == {"fiscal_year": year, "period_ends": ends, "period_ends_differ": bool(differ)}
    assert out["period_ends"] is not ends
    out["period_ends"]["ZZZ"] = "1900-01-01"
    out["fiscal_year"] = 1900
    assert screen == screen_before and agg == agg_before
