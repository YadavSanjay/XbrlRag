"""Spec tests for yearless-period-mention: a period mention with no year evidence
("year-over-year", "each year") is ignored when another period of the query resolved, and
never otherwise (docs/specs/yearless-period-mention.md).

Everything is synthetic mention text; the expected values are the spec's rule applied by hand.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.entity_extractor import ResolvedPeriod
from src.retriever.xbrl.period_evidence import has_year_evidence, partition_yearless_periods

# Requirement 1, lists 2-5, restated by hand.
RELATIVE = ["last", "latest", "recent", "current", "this", "prior", "previous", "preceding",
            "ago", "past", "trailing", "next", "earlier", "ytd", "today", "now"]
SUB_ANNUAL = ["quarter", "quarters", "quarterly", "half", "halves", "month", "months", "date"]
NUMBER_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "eleven", "twelve", "first", "second", "third", "fourth"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december", "jan", "feb", "mar", "apr", "jun",
          "jul", "aug", "sep", "sept", "oct", "nov", "dec"]
EVIDENCE_WORDS = RELATIVE + SUB_ANNUAL + NUMBER_WORDS + MONTHS
# Words that are in none of the lists (hand-checked against Requirement 1).
FILLER = ["fy", "fiscal", "year", "years", "the", "ended", "ending", "annual", "filing",
          "calendar", "each", "over", "yoy", "full", "period", "per", "annum", "for", "of",
          "growth", "change", "reported", "statement", "y", "basis", "versus", "vs",
          "nowhere", "maybe", "lasting", "decade", "halfway", "pastoral", "agonist"]
SEPARATORS = [" ", "-", "/", ", ", " - ", "  "]
CONTROL_TRUE = "fiscal 2024"
CONTROL_FALSE = "year-over-year"


def _p(mention: str, fiscal_year: int | None = None, ambiguous: bool = False,
       reason: str | None = None) -> ResolvedPeriod:
    return ResolvedPeriod(mention=mention, fiscal_year=fiscal_year, ambiguous=ambiguous,
                          reason=reason)


def _failed(mention: str) -> ResolvedPeriod:
    return _p(mention, ambiguous=True, reason="could_not_resolve_year")


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
@pytest.mark.parametrize("mention", [
    "2024", "fiscal 2024", "FY2023", "FY24", "'23", "Q3", "third quarter", "first half",
    "nine months", "year to date", "YTD", "last fiscal year", "most recent", "prior year",
    "the year ended December 31", "Sept", "May", "quarterly", "trailing twelve months",
    "LAST YEAR",
])
def test_mentions_with_year_evidence(mention):
    # oracle: Requirement 1 by hand — each holds a digit or a listed whole word
    assert has_year_evidence(CONTROL_FALSE) is False  # control
    assert has_year_evidence(mention) is True


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("mention", [
    "", "FY", "fiscal", "fiscal year", "the year ended", "annual", "annual filing",
    "calendar year", "each year", "year-over-year", "YoY", "y/y", "full year", "nowhere",
    "maybe", "lasting", "decade", "halfway", "per annum",
])
def test_mentions_without_year_evidence(mention):
    # oracle: Requirement 1 by hand — no digit and no listed whole word (a listed word inside
    # a longer letter run, as in "nowhere" or "decade", does not count)
    assert has_year_evidence(CONTROL_TRUE) is True  # control
    assert has_year_evidence(mention) is False


_filler_words = st.lists(st.sampled_from(FILLER), min_size=1, max_size=6)
_seps = st.lists(st.sampled_from(SEPARATORS), min_size=6, max_size=6)


def _join(words: list[str], seps: list[str]) -> str:
    out = words[0]
    for i, w in enumerate(words[1:]):
        out += seps[i % len(seps)] + w
    return out


def _case(word: str, mode: int) -> str:
    return [word, word.upper(), word.capitalize()][mode]


@pytest.mark.criterion("C1")
@given(_filler_words, _seps, st.integers(0, 2))
def test_filler_only_has_no_evidence(words, seps, mode):
    # oracle: Requirement 1 — none of the FILLER words is in a list and none holds a digit
    mention = _join([_case(w, mode) for w in words], seps)
    assert has_year_evidence(CONTROL_TRUE) is True  # control
    assert has_year_evidence(mention) is False


@pytest.mark.criterion("C1")
@given(_filler_words, _seps, st.sampled_from(EVIDENCE_WORDS + ["2024", "7", "q4", "h1"]),
       st.integers(0, 6), st.integers(0, 2))
def test_one_evidence_token_anywhere_is_enough(words, seps, token, pos, mode):
    # oracle: Requirement 1 — a single listed whole word, or a digit, is evidence wherever it
    # stands and in any letter case
    i = pos % (len(words) + 1)
    mention = _join(words[:i] + [_case(token, mode)] + words[i:], seps)
    assert has_year_evidence(mention) is True


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.sampled_from(EVIDENCE_WORDS), st.sampled_from(["x", "zz", "s", "ly", "able"]),
       st.booleans())
def test_glued_word_is_not_evidence(word, glue, before):
    # oracle: Requirement 1 — matching is on whole tokens: a listed word inside a longer run of
    # letters is no evidence (the glue strings are themselves in no list); the bare word is
    glued = glue + word if before else word + glue
    if glued in EVIDENCE_WORDS:  # e.g. "month" + "s"
        return
    assert has_year_evidence(f"fiscal {word}") is True
    assert has_year_evidence(f"fiscal {glued}") is False


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.lists(st.sampled_from(FILLER + EVIDENCE_WORDS + ["2023", "q2"]), min_size=1,
                max_size=5), _seps)
def test_letter_case_does_not_matter(words, seps):
    # oracle: Requirement 1 lowercases before tokenising, so upper/title case read the same
    mention = _join(words, seps)
    expected = any(w in EVIDENCE_WORDS or any(c.isdigit() for c in w) for w in words)
    assert has_year_evidence(mention) is expected
    assert has_year_evidence(mention.upper()) is expected
    assert has_year_evidence(mention.title()) is expected


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_the_defect_case_ignores_year_over_year():
    # oracle: Requirement 2 by hand — int-b96e86dae3b5-02a0e6fc: "fiscal 2024" resolved,
    # "year-over-year" ambiguous with no evidence -> ignored
    fy = _p("fiscal 2024", 2024)
    yoy = _failed("year-over-year")
    periods = [fy, yoy]
    kept, ignored = partition_yearless_periods(periods)
    assert kept == [fy] and kept[0] is fy
    assert ignored == [yoy] and ignored[0] is yoy
    assert periods == [fy, yoy]  # input untouched


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("resolved,yearless", [
    ([("2023", 2023), ("2025", 2025)], ["each year"]),
    ([("2023", 2023)], ["annual filing"]),
    ([("2024", 2024)], ["year-over-year"]),
])
def test_stored_cases_ignore_the_yearless_mention(resolved, yearless):
    # oracle: Requirement 2 by hand on the other stored cases of the spec's Notes
    res = [_p(m, y) for m, y in resolved]
    bad = [_failed(m) for m in yearless]
    kept, ignored = partition_yearless_periods(res + bad)
    assert kept == res
    assert ignored == bad


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("periods", [
    [_failed("year-over-year")],
    [_failed("YoY"), _p("2019 through 2032", ambiguous=True, reason="range too long")],
])
def test_nothing_resolved_ignores_nothing(periods):
    # oracle: Requirement 2 — no period has ambiguous false, so every period is kept
    kept, ignored = partition_yearless_periods(list(periods))
    assert kept == periods
    assert ignored == []


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("periods", [
    [_p("2024", 2024), _failed("Q3")],
    [_p("FY2023", 2023), _failed("last fiscal year")],
    [_p("FY", 2024), _p("2023", 2023)],
])
def test_real_failures_and_resolved_periods_are_kept(periods):
    # oracle: Requirement 2 — "Q3" (digit) and "last fiscal year" ("last") carry evidence so
    # stay and still degrade; a resolved period is kept whatever its mention
    kept, ignored = partition_yearless_periods(list(periods))
    assert kept == periods
    assert ignored == []


@pytest.mark.criterion("C2")
def test_several_yearless_failures_all_ignored_in_order():
    # oracle: Requirement 2 by hand — every ignorable period goes, order kept on both sides
    a, b, c = _failed("each year"), _failed("YoY"), _failed("annual")
    fy = _p("fiscal 2022", 2022)
    q = _failed("Q2")
    kept, ignored = partition_yearless_periods([a, fy, b, q, c])
    assert kept == [fy, q]
    assert ignored == [a, b, c]


@pytest.mark.criterion("C2")
def test_empty_input():
    # oracle: Requirement 2 — nothing in, nothing out; the defect case as control
    fy, yoy = _p("fiscal 2024", 2024), _failed("year-over-year")
    assert partition_yearless_periods([fy, yoy]) == ([fy], [yoy])  # control
    assert partition_yearless_periods([]) == ([], [])


# A hand-labelled pool: (mention, carries year evidence by Requirement 1).
POOL = [("fiscal 2024", True), ("2023", True), ("Q3", True), ("last fiscal year", True),
        ("most recent", True), ("nine months", True), ("December 31", True),
        ("year-over-year", False), ("each year", False), ("annual filing", False),
        ("FY", False), ("the year ended", False), ("YoY", False), ("", False)]
EVIDENCE = dict(POOL)


@st.composite
def _periods(draw):
    n = draw(st.integers(1, 7))
    out = []
    for _ in range(n):
        mention, _ev = draw(st.sampled_from(POOL))
        ambiguous = draw(st.booleans())
        out.append(_p(mention, None if ambiguous else 2024, ambiguous,
                      "could_not_resolve_year" if ambiguous else None))
    return out


@pytest.mark.criterion("C2")
@given(_periods())
def test_partition_properties(periods):
    # oracle: Requirement 2 applied by hand with the pool's hand labels
    before = copy.deepcopy(periods)
    kept, ignored = partition_yearless_periods(periods)
    any_resolved = any(not p.ambiguous for p in periods)
    expected_ignored = [p for p in periods
                        if any_resolved and p.ambiguous and not EVIDENCE[p.mention]]
    expected_kept = [p for p in periods if not any(p is q for q in expected_ignored)]
    assert [id(p) for p in ignored] == [id(p) for p in expected_ignored]
    assert [id(p) for p in kept] == [id(p) for p in expected_kept]
    assert all(any(p is k for k in kept) for p in periods if not p.ambiguous)
    assert periods == before


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(_periods())
def test_partition_is_idempotent_on_kept(periods):
    # oracle: Requirement 2 — kept holds no ignorable period next to a resolved one, so a
    # second pass ignores nothing; and the two lists interleave back to the input
    kept, ignored = partition_yearless_periods(periods)
    assert len(kept) + len(ignored) == len(periods)
    assert sorted(map(id, kept + ignored)) == sorted(map(id, periods))
    kept2, ignored2 = partition_yearless_periods(list(kept))
    assert [id(p) for p in kept2] == [id(p) for p in kept]
    assert ignored2 == []
