"""Spec tests for last-fiscal-year-phrase: "last / previous / preceding fiscal year" resolve as
"prior fiscal year" does, to the anchored filer's ``last_seen_fy - 1``
(docs/specs/last-fiscal-year-phrase.md).

Everything is synthetic mention text and a synthetic filer; the database is faked at
``psycopg2.connect``. Expected years are the existing meaning of "prior fiscal year" applied by
hand: ``anchor - 1``. All comparisons are exact integers and strings.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.period_evidence import has_year_evidence

PHRASES = ["last fiscal year", "previous fiscal year", "preceding fiscal year"]
# Words around a phrase that carry no period meaning of their own: no digit, no relative
# phrase ("current", "latest", "most recent", "this year"), no quarter, half or month word,
# no "calendar" (hand-checked against Requirement 1 and the extractor's phrase tables).
PREFIXES = ["", "the ", "in the ", "during the ", "example co's ", "for the "]
SUFFIXES = ["", " ended", " results", " reported", " total"]
QUARTERS = [("Q1", "Q1"), ("Q2", "Q2"), ("Q3", "Q3"), ("Q4", "Q4"),
            ("first quarter", "Q1"), ("second quarter", "Q2"), ("third quarter", "Q3"),
            ("fourth quarter", "Q4")]
ANCHORS = st.integers(min_value=2018, max_value=2035)


def _one(kit, mention, anchor):
    periods = kit.resolve(mention, anchor)
    assert len(periods) == 1, periods
    return periods[0]


def _year(kit, mention, anchor):
    p = _one(kit, mention, anchor)
    return (p.fiscal_year, p.fiscal_period, p.ambiguous)


def _recase(text: str, mask: list[bool]) -> str:
    return "".join(ch.upper() if mask[i % len(mask)] else ch.lower()
                   for i, ch in enumerate(text))


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
@pytest.mark.parametrize("mention", [
    "last fiscal year", "the last fiscal year", "previous fiscal year", "preceding fiscal year",
    "LAST FISCAL YEAR", "Apple's last fiscal year",
])
def test_c1_phrase_resolves_to_the_year_before_the_anchor(kit, mention):
    # oracle: Requirement 1 — as "prior fiscal year": last_seen_fy 2025 - 1 = 2024, annual
    assert _year(kit, mention, kit.company(2025)) == (2024, "FY", False)


@pytest.mark.criterion("C1")
@given(anchor=ANCHORS, phrase=st.sampled_from(PHRASES), prefix=st.sampled_from(PREFIXES),
       suffix=st.sampled_from(SUFFIXES), mask=st.lists(st.booleans(), min_size=1, max_size=12))
def test_c1_any_anchor_case_and_neutral_words_give_anchor_minus_one(
        kit, anchor, phrase, prefix, suffix, mask):
    # oracle: Requirement 1 by hand — anchor - 1, the same as "prior fiscal year" for that anchor
    mention = _recase(prefix + phrase + suffix, mask)
    company = kit.company(anchor)
    assert _year(kit, mention, company) == (anchor - 1, "FY", False)
    assert _year(kit, mention, company) == _year(kit, "prior fiscal year", company)


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
@pytest.mark.parametrize("mention, expected", [
    ("Q3 of the last fiscal year", (2026, "Q3", False)),
    ("fourth quarter of the previous fiscal year", (2026, "Q4", False)),
])
def test_c2_a_quarter_of_the_phrase_keeps_the_quarter(kit, mention, expected):
    # oracle: Requirement 2 — the detected quarter of last_seen_fy 2027 - 1 = 2026 (2027, so the
    # answer differs from extract_years_in_range's fixed 2024 default for a yearless "Q3")
    assert _year(kit, mention, kit.company(2027)) == expected


@pytest.mark.criterion("C2")
@given(anchor=ANCHORS, phrase=st.sampled_from(PHRASES), quarter=st.sampled_from(QUARTERS))
def test_c2_any_quarter_of_any_phrase(kit, anchor, phrase, quarter):
    # oracle: Requirement 2 by hand — (anchor - 1, the named quarter)
    words, expected_period = quarter
    mention = f"{words} of the {phrase}"
    assert _year(kit, mention, kit.company(anchor)) == (anchor - 1, expected_period, False)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
@pytest.mark.parametrize("phrase", PHRASES)
def test_c3_no_anchor_is_ambiguous_and_still_degrades(kit, phrase):
    # oracle: Requirement 3 — no company or no last_seen_fy: ambiguous, no year guessed; the
    # phrase carries year evidence so yearless-period-mention keeps it (it degrades)
    for anchor in (None, kit.company(None)):
        p = _one(kit, phrase, anchor)
        assert p.ambiguous is True
        assert p.fiscal_year is None
    assert has_year_evidence(phrase) is True
    assert _year(kit, phrase, kit.company(2025)) == (2024, "FY", False)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(anchor=ANCHORS, phrase=st.sampled_from(PHRASES))
def test_c3_removing_the_anchor_never_moves_the_year(kit, anchor, phrase):
    # oracle: Requirement 3 by hand — with an anchor anchor - 1; without, ambiguous, never
    # another year
    assert _year(kit, phrase, kit.company(anchor)) == (anchor - 1, "FY", False)
    unanchored = _one(kit, phrase, kit.company(None))
    assert unanchored.ambiguous is True
    assert unanchored.fiscal_year is None


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_c4_only_the_whole_phrases_and_the_neighbours_keep_their_year(kit):
    # oracle: Requirement 4 — last_seen_fy 2025: "last fiscal year" is 2024; "last fiscal
    # quarter" is never the full year; "most recent"/"latest fiscal year" stay 2025
    company = kit.company(2025)
    assert _year(kit, "last fiscal year", company) == (2024, "FY", False)
    quarter = kit.resolve("last fiscal quarter", company)
    assert not any(p.fiscal_period == "FY" and not p.ambiguous for p in quarter)
    assert _year(kit, "most recent fiscal year", company) == (2025, "FY", False)
    assert _year(kit, "latest fiscal year", company) == (2025, "FY", False)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(anchor=ANCHORS, phrase=st.sampled_from(PHRASES))
def test_c4_year_to_quarter_never_gives_the_full_year(kit, anchor, phrase):
    # oracle: Requirement 4 by hand — the year phrase gives (anchor - 1, FY); the same words
    # with "quarter" for "year" never give the full year anchor - 1
    company = kit.company(anchor)
    assert _year(kit, phrase, company) == (anchor - 1, "FY", False)
    swapped = kit.resolve(phrase.replace("year", "quarter"), company)
    assert not any(p.fiscal_year == anchor - 1 and p.fiscal_period == "FY" and not p.ambiguous
                   for p in swapped)
