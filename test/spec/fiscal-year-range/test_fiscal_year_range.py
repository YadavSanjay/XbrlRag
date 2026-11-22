"""Spec tests for fiscal-year-range: a period mention that names a range resolves to every
fiscal year in it (docs/specs/fiscal-year-range.md).

C1-C3 test ``extract_years_in_range`` alone. C4-C7 run the real router and extractor (fake LLM
adapter, fake database) and read what each consumer gets. Every expected list is the phrase read
by hand. No ``data/`` file, network or clock is read. The spec has no new functions (bug-fix
mode), so each test fails on the 2026-11-05 code; checks that already hold ride alongside the
behaviour they guard.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context_arms import filing_targets
from src.retriever.extract_year_util import extract_years_in_range
from src.retriever.html10k.scopes import search_scopes
from src.retriever.xbrl.branch_dispatcher import BRANCH, BranchDispatcher

# The resolver's own year bounds (entity_extractor passes [2018, 2035]).
LO, HI = 2018, 2035
BOUNDS = [LO, HI]


def years(text: str) -> list[int]:
    return extract_years_in_range(text, year_range=BOUNDS)


# ----------------------------------------------------------------------------------
# Phrasings: the endpoint forms and separators of requirement 1.
# ----------------------------------------------------------------------------------

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]

# Endpoint forms; each formats one year. Date forms take a month and a day too.
YEAR_FORMS = (
    "FY{yyyy}", "FY {yyyy}", "FY{yy}", "FY'{yy}", "FY’{yy}",
    "fiscal {yyyy}", "fiscal year {yyyy}", "{yyyy}",
)
DATE_FORMS = (
    "the fiscal year ended {month} {day}, {yyyy}",
    "fiscal year ended {mon} {day} {yyyy}",
    "{month} {day}, {yyyy}",
)
# Separators that join two endpoints. Dash separators are used with year forms only.
WORD_SEPARATORS = ("{a} through {b}", "{a} thru {b}", "{a} to {b}",
                   "from {a} to {b}", "from {a} through {b}", "from {a} thru {b}",
                   "between {a} and {b}")
DASH_SEPARATORS = ("{a}-{b}", "{a} - {b}", "{a}–{b}", "{a} – {b}")
LIST_JOINS = (" and ", ", ", ", and ", " vs ", " versus ", " compared with ")


def endpoint(form: str, year: int, month: int = 12, day: int = 31) -> str:
    """One endpoint written in *form*."""
    name = _MONTHS[month - 1]
    return form.format(yyyy=f"{year:04d}", yy=f"{year % 100:02d}", month=name, mon=name[:3],
                       day=day)


def _keep(text: str) -> str:
    return text


@st.composite
def range_phrase(draw, min_span: int = 2, max_span: int = 10, reverse: bool | None = None):
    """A range phrase and its (lower, higher) years: any two forms, any separator."""
    span = draw(st.integers(min_value=min_span, max_value=max_span))
    lo = draw(st.integers(min_value=LO, max_value=HI - span + 1))
    hi = lo + span - 1
    if draw(st.booleans()):
        forms = st.sampled_from(DATE_FORMS + YEAR_FORMS)
        sep = draw(st.sampled_from(WORD_SEPARATORS))
    else:
        forms = st.sampled_from(YEAR_FORMS)
        sep = draw(st.sampled_from(WORD_SEPARATORS + DASH_SEPARATORS))
    month = draw(st.integers(min_value=1, max_value=12))
    day = draw(st.integers(min_value=1, max_value=28))
    backwards = draw(st.booleans()) if reverse is None else reverse
    first, last = (hi, lo) if backwards else (lo, hi)
    text = sep.format(a=endpoint(draw(forms), first, month, day),
                      b=endpoint(draw(forms), last, month, day))
    case = draw(st.sampled_from([str.lower, str.upper, _keep]))
    return case(text), lo, hi


def every(lo: int, hi: int) -> list[int]:
    return list(range(lo, hi + 1))


# ---------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_range_phrasings_give_every_year():
    # oracle: each phrase read by hand; requirement 1 (inclusive, ascending)
    cases = {
        "fiscal year 2023 through fiscal year 2025": [2023, 2024, 2025],
        "fiscal 2023 to fiscal 2025": [2023, 2024, 2025],
        "from fiscal 2023 through fiscal 2025": [2023, 2024, 2025],
        "from fiscal year 2023 to fiscal year 2025": [2023, 2024, 2025],
        "between fiscal year 2022 and fiscal year 2025": [2022, 2023, 2024, 2025],
        "between fiscal 2023 and fiscal 2025": [2023, 2024, 2025],
        "fiscal 2023 thru fiscal 2025": [2023, 2024, 2025],
        "FY'23 through FY'25": [2023, 2024, 2025],
        "FY’22 to FY’24": [2022, 2023, 2024],
        "FY23-FY25": [2023, 2024, 2025],
        "FY2021 – FY2024": [2021, 2022, 2023, 2024],
        "FY2023 through FY2025": [2023, 2024, 2025],
        "2023 through 2025": [2023, 2024, 2025],
        "fiscal years 2023 through 2025": [2023, 2024, 2025],
        "FY2023 through fiscal 2025": [2023, 2024, 2025],
        "the fiscal year ended June 30, 2023 through the fiscal year ended June 30, 2025":
            [2023, 2024, 2025],
        "between the fiscal year ended January 30, 2022 and the fiscal year ended January 25, 2026":
            [2022, 2023, 2024, 2025, 2026],
        "from the fiscal year ended dec 31 2023 thru dec 31 2025": [2023, 2024, 2025],
        "from December 31, 2022 to December 31, 2024": [2022, 2023, 2024],
    }
    for text, expected in cases.items():
        assert years(text) == expected, text


@pytest.mark.criterion("C1")
def test_a_range_inside_a_question_gives_every_year():
    # oracle: the smoke case and two suite-shaped questions, read by hand
    cases = {
        "Please present Procter & Gamble's shares outstanding for each year from fiscal year 2023 "
        "through fiscal year 2025.": [2023, 2024, 2025],
        "Can you walk me through Tesla's NI year by year from fiscal year 2023 to fiscal year "
        "2025?": [2023, 2024, 2025],
        "Lockheed Martin Repayments of Long-Term Debt trend, fiscal 2023 through fiscal 2025":
            [2023, 2024, 2025],
    }
    for text, expected in cases.items():
        assert years(text) == expected, text


@pytest.mark.criterion("C1")
@given(range_phrase(reverse=False))
def test_any_range_phrasing_gives_every_year(case):
    # oracle: property — the years from lo to hi, inclusive, whatever the forms and separator
    text, lo, hi = case
    assert years(text) == every(lo, hi), text


# ---------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
def test_reversed_and_overlong_ranges():
    # oracle: requirements 2 and 3 by hand — reversed is the same years; above 10 years only
    # the endpoints; exactly 10 years expands
    assert years("from fiscal year 2025 to fiscal year 2023") == [2023, 2024, 2025]
    assert years("FY25 through FY23") == [2023, 2024, 2025]
    assert years("fiscal 2018 through fiscal 2028") == [2018, 2028]
    assert years("fiscal 2018 through fiscal 2027") == every(2018, 2027)
    assert extract_years_in_range("2000 to 2030") == [2000, 2030]
    assert extract_years_in_range("from 2001 to 2010") == every(2001, 2010)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.data())
def test_reversing_a_range_does_not_change_its_years(data):
    # oracle: metamorphic — the same endpoints in the other order name the same years
    text, lo, hi = data.draw(range_phrase(reverse=True))
    assert years(text) == every(lo, hi), text


@pytest.mark.criterion("C2")
@given(range_phrase(min_span=11, max_span=18))
def test_a_range_over_ten_years_gives_its_endpoints(case):
    # oracle: property — requirement 3, more than MAX_RANGE_YEARS = 10 is not expanded
    text, lo, hi = case
    assert years(text) == [lo, hi], text


# ---------------------------------------------------------------------------------- C3


@pytest.mark.criterion("C3")
def test_lists_keep_their_years_and_join_a_range():
    # oracle: requirement 4 by hand — a list is its years, a list with a range is the union
    assert years("fiscal 2023 and fiscal 2025") == [2023, 2025]
    assert years("FY2023, FY2025") == [2023, 2025]
    assert years("fiscal 2025 vs fiscal 2023") == [2023, 2025]
    assert years("the difference between revenue in fiscal 2025 and fiscal 2023") == [2023, 2025]
    assert years("fiscal 2019 and fiscal year 2023 through fiscal year 2025") == [
        2019, 2023, 2024, 2025]
    assert years("fiscal 2019, and from fiscal 2022 to fiscal 2024") == [2019, 2022, 2023, 2024]


@pytest.mark.criterion("C3")
@given(
    st.lists(st.integers(min_value=LO, max_value=HI), min_size=1, max_size=3, unique=True),
    st.sampled_from(YEAR_FORMS),
    st.sampled_from(LIST_JOINS),
    range_phrase(reverse=False),
)
def test_listed_years_plus_a_range_are_the_union(listed, form, join, case):
    # oracle: property — listed years stay as listed; the range adds its own years
    text, lo, hi = case
    head = join.join(endpoint(form, y) for y in listed)
    phrase = f"{head} and {text}"
    assert years(phrase) == sorted(set(listed) | set(every(lo, hi))), phrase


# ---------------------------------------------------------------------------------- C4


def _decide(router, result):
    return BranchDispatcher(intent_router=router).decide_from_result("AAA revenue", result)


@pytest.mark.criterion("C4")
def test_a_multi_year_mention_skips_the_fast_path(make_route):
    # oracle: requirement 6; the phrases name 3 and 2 years, read by hand
    for mention in ("fiscal year 2023 through fiscal year 2025", "fiscal 2023 and fiscal 2025",
                    "FY2023 through FY2025"):
        router, result = make_route([mention])
        assert result.plan is None, mention
        decision = _decide(router, result)
        assert decision.branch == BRANCH.PLAN_GENERATOR, mention
        assert decision.degraded is False, mention

    # A single year keeps its one-step lookup.
    router, result = make_route(["fiscal 2024"])
    assert result.plan is not None
    steps = result.plan["steps"]
    assert len(steps) == 1
    assert steps[0]["op"] == "lookup"
    assert steps[0]["fy"] == 2024


@pytest.mark.criterion("C4")
@given(range_phrase())
def test_no_range_takes_the_fast_path(make_route, case):
    # oracle: property — every generated range names 2 to 10 years (requirement 6)
    text, _, _ = case
    router, result = make_route([text])
    assert result.plan is None, text
    assert _decide(router, result).branch == BRANCH.PLAN_GENERATOR, text


# ---------------------------------------------------------------------------------- C5


@pytest.mark.criterion("C5")
def test_the_planner_gets_every_year_in_scope(make_route, planner_years):
    # oracle: the mentions read by hand
    cases = [
        (["fiscal year 2023 through fiscal year 2025"], [2023, 2024, 2025]),
        (["fiscal year 2025 through fiscal year 2023"], [2023, 2024, 2025]),
        (["fiscal 2019", "fiscal 2023 to fiscal 2025"], [2019, 2023, 2024, 2025]),
        (["fiscal 2023 and fiscal 2025"], [2023, 2025]),
    ]
    for mentions, expected in cases:
        _, result = make_route(mentions)
        assert planner_years(result) == expected, mentions


@pytest.mark.criterion("C5")
@given(range_phrase())
def test_any_range_puts_every_year_in_the_planner_scope(make_route, planner_years, case):
    # oracle: property — the years from lo to hi, inclusive
    text, lo, hi = case
    _, result = make_route([text])
    assert planner_years(result) == every(lo, hi), text


# ---------------------------------------------------------------------------------- C6


@pytest.mark.criterion("C6")
def test_html_rag_long_context_and_narrative_see_every_year(
    make_route, narrative_filter, aaa_cik
):
    # oracle: "fiscal year 2023 through fiscal year 2025" names 2023, 2024 and 2025
    mention = "fiscal year 2023 through fiscal year 2025"
    _, result = make_route([mention])

    scopes, _reason = search_scopes(result, max_filer_searches=5)
    assert scopes == [{"ciks": [aaa_cik], "fiscal_years": [2023, 2024, 2025]}]

    assert filing_targets(result) == ([("AAA", 2023), ("AAA", 2024), ("AAA", 2025)], None)

    _, narrative = make_route([mention], intent="NARRATIVE")
    assert narrative.plan is not None
    assert narrative_filter(narrative) == [2023, 2024, 2025]


@pytest.mark.criterion("C6")
@given(range_phrase())
def test_any_range_reaches_html_rag_and_long_context(make_route, aaa_cik, case):
    # oracle: property — the years from lo to hi, inclusive
    text, lo, hi = case
    _, result = make_route([text])
    scopes, _reason = search_scopes(result, max_filer_searches=5)
    assert scopes == [{"ciks": [aaa_cik], "fiscal_years": every(lo, hi)}], text
    assert filing_targets(result) == ([("AAA", y) for y in every(lo, hi)], None), text


# ---------------------------------------------------------------------------------- C7


@pytest.mark.criterion("C7")
def test_an_overlong_range_degrades_and_ten_years_does_not(make_route, planner_years):
    # oracle: requirement 3 — 2018..2030 is 13 years (> 10); 2018..2027 is exactly 10
    mention = "fiscal 2018 through fiscal 2030"
    router, result = make_route([mention])
    assert result.plan is None
    decision = _decide(router, result)
    assert decision.branch == BRANCH.NARRATIVE_BRANCH
    assert decision.degraded is True
    assert mention in decision.reason
    targets, reason = filing_targets(result)
    assert targets == []
    assert reason is not None

    router, result = make_route(["fiscal 2018 through fiscal 2027"])
    assert _decide(router, result).degraded is False
    assert planner_years(result) == every(2018, 2027)


@pytest.mark.criterion("C7")
@given(range_phrase(min_span=11, max_span=18))
def test_any_overlong_range_degrades(make_route, case):
    # oracle: property — every generated span is 11 to 18 years, above MAX_RANGE_YEARS = 10
    text, _, _ = case
    router, result = make_route([text])
    assert result.plan is None, text
    decision = _decide(router, result)
    assert decision.degraded is True, text
    assert decision.branch == BRANCH.NARRATIVE_BRANCH, text
