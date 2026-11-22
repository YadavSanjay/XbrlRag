"""C2, C3: one date matched to the filer's calendar (docs/specs/fiscal-year-ended-date.md, req. 2).

Oracle: Home Depot's public period ends and labels (conftest.py); the generated calendars' own
labels; requirement 2's outcomes, by hand.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.fiscal_date import match_period_end

KEYS = {"status", "fiscal_year", "fiscal_period", "matched_end", "reason"}


def _m(end, cal, period, quarter=None, **kw):
    got = match_period_end(end, cal, period, quarter, **kw)
    assert isinstance(got, dict) and set(got) == KEYS, got
    return got


def _ok(fy, fp, matched_end):
    return {"status": "matched", "fiscal_year": fy, "fiscal_period": fp,
            "matched_end": matched_end, "reason": None}


def _not(result, status, end):
    assert result["status"] == status, result
    assert result["fiscal_year"] is None and result["fiscal_period"] is None, result
    assert result["matched_end"] is None, result
    assert isinstance(result["reason"], str) and end.isoformat() in result["reason"], result


# ------------------------------------------------------------------ C2: the label, by date


@pytest.mark.criterion("C2")
def test_home_depot_end_dates_take_its_own_labels(kit):
    # oracle: Home Depot's 10-K covers — fiscal 2023/2024/2025 ended 2024-01-28 / 2025-02-02 /
    # 2026-02-01 (the C9 silent-wrong read 2025-02-02 as 2025)
    assert _m(date(2025, 2, 2), kit.HD, "FY") == _ok(2024, "FY", date(2025, 2, 2))
    assert _m(date(2024, 1, 28), kit.HD, "FY") == _ok(2023, "FY", date(2024, 1, 28))
    assert _m(date(2026, 2, 1), kit.HD, "FY") == _ok(2025, "FY", date(2026, 2, 1))


@pytest.mark.criterion("C2")
def test_seven_days_either_side_match_and_eight_do_not(kit):
    # oracle: requirement 2, tolerance 7 days inclusive — "January 31, 2025" (the nominal month
    # end, 2 days early) is fiscal 2024; 7 days off still matches, 8 does not
    end = date(2025, 2, 2)
    assert _m(date(2025, 1, 31), kit.HD, "FY") == _ok(2024, "FY", end)
    assert _m(end - timedelta(days=7), kit.HD, "FY") == _ok(2024, "FY", end)
    assert _m(end + timedelta(days=7), kit.HD, "FY") == _ok(2024, "FY", end)
    _not(_m(end - timedelta(days=8), kit.HD, "FY"), "not_found", end - timedelta(days=8))
    _not(_m(end + timedelta(days=8), kit.HD, "FY"), "not_found", end + timedelta(days=8))
    # a narrower tolerance passed in is honoured
    _not(_m(date(2025, 1, 31), kit.HD, "FY", tolerance_days=1), "not_found", date(2025, 1, 31))


@pytest.mark.criterion("C2")
def test_a_filer_labelled_by_end_year_keeps_the_end_year(kit):
    # oracle: by hand — a December filer's fiscal 2024 ends 2024-12-31
    cal = [kit.row(2023, "FY", date(2023, 12, 31)), kit.row(2024, "FY", date(2024, 12, 31))]
    assert _m(date(2024, 12, 31), cal, "FY") == _ok(2024, "FY", date(2024, 12, 31))
    assert _m(date(2023, 12, 31), cal, "any") == _ok(2023, "FY", date(2023, 12, 31))


@pytest.mark.criterion("C2")
@given(data=st.data())
def test_generated_calendars_every_end_and_shift(kit, data):
    # oracle: the generated calendar's own labels (conftest build_calendar); 0-7 days off is
    # the same year, 8-40 days off is none (consecutive annual ends are >= 364 days apart)
    rows, annual, _quarters = data.draw(kit.calendars)
    end, label = data.draw(st.sampled_from(sorted(annual.items())))
    near = data.draw(st.integers(-7, 7))
    assert _m(end + timedelta(days=near), rows, "FY") == _ok(label, "FY", end)
    far = data.draw(st.integers(8, 40)) * data.draw(st.sampled_from([-1, 1]))
    _not(_m(end + timedelta(days=far), rows, "FY"), "not_found", end + timedelta(days=far))


# ------------------------------------------------------------------ C3: defined outcomes


@pytest.mark.criterion("C3")
def test_no_calendar(kit):
    # oracle: requirement 2 — no usable row is no_calendar (a row of another period kind, e.g.
    # "H1", is not usable); one real row then matches
    d = date(2025, 2, 2)
    _not(_m(d, [], "FY"), "no_calendar", d)
    _not(_m(d, [kit.row(2024, "H1", d)], "any"), "no_calendar", d)
    assert _m(d, [kit.row(2024, "FY", d)], "FY") == _ok(2024, "FY", d)


@pytest.mark.criterion("C3")
def test_two_annual_rows_within_tolerance(kit):
    # oracle: requirement 2, by hand — a transition: fiscal 2024 ends 2025-01-31 and a short
    # fiscal 2025 ends 2025-02-05; the exact date picks, a date between them ties, and two
    # labels on one end date tie too
    cal = [kit.row(2024, "FY", date(2025, 1, 31)), kit.row(2025, "FY", date(2025, 2, 5))]
    assert _m(date(2025, 1, 31), cal, "FY") == _ok(2024, "FY", date(2025, 1, 31))
    assert _m(date(2025, 2, 5), cal, "FY") == _ok(2025, "FY", date(2025, 2, 5))
    _not(_m(date(2025, 2, 2), cal, "FY"), "ambiguous", date(2025, 2, 2))
    _not(_m(date(2025, 2, 3), cal, "any"), "ambiguous", date(2025, 2, 3))
    clash = [kit.row(2024, "FY", date(2025, 2, 2)), kit.row(2025, "FY", date(2025, 2, 2))]
    _not(_m(date(2025, 2, 2), clash, "FY"), "ambiguous", date(2025, 2, 2))


@pytest.mark.criterion("C3")
def test_quarter_and_annual(kit):
    # oracle: requirement 2, by hand, on Home Depot's rows — 2025-05-04 ends Q1 of fiscal 2025
    q1 = date(2025, 5, 4)
    _not(_m(q1, kit.HD, "FY"), "ambiguous", q1)                  # a quarter end, asked as a year
    assert _m(q1, kit.HD, "quarter") == _ok(2025, "Q1", q1)
    assert _m(q1, kit.HD, "quarter", "Q1") == _ok(2025, "Q1", q1)
    _not(_m(q1, kit.HD, "quarter", "Q2"), "ambiguous", q1)       # named quarter differs
    assert _m(q1, kit.HD, "any") == _ok(2025, "Q1", q1)
    fye = date(2026, 2, 1)
    assert _m(fye, kit.HD, "quarter") == _ok(2025, "Q4", fye)    # the quarter ending the year
    assert _m(fye, kit.HD, "quarter", "Q1") == _ok(2025, "Q1", fye)  # "Q1 of the FY ended"
    assert _m(fye, kit.HD, "any") == _ok(2025, "FY", fye)        # annual first
    _not(_m(date(2025, 8, 20), kit.HD, "quarter"), "not_found", date(2025, 8, 20))
    _not(_m(date(2025, 8, 20), kit.HD, "any"), "not_found", date(2025, 8, 20))


@pytest.mark.criterion("C3")
def test_a_quarter_row_on_the_year_end_is_preferred_for_a_quarter(kit):
    # oracle: requirement 2 — with a Q4 row on the year end, "quarter" takes the quarter row and
    # "FY" / "any" the annual row; a quarter tie is ambiguous
    d = date(2024, 12, 31)
    cal = [kit.row(2024, "FY", d), kit.row(2024, "Q4", d)]
    assert _m(d, cal, "quarter") == _ok(2024, "Q4", d)
    assert _m(d, cal, "FY") == _ok(2024, "FY", d)
    assert _m(d, cal, "any") == _ok(2024, "FY", d)
    ties = [kit.row(2025, "Q1", date(2025, 3, 29)), kit.row(2025, "Q2", date(2025, 4, 2))]
    _not(_m(date(2025, 3, 31), ties, "quarter"), "ambiguous", date(2025, 3, 31))


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(data=st.data())
def test_order_duplicates_and_far_rows_change_nothing(kit, data):
    # oracle: requirement 2 — rows count once and their order does not matter; a row more than
    # 7 days from the date is no candidate. The expected value is the generated label.
    rows, annual, quarters = data.draw(kit.calendars)
    period = data.draw(st.sampled_from(["FY", "quarter", "any"]))
    if period == "FY" or not quarters:
        end, label = data.draw(st.sampled_from(sorted(annual.items())))
        expected = _ok(label, "FY" if period != "quarter" else "Q4", end)
    else:
        end, label, n = data.draw(st.sampled_from(quarters))
        expected = _ok(label, n, end)
    seed = data.draw(st.integers(0, 2**32 - 1))
    shuffled = list(rows) + [dict(r) for r in rows[: data.draw(st.integers(0, len(rows)))]]
    random.Random(seed).shuffle(shuffled)
    far = data.draw(st.integers(8, 40))
    shuffled.append(kit.row(1999, data.draw(st.sampled_from(["FY", "Q2"])), end + timedelta(far)))
    assert _m(end, rows, period) == expected
    assert _m(end, shuffled, period) == expected
