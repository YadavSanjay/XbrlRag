"""C1: what "has a period of its own" means (docs/specs/dependent-subquery-period.md, R1).

Every expected value is Requirement 1 applied by hand: a text states a period when it writes a
year or one of the listed period phrases; ``<<TOP…>>`` tokens and the bare word "current" never
count. All results are exact booleans.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.subquery_period import has_own_period

# Requirement 1.2, copied from the spec.
PERIOD_PHRASES = [
    "most recent", "latest", "this year", "current year", "current fiscal year",
    "current quarter", "current fiscal quarter", "current period",
    "prior year", "last year", "previous year", "prior fiscal year", "last fiscal year",
    "previous fiscal year", "preceding fiscal year", "year ago", "year-ago", "preceding year",
    "first quarter", "1st quarter", "q1", "second quarter", "2nd quarter", "q2",
    "third quarter", "3rd quarter", "q3", "fourth quarter", "4th quarter", "q4",
    "year to date", "year-to-date", "ytd", "first half", "1st half", "six months", "six-month",
    "h1", "first nine months", "nine months", "nine-month", "9 months", "cumulative",
]

NO_PERIOD = [
    "For <<TOP:sq1:1>>, what is the percentage growth in Payments to Acquire Property, Plant, "
    "and Equipment?",
    "Among <<TOP:sq1:3>>, which has the highest Current Ratio?",
    "For <<TOP:sq1:1>>, what was the change in total current assets?",
    "What are Amazon's main logistics-related risk factors?",
    "For <<TOP:fy2024:1>>, what is the year-over-year growth in revenue?",
    "Which is first-ranked by 10-K revenue?",
]

SUFFIXES = [
    " in FY24", " for fiscal 2015", " in Q3", " for the latest fiscal year",
    " versus the prior year", " for the current fiscal year", " year to date",
    " for the fiscal year ended June 30, 2024",
]

# Words that state no period alone or next to each other (by hand: no digit next to a FY
# marker, no "year" after "current"/"this"/"last", no phrase of Requirement 1.2).
FREE_WORDS = [
    "For", "Among", "which", "has", "the", "highest", "Current", "Ratio", "current", "assets",
    "liabilities", "Assets, Current", "Liabilities, Current", "growth", "in", "revenue", "of",
    "first-ranked", "top", "annual", "fiscal", "FY", "what", "is", "percentage", "change",
    "total", "debt", "operating", "margin", "?", ",",
]
TOKEN_IDS = st.from_regex(r"[A-Za-z0-9_]{1,8}", fullmatch=True)
TOKENS = st.builds(lambda i, n: f"<<TOP:{i}:{n}>>", TOKEN_IDS, st.integers(1, 9))
FREE_TEXT = st.lists(st.one_of(st.sampled_from(FREE_WORDS), TOKENS), min_size=1, max_size=12)
YEARS = st.integers(min_value=2000, max_value=2099)
YEAR_FORMS = [
    lambda y: f"{y}", lambda y: f"FY{y % 100:02d}", lambda y: f"fiscal {y}",
    lambda y: f"FY'{y % 100:02d}",
]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", NO_PERIOD)
def test_c1_stored_shapes_state_no_period_until_one_is_appended(text):
    # oracle: Requirement 1 by hand — these texts write no year (tokens removed; "10-K" is no
    # year) and no listed phrase ("Current Ratio", "current assets" are not "current year");
    # each suffix writes a year or a listed phrase
    assert has_own_period(text) is False
    for suffix in SUFFIXES:
        assert has_own_period(text + suffix) is True, suffix


@pytest.mark.criterion("C1")
def test_c1_a_year_like_sub_query_id_never_counts():
    # oracle: Requirement 1 by hand — the token is removed before reading, so "fy2024" inside
    # it is not a year; the same text with "FY2024" outside the token writes one
    assert has_own_period("For <<TOP:fy2024:1>>, what was revenue?") is False
    assert has_own_period("For <<TOP:fy2024:1>>, what was revenue in FY2024?") is True


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(words=FREE_TEXT, phrase=st.sampled_from(PERIOD_PHRASES))
def test_c1_free_text_states_none_and_a_phrase_makes_it_state_one(words, phrase):
    # oracle: metamorphic, Requirement 1 — period-free words and tokens state no period;
    # appending one listed phrase after a space makes the phrase occur
    text = " ".join(words)
    assert has_own_period(text) is False
    assert has_own_period(f"{text} {phrase}") is True
    assert has_own_period(f"{text} {phrase.upper()}?") is True


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(words=FREE_TEXT, year=YEARS, form=st.sampled_from(YEAR_FORMS))
def test_c1_appending_any_year_form_makes_it_state_one(words, year, form):
    # oracle: metamorphic, Requirement 1.1 — each form writes a year inside 2000-2100
    text = " ".join(words)
    assert has_own_period(text) is False
    assert has_own_period(f"{text} for {form(year)}") is True
