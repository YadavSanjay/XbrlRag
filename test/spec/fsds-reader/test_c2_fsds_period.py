"""C2: ``fsds_period`` encodes an XBRL period as SEC's ``(ddate, qtrs)``.

Oracle: SEC readme, num table — ddate "The end date for the data value, rounded to the
nearest month end"; qtrs "The count of the number of quarters represented by the data
value, rounded to the nearest whole number. '0' indicates it is a point-in-time value."
Point cases are real fiscal periods (dates from each filing's rendered statement headers
and XBRL contexts) whose (ddate, qtrs) SEC itself wrote into the committed num rows.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_reference.fsds import fsds_period

# (accession, start, end, SEC ddate, SEC qtrs). The period dates are the filings' own:
# AAPL FY2023 is the 53 weeks ended 2023-09-30; COST FY2023 the 53 weeks ended 2023-09-03
# ("53 weeks ended September 3, 2023" in its income statement header); AVGO fiscal 2024
# ended 2024-11-03 (53 weeks); MSFT's year ends June 30, ORCL's May 31, TSM's December 31.
REAL = [
    ("AAPL_24", "2023-10-01", "2024-09-28", "2024-09-30", 4),
    ("AAPL_24", "2022-09-25", "2023-09-30", "2023-09-30", 4),
    ("AAPL_24", "2021-09-26", "2022-09-24", "2022-09-30", 4),
    ("COST_23", "2022-08-29", "2023-09-03", "2023-08-31", 4),
    ("COST_23", "2021-08-30", "2022-08-28", "2022-08-31", 4),
    ("COST_23", "2020-08-31", "2021-08-29", "2021-08-31", 4),
    ("AVGO_24", "2023-10-30", "2024-11-03", "2024-10-31", 4),
    ("AVGO_24", "2022-10-31", "2023-10-29", "2023-10-31", 4),
    ("AVGO_24", "2021-11-01", "2022-10-30", "2022-10-31", 4),
    ("MSFT_24", "2023-07-01", "2024-06-30", "2024-06-30", 4),
    ("ORCL_25", "2024-06-01", "2025-05-31", "2025-05-31", 4),
    ("TSM_24", "2024-01-01", "2024-12-31", "2024-12-31", 4),
    ("CEG_23", None, "2023-12-31", "2023-12-31", 0),
]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("acc", "start", "end", "ddate", "qtrs"), REAL)
def test_real_periods_encode_as_sec_wrote_them(kit, acc, start, end, ddate, qtrs):
    # oracle: SEC's own num rows for the accession carry exactly this (ddate, qtrs)
    adsh = getattr(kit, acc)
    sec_pairs = {(r["ddate"], r["qtrs"]) for r in kit.raw_rows("num") if r["adsh"] == adsh}
    assert (ddate.replace("-", ""), str(qtrs)) in sec_pairs
    assert fsds_period(start, end) == (ddate, qtrs)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("end", ["2024-04-15", "2023-02-14", "2024-06-15"])
def test_exact_tie_between_two_month_ends_raises(end):
    # oracle: hand count — 2024-04-15 is 15 days after 03-31 and 15 before 04-30; 2023-02-14 is
    # 14 days from 01-31 and from 02-28; 2024-06-15 is 15 days from 05-31 and from 06-30.
    with pytest.raises(ValueError):
        fsds_period(None, end)


@pytest.mark.criterion("C2")
def test_reversed_period_raises():
    # oracle: requirement 6 — a start after the end is rejected
    with pytest.raises(ValueError):
        fsds_period("2024-10-01", "2024-09-28")


def _month_end(d: dt.date) -> dt.date:
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


@pytest.mark.criterion("C2")
@given(end=st.dates(min_value=dt.date(1995, 1, 1), max_value=dt.date(2045, 12, 31)))
def test_ddate_is_the_nearest_month_end(end):
    # oracle: readme ddate definition — the candidates are this month's and last month's ends
    this_end = _month_end(end)
    prev_end = end.replace(day=1) - dt.timedelta(days=1)
    up, down = (this_end - end).days, (end - prev_end).days
    if up == down:
        with pytest.raises(ValueError):
            fsds_period(None, end.isoformat())
        return
    ddate, qtrs = fsds_period(None, end.isoformat())
    expected = this_end if up < down else prev_end
    assert ddate == expected.isoformat()
    assert qtrs == 0  # readme: 0 is a point-in-time value
    got = dt.date.fromisoformat(ddate)
    assert _month_end(got) == got
    assert abs((got - end).days) <= 15


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    end=st.dates(min_value=dt.date(1995, 1, 1), max_value=dt.date(2045, 12, 31)),
    weeks=st.sampled_from([(13, 1), (14, 1), (26, 2), (27, 2), (39, 3), (40, 3), (52, 4), (53, 4)]),
)
def test_week_based_periods_round_to_whole_quarters(end, weeks):
    # oracle: readme qtrs "rounded to the nearest whole number"; a 52/53-week year is 4
    # quarters, 13/14 weeks 1, 26/27 weeks 2, 39/40 weeks 3 (hand: 371 d * 4 / 365.25 = 4.06).
    n_weeks, quarters = weeks
    start = end - dt.timedelta(days=7 * n_weeks - 1)
    instant = None
    try:
        instant = fsds_period(None, end.isoformat())
    except ValueError:
        with pytest.raises(ValueError):
            fsds_period(start.isoformat(), end.isoformat())
        return
    ddate, qtrs = fsds_period(start.isoformat(), end.isoformat())
    assert qtrs == quarters
    assert ddate == instant[0]  # the duration's ddate is its end's month end
