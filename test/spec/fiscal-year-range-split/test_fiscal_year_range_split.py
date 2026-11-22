"""Spec tests for fiscal-year-range-split (docs/specs/fiscal-year-range-split.md).

C1, C2: the fiscal-year ranges a question states; C3, C4: the middle years a split range gets
back, and only those.

Oracle: the question text read by hand, the resolved years written in each test, and for dated
endpoints the synthetic calendar rows below, read by hand. Everything here is synthetic: a
December-31 filer (fiscal year N ends December 31 of N) and a 52/53-week filer whose label is the
year its fiscal year starts (fiscal 2022 ends 2023-01-29, the shape of the Home Depot rows of
docs/specs/fiscal-year-ended-date.md). The three evidence questions are quoted from the dev
suite. No test reads ``data/``, the database or the clock.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.fiscal_date import missing_range_years, stated_fiscal_year_ranges

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def row(fiscal_year: int, fiscal_period: str, period_end: date) -> dict:
    """One fiscal_calendar row as a RealDictCursor returns it."""
    return {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "period_end": period_end}


# A December-31 filer: fiscal 2021..2025, each ending December 31 of its own year.
DEC31 = [row(y, "FY", date(y, 12, 31)) for y in range(2021, 2026)]

# A 52/53-week filer labelled by the year its fiscal year starts; its first quarter of fiscal
# 2025 ends 2025-05-04 (a quarter end, no fiscal-year end).
WEEKS = [
    row(2022, "FY", date(2023, 1, 29)),
    row(2023, "FY", date(2024, 1, 28)),
    row(2024, "FY", date(2025, 2, 2)),
    row(2025, "FY", date(2026, 2, 1)),
    row(2025, "Q1", date(2025, 5, 4)),
]

# The evidence questions (dev suite), verbatim.
UNP_Q = "Union Pacific Corporation shares outstanding trend, FY23 through FY25"
TESLA_Q = ("Tesla NI trend, the fiscal year ended December 31, 2023 through the fiscal year "
           "ended December 31, 2025")
META_Q = ("Meta Stockholders' Equity Attributable to Parent by year, the fiscal year ended "
          "December 31, 2023 to the fiscal year ended December 31, 2025")
WEEKS_Q = ("Revenue trend, the fiscal year ended January 29, 2023 through the fiscal year ended "
           "February 2, 2025")

LO, HI = 2018, 2035


def written_date(d: date) -> str:
    """A month-first written date, e.g. ``December 31, 2023``."""
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


# Undated endpoint forms of requirement 1; each writes year ``y``.
UNDATED_FORMS = {
    "FY4": lambda y: f"FY{y}",
    "FY 4": lambda y: f"FY {y}",
    "FY2": lambda y: f"FY{y % 100:02d}",
    "FY'2": lambda y: f"FY'{y % 100:02d}",
    "FY’2": lambda y: f"FY’{y % 100:02d}",
    "fiscal": lambda y: f"fiscal {y}",
    "fiscal year": lambda y: f"fiscal year {y}",
    "bare": lambda y: f"{y}",
}
# Dated endpoint forms; without a calendar each is the date's year (requirement 4).
DATED_FORMS = {
    "date": lambda y: f"December 31, {y}",
    "abbr": lambda y: f"Dec 31 {y}",
    "abbr dot": lambda y: f"Dec. 31, {y}",
    "fye": lambda y: f"the fiscal year ended December 31, {y}",
    "fye nothe": lambda y: f"fiscal year ended Dec 31, {y}",
}
ALL_FORMS = {**UNDATED_FORMS, **DATED_FORMS}

# Range separators of requirement 1: (before the first endpoint, between the endpoints).
RANGE_JOINS = [
    ("", " through "), ("", " thru "), ("", " to "), ("from ", " to "), ("from ", " through "),
    ("from ", " thru "), ("", "-"), ("", " - "), ("", "–"), ("", " – "), ("", " -> "),
    ("", " → "), ("between ", " and "),
]
# List connectors of requirement 2.
LIST_JOINS = [" and ", ", ", " vs ", " versus ", " compared with ", " or "]

# Sentence frames with no year, no quarter cue and no range word.
HEADS = ["", "AAA revenue trend, ", "Show Alpha Holdings net income for each year ",
         "What was AAA's total assets by year, "]
TAILS = ["", "?", ".", " please"]


def endpoint_pairs():
    """Two distinct years in bounds and a form for each (any mix of forms)."""
    years = st.integers(min_value=LO, max_value=HI)
    return st.tuples(years, years, st.sampled_from(sorted(ALL_FORMS)),
                     st.sampled_from(sorted(ALL_FORMS))).filter(lambda t: t[0] != t[1])




@pytest.mark.criterion("C1")
def test_the_union_pacific_range():
    # oracle: "FY23 through FY25" read by hand: fiscal 2023 to fiscal 2025
    assert stated_fiscal_year_ranges(UNP_Q) == [(2023, 2025)]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AAA revenue FY2023 through FY2025", [(2023, 2025)]),
        ("AAA revenue FY 2022 thru FY 2024", [(2022, 2024)]),
        ("AAA revenue fiscal 2021 to fiscal 2024", [(2021, 2024)]),
        ("AAA revenue from fiscal year 2022 to fiscal year 2025", [(2022, 2025)]),
        ("AAA revenue from FY'21 through FY'23", [(2021, 2023)]),
        ("AAA revenue FY’21-FY’24", [(2021, 2024)]),
        ("AAA revenue 2020 – 2023", [(2020, 2023)]),
        ("AAA revenue 2020–2022", [(2020, 2022)]),
        ("AAA revenue FY22 -> FY25", [(2022, 2025)]),
        ("AAA revenue FY22 → FY24", [(2022, 2024)]),
        ("the change in AAA revenue between FY23 and FY25", [(2023, 2025)]),
        ("AAA revenue for fiscal years 2023 through 2025", [(2023, 2025)]),
        ("AAA revenue FY2023 through December 31, 2025", [(2023, 2025)]),
        ("AAA revenue Dec. 31, 2022 to Dec 31 2024", [(2022, 2024)]),
        ("AAA revenue from FY2025 to FY2023", [(2023, 2025)]),
        ("AAA revenue FY2019 through FY2021 and FY2023 through FY2025",
         [(2019, 2021), (2023, 2025)]),
        ("AAA revenue fiscal 2018 through fiscal 2030", [(2018, 2030)]),
    ],
)
def test_each_separator_and_form(text, expected):
    # oracle: requirement 1's separators and endpoint forms, and requirement 5's order, read by
    # hand from the text (no calendar: a date endpoint is the date's year)
    assert stated_fiscal_year_ranges(text) == expected


@pytest.mark.criterion("C1")
def test_lists_split_years_and_quarters_state_no_range():
    # oracle: requirements 2, 3 and 1's non-ranges, by hand; the last text keeps one real range
    # so an empty answer for everything is not enough
    for text in ("AAA revenue FY23 and FY25", "AAA revenue FY23, FY25", "AAA revenue FY23 vs FY25",
                 "AAA revenue FY23 versus FY25", "AAA revenue FY23 compared with FY25",
                 "AAA revenue FY23 or FY25",
                 "the change between revenue in FY23 and FY25", "AAA revenue FY21-23",
                 "AAA revenue 2021-22", "AAA revenue FY2023-24", "AAA assets on 2023-12-31",
                 "AAA revenue Q1 FY23 through FY25",
                 "AAA revenue for the first quarter of fiscal 2023 to fiscal 2025",
                 "AAA revenue fiscal 2010 through fiscal 2025"):
        assert stated_fiscal_year_ranges(text) == [], text
    assert stated_fiscal_year_ranges("AAA revenue FY23 and FY21 through FY22") == [(2021, 2022)]


@pytest.mark.criterion("C1")
@given(pair=endpoint_pairs(), join=st.sampled_from(RANGE_JOINS), head=st.sampled_from(HEADS),
       tail=st.sampled_from(TAILS))
def test_any_two_endpoints_joined_as_a_range_give_that_range(pair, join, head, tail):
    # oracle: the two years are generated; each form writes its year (a date endpoint without a
    # calendar is the date's year), so the range is (min, max) by hand
    y1, y2, f1, f2 = pair
    lead, sep = join
    text = f"{head}{lead}{ALL_FORMS[f1](y1)}{sep}{ALL_FORMS[f2](y2)}{tail}"
    assert stated_fiscal_year_ranges(text) == [(min(y1, y2), max(y1, y2))]


@pytest.mark.criterion("C1")
@given(pair=endpoint_pairs(), join=st.sampled_from(LIST_JOINS), head=st.sampled_from(HEADS),
       tail=st.sampled_from(TAILS))
@pytest.mark.metamorphic
def test_the_same_endpoints_joined_as_a_list_give_no_range(pair, join, head, tail):
    # oracle: requirement 2 — a list connector states no range; the range spelling of the same
    # endpoints is the positive control
    y1, y2, f1, f2 = pair
    a, b = ALL_FORMS[f1](y1), ALL_FORMS[f2](y2)
    assert stated_fiscal_year_ranges(f"{head}{a}{join}{b}{tail}") == []
    assert stated_fiscal_year_ranges(f"{head}{a} through {b}{tail}") == [
        (min(y1, y2), max(y1, y2))]


@pytest.mark.criterion("C2")
def test_dated_endpoints_read_through_a_december_filer():
    # oracle: DEC31 rows — fiscal 2023 ends 2023-12-31, fiscal 2025 ends 2025-12-31
    assert stated_fiscal_year_ranges(TESLA_Q, DEC31) == [(2023, 2025)]
    assert stated_fiscal_year_ranges(META_Q, DEC31) == [(2023, 2025)]


@pytest.mark.criterion("C2")
def test_dated_endpoints_take_the_filers_labels():
    # oracle: WEEKS rows — 2023-01-29 ends fiscal 2022, 2025-02-02 ends fiscal 2024; with no
    # calendar the date's own years 2023 and 2025 are read (requirement 4)
    assert stated_fiscal_year_ranges(WEEKS_Q, WEEKS) == [(2022, 2024)]
    assert stated_fiscal_year_ranges(WEEKS_Q) == [(2023, 2025)]
    assert stated_fiscal_year_ranges(WEEKS_Q, []) == [(2023, 2025)]


@pytest.mark.criterion("C2")
def test_a_date_that_ends_no_fiscal_year_drops_the_range():
    # oracle: WEEKS rows by hand — 2024-03-15 is 47 days from the nearest annual end; 2025-05-04
    # ends only fiscal 2025's first quarter. The last text is the positive control.
    far = ("Revenue trend, the fiscal year ended January 29, 2023 through the fiscal year ended "
           "March 15, 2024")
    quarter = ("Revenue trend, the fiscal year ended January 29, 2023 through the fiscal year "
               "ended May 4, 2025")
    assert stated_fiscal_year_ranges(far, WEEKS) == []
    assert stated_fiscal_year_ranges(quarter, WEEKS) == []
    near = ("Revenue trend, the fiscal year ended January 31, 2023 through the fiscal year ended "
            "January 31, 2025")
    # 2023-01-31 is 2 days from fiscal 2022's end, 2025-01-31 is 2 days from fiscal 2024's end
    assert stated_fiscal_year_ranges(near, WEEKS) == [(2022, 2024)]


@pytest.mark.criterion("C2")
@given(
    first=st.integers(min_value=2019, max_value=2028),
    years=st.integers(min_value=3, max_value=6),
    offset=st.sampled_from([0, -1]),
    pick=st.tuples(st.integers(min_value=0, max_value=5), st.integers(min_value=0, max_value=5)),
    month=st.sampled_from([1, 6, 9, 12]),
)
def test_generated_calendars_map_end_dates_to_labels(first, years, offset, pick, month):
    # oracle: the calendar is generated by hand-coded rules — year i ends on the 28th of
    # ``month`` of first + i and is labelled first + i + offset; a range between two of those
    # end dates is the two labels
    ends = [date(first + i, month, 28) for i in range(years)]
    cal = [row(first + i + offset, "FY", e) for i, e in enumerate(ends)]
    i, j = pick[0] % years, pick[1] % years
    if i == j:
        j = (i + 1) % years
    text = (f"AAA trend, the fiscal year ended {written_date(ends[i])} through the fiscal year "
            f"ended {written_date(ends[j])}")
    la, lb = first + i + offset, first + j + offset
    assert stated_fiscal_year_ranges(text, cal) == [(min(la, lb), max(la, lb))]


@pytest.mark.criterion("C2")
@given(
    first=st.integers(min_value=2019, max_value=2028),
    shift=st.integers(min_value=8, max_value=150),
    sign=st.sampled_from([1, -1]),
)
def test_a_date_8_or_more_days_from_every_end_drops_the_range(first, shift, sign):
    # oracle: annual ends one year apart; a date 8 to 150 days from the first end is at least
    # 8 days from every end (the next is 365 days away), outside the 7-day tolerance. The
    # unshifted text is the positive control.
    ends = [date(first + i, 12, 28) for i in range(4)]
    cal = [row(first + i, "FY", e) for i, e in enumerate(ends)]
    moved = ends[0] + timedelta(days=sign * shift)
    good = (f"AAA trend, the fiscal year ended {written_date(ends[0])} through the fiscal year "
            f"ended {written_date(ends[2])}")
    bad = (f"AAA trend, the fiscal year ended {written_date(moved)} through the fiscal year "
           f"ended {written_date(ends[2])}")
    assert stated_fiscal_year_ranges(good, cal) == [(first, first + 2)]
    assert stated_fiscal_year_ranges(bad, cal) == []



@pytest.mark.criterion("C3")
def test_the_evidence_cases_get_their_middle_year():
    # oracle: each question states fiscal 2023 through 2025 (the dated ones through DEC31's
    # December-31 ends); the router resolved only the endpoints 2023 and 2025
    assert missing_range_years(UNP_Q, [2023, 2025]) == [2024]
    assert missing_range_years(TESLA_Q, [2023, 2025], DEC31) == [2024]
    assert missing_range_years(META_Q, [2025, 2023], DEC31) == [2024]


@pytest.mark.criterion("C3")
def test_fills_by_the_filers_labels():
    # oracle: WEEKS — the stated range is fiscal 2022 through 2024; resolved [2022, 2024]
    assert missing_range_years(WEEKS_Q, [2022, 2024], WEEKS) == [2023]


@pytest.mark.criterion("C3")
def test_fills_every_gap_and_only_inside_ranges():
    # oracle: by hand — FY21..FY25 less {21, 23, 25}; two ranges 2019-2021 and 2023-2025 with
    # their endpoints resolved: 2020 and 2024, never 2022 (between the ranges); a complete
    # range adds nothing
    assert missing_range_years("AAA revenue FY21 through FY25", [2021, 2023, 2025]) == [2022, 2024]
    two = "AAA revenue FY2019 through FY2021 and FY2023 through FY2025"
    assert missing_range_years(two, [2019, 2021, 2023, 2025]) == [2020, 2024]
    assert missing_range_years(UNP_Q, [2023, 2024, 2025]) == []
    assert missing_range_years("AAA revenue from 2025 to 2022", [2022, 2025]) == [2023, 2024]


@pytest.mark.criterion("C3")
@given(
    lo=st.integers(min_value=2018, max_value=2026),
    span=st.integers(min_value=1, max_value=9),
    forms=st.tuples(st.sampled_from(sorted(ALL_FORMS)), st.sampled_from(sorted(ALL_FORMS))),
    join=st.sampled_from(RANGE_JOINS),
    head=st.sampled_from(HEADS),
    reverse=st.booleans(),
    data=st.data(),
)
def test_resolved_plus_filled_is_exactly_the_range(lo, span, forms, join, head, reverse, data):
    # oracle: the range lo..hi is generated and written by hand-coded forms; the resolved years
    # are its two endpoints, any subset of its middle, and any years outside it. The fill is
    # the range less the resolved years.
    hi = lo + span
    middle = list(range(lo + 1, hi))
    some_middle = data.draw(st.lists(st.sampled_from(middle), unique=True) if middle
                            else st.just([]))
    outside = data.draw(st.lists(st.integers(min_value=2018, max_value=2035).filter(
        lambda y: y < lo or y > hi), unique=True, max_size=3))
    a, b = (hi, lo) if reverse else (lo, hi)
    lead, sep = join
    text = f"{head}{lead}{ALL_FORMS[forms[0]](a)}{sep}{ALL_FORMS[forms[1]](b)}"
    resolved = [lo, hi, *some_middle, *outside]
    got = missing_range_years(text, resolved)
    assert got == sorted(set(range(lo, hi + 1)) - set(resolved))
    assert not set(got) & set(resolved)


@pytest.mark.criterion("C4")
def test_lists_add_nothing():
    # oracle: requirement 2 — each text is a list of 2023 and 2025; the range spelling is the
    # positive control
    for text in ("AAA revenue FY23 and FY25", "AAA revenue FY23 vs FY25",
                 "the change between revenue in FY23 and FY25", "AAA revenue FY23, FY25"):
        assert missing_range_years(text, [2023, 2025]) == [], text
    assert missing_range_years("the change in revenue between FY23 and FY25",
                               [2023, 2025]) == [2024]


@pytest.mark.criterion("C4")
def test_one_endpoint_long_ranges_quarters_and_unmatched_dates_add_nothing():
    # oracle: requirement 6 by hand — an endpoint unresolved; 11 years (2018..2028) is over
    # MAX_RANGE_YEARS while 10 (2018..2027) is not; a quarter range (requirement 3); a date
    # 47 days from every WEEKS annual end (requirement 4)
    assert missing_range_years(UNP_Q, [2025]) == []
    assert missing_range_years(UNP_Q, [2023, 2024]) == []
    assert missing_range_years("AAA revenue fiscal 2018 through fiscal 2028", [2018, 2028]) == []
    assert missing_range_years("AAA revenue fiscal 2018 through fiscal 2027",
                               [2018, 2027]) == list(range(2019, 2027))
    assert missing_range_years("AAA revenue Q1 FY23 through FY25", [2023, 2025]) == []
    far = ("Revenue trend, the fiscal year ended January 29, 2023 through the fiscal year ended "
           "March 15, 2024")
    assert missing_range_years(far, [2022, 2023], WEEKS) == []
    assert missing_range_years(TESLA_Q, [2023, 2025], WEEKS) == []
    assert missing_range_years(TESLA_Q, [2023, 2025], DEC31) == [2024]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(
    lo=st.integers(min_value=2018, max_value=2030),
    span=st.integers(min_value=2, max_value=5),
    forms=st.tuples(st.sampled_from(sorted(ALL_FORMS)), st.sampled_from(sorted(ALL_FORMS))),
    join=st.sampled_from(LIST_JOINS),
    head=st.sampled_from(HEADS),
)
def test_a_list_connector_in_place_of_the_range_adds_nothing(lo, span, forms, join, head):
    # oracle: requirement 2 — the same endpoints joined as a list state no range; the range
    # spelling fills the middle (positive control)
    hi = lo + span
    a, b = ALL_FORMS[forms[0]](lo), ALL_FORMS[forms[1]](hi)
    assert missing_range_years(f"{head}{a}{join}{b}", [lo, hi]) == []
    assert missing_range_years(f"{head}{a} through {b}", [lo, hi]) == list(range(lo + 1, hi))


@pytest.mark.criterion("C4")
@given(
    lo=st.integers(min_value=2018, max_value=2030),
    span=st.integers(min_value=2, max_value=5),
    drop_low=st.booleans(),
    data=st.data(),
)
def test_resolved_years_without_an_endpoint_add_nothing(lo, span, drop_low, data):
    # oracle: requirement 6 — both endpoints must be resolved; with both the middle comes back
    # (positive control)
    hi = lo + span
    kept = [y for y in range(lo, hi + 1) if y != (lo if drop_low else hi)]
    resolved = data.draw(st.lists(st.sampled_from(kept), unique=True, min_size=1))
    text = f"AAA revenue FY{lo} through FY{hi}"
    assert missing_range_years(text, resolved) == []
    assert missing_range_years(text, [lo, hi]) == list(range(lo + 1, hi))


@pytest.mark.criterion("C4")
@given(lo=st.integers(min_value=2018, max_value=2024), span=st.integers(min_value=11, max_value=18))
def test_ranges_over_ten_years_add_nothing(lo, span):
    # oracle: requirement 6 with MAX_RANGE_YEARS = 10 — lo..lo+span-1 counts span years; a
    # 10-year range from the same start fills its middle (positive control)
    hi = lo + span - 1
    assert missing_range_years(f"AAA revenue FY{lo} through FY{hi}", [lo, hi]) == []
    assert missing_range_years(f"AAA revenue FY{lo} through FY{lo + 9}",
                               [lo, lo + 9]) == list(range(lo + 1, lo + 9))
