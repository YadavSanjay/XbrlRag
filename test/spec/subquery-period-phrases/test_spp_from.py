"""C3-C5: a lone "from <year>" is a base year (docs/specs/subquery-period-phrases.md, R2).

Every expected value is Requirement 2 applied by hand to synthetic text: the base year is the
one year a sub-query writes after "from" with no end, quarter, relative phrase or date; the end
is the question's one later year, else base + 1; the rewrite replaces the year part after
"from" by "fiscal year B to fiscal year E". All results are exact.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.extract_year_util import extract_years_in_range
from src.retriever.xbrl.subquery_period import from_base_year, from_end_year, with_from_end_year

DEFECT_A_SQ = "For <<TOP:sq1:1>>, what was the accumulated earnings growth from FY24?"
DEFECT_A_Q = ("Which 3 covered companies had the largest Net Income in FY25, and what was the "
              "accumulated earnings growth of the first-ranked one from FY24?")
# The from-phrase forms of Requirement 2.1 (after "from "), mixed case.
FROM_FORMS = [
    lambda y: f"FY{y % 100:02d}", lambda y: f"FY{y}", lambda y: f"FY {y}",
    lambda y: f"FY'{y % 100:02d}", lambda y: f"fiscal {y}", lambda y: f"fiscal year {y}",
    lambda y: f"{y}", lambda y: f"Fiscal Year {y}",
]
# Carrier sub-queries with no year or period phrase of their own; {f} is the from-phrase.
CARRIERS = [
    "For <<TOP:sq1:1>>, what was the accumulated earnings growth {f}?",
    "How did revenue change for <<TOP:sq2:3>> {f}.",
    "By how much did the leader's capital expenditures grow {f}",
]
BASES = st.integers(min_value=2018, max_value=2034)


def _carrier(template, phrase):
    return template.replace("{f}", phrase)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
@pytest.mark.parametrize("text, base", [
    (DEFECT_A_SQ, 2024),
    ("For <<TOP:sq1:1>>, how did revenue grow from fiscal 2024?", 2024),
    ("For <<TOP:sq1:1>>, how did revenue grow from fiscal year 2024?", 2024),
    ("For <<TOP:sq1:1>>, how did revenue grow from 2024?", 2024),
    ("From FY2024, how did revenue change for <<TOP:sq1:1>>?", 2024),
    ("For <<TOP:sq1:1>>, how did revenue grow from FY 2024?", 2024),
    ("For <<TOP:sq1:1>>, how did revenue grow from FY'23?", 2023),
])
def test_c3_a_lone_from_year_is_the_base(text, base):
    # oracle: Requirement 2.1 by hand — one year in 2018-2035, written after "from" in a listed
    # form, no end word after it, no period phrase, no date
    assert from_base_year(text) == base


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("text", [
    "For <<TOP:sq1:1>>, how did revenue grow from FY24 to FY25?",
    "For <<TOP:sq1:1>>, how did revenue grow from FY24 through FY25?",
    "For <<TOP:sq1:1>>, how did revenue grow between FY24 and FY25?",
    "For <<TOP:sq1:1>>, how did revenue grow from FY24 to date?",
    "For <<TOP:sq1:1>>, how did revenue grow from Q3 FY24?",
    "For <<TOP:sq1:1>>, how did revenue grow from FY24 to the latest fiscal year?",
    "For <<TOP:sq1:1>>, what was revenue in FY24?",
    "For <<TOP:sq1:1>>, how did revenue grow from the prior year?",
    "For <<TOP:sq1:1>>, how did revenue grow from FY2040?",
    "For <<TOP:sq1:1>>, how did revenue grow from the fiscal year ended June 30, 2024?",
    "For <<TOP:sq1:1>>, how did revenue grow from 24?",
    "For <<TOP:sq1:1>>, how did revenue grow from <<TOP:fy2024:1>>?",
    "For <<TOP:sq1:1>>, what was revenue from FY24?",
    "For <<TOP:sq1:1>>, what was the total debt from fiscal 2024?",
])
def test_c3_no_lone_from_year_is_none(text):
    # oracle: Requirement 2.1 by hand — an end ("to", "through", "to date"), two years, a
    # quarter or relative phrase, no "from", no year, a year out of bounds, a date, a bare
    # two-digit number, a year only inside a <<TOP…>> token, or a lookup with no growth or
    # change word (a point in time, not a base). The paired defect-A call keeps
    # the test failing against a stub returning None.
    assert from_base_year(text) is None
    assert from_base_year(DEFECT_A_SQ) == 2024


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(template=st.sampled_from(CARRIERS), form=st.sampled_from(FROM_FORMS), base=BASES,
       gap=st.integers(min_value=1, max_value=5),
       end=st.sampled_from([" to FY{e}", " through fiscal {e}", " to date", " until {e}",
                            " - {e}"]))
def test_c3_property_lone_from_and_what_breaks_it(template, form, base, gap, end):
    # oracle: metamorphic, Requirement 2.1 — "from <form of B>" alone gives B; an end after it,
    # a quarter in front of the year, or "in" in place of "from" gives None
    assert from_base_year(_carrier(template, f"from {form(base)}")) == base
    end_text = end.replace("{e}", str(base + gap))
    assert from_base_year(_carrier(template, f"from {form(base)}{end_text}")) is None
    assert from_base_year(_carrier(template, f"from Q3 {form(base)}")) is None
    assert from_base_year(_carrier(template, f"in {form(base)}")) is None


# Requirement 2.1 condition 6, copied from the spec.
GROWTH_WORDS = [
    "grow", "grows", "grew", "grown", "growing", "growth", "change", "changes", "changed",
    "increase", "increases", "increased", "decrease", "decreases", "decreased", "rise", "rises",
    "rose", "risen", "fall", "falls", "fell", "fallen", "decline", "declines", "declined",
    "cagr",
]
# A carrier whose only growth or change word is {w}; {f} is the from-phrase.
WORD_CARRIER = "For <<TOP:sq1:1>>, what was the {w} in revenue {f}?"


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(word=st.sampled_from(GROWTH_WORDS), case=st.sampled_from([str.lower, str.upper,
                                                                 str.capitalize]),
       neutral=st.sampled_from(["level", "amount", "value"]),
       form=st.sampled_from(FROM_FORMS), base=BASES)
def test_c3_property_the_growth_word_gates_the_base(word, case, neutral, form, base):
    # oracle: metamorphic, Requirement 2.1 condition 6 — with any listed growth or change word
    # (any case) the lone from-year is the base; replacing that word by a neutral one makes the
    # text a lookup, so it gives None
    phrase = f"from {form(base)}"
    with_word = WORD_CARRIER.replace("{w}", case(word)).replace("{f}", phrase)
    without = WORD_CARRIER.replace("{w}", neutral).replace("{f}", phrase)
    assert from_base_year(with_word) == base
    assert from_base_year(without) is None


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
@pytest.mark.parametrize("question, base, end", [
    (DEFECT_A_Q, 2024, 2025),
    ("Which 3 covered companies had the largest Net Income in FY24, and how did the leader's "
     "earnings grow?", 2024, 2025),
    ("Which 3 covered companies had the largest Net Income in FY23, and how did the leader's "
     "earnings grow from FY21?", 2021, 2023),
    ("Compare the top 3 by Net Income in FY25 and in FY26 on earnings growth.", 2024, 2025),
    ("Which 3 covered companies had the largest Net Income in FY23?", 2024, 2025),
])
def test_c4_the_end_year(question, base, end):
    # oracle: Requirement 2.2 by hand — the one stated year above the base, else base + 1
    # (FY25 and FY26 are two later years; FY23 is below a 2024 base)
    assert from_end_year(question, base) == end


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(base=BASES, gap=st.integers(min_value=1, max_value=8),
       gap2=st.integers(min_value=1, max_value=8),
       below=st.lists(st.integers(min_value=1, max_value=10), max_size=2))
def test_c4_property_one_later_year_or_base_plus_one(base, gap, gap2, below):
    # oracle: metamorphic, Requirement 2.2 — one later year E is the end, whatever years below
    # the base the question also states; two distinct later years, or none, give base + 1
    earlier = "".join(f" Also show FY{base - d}." for d in below)
    later = base + gap
    q_one = f"Top 3 by Net Income in FY{later}: how did earnings grow from FY{base}?{earlier}"
    assert from_end_year(q_one, base) == later
    q_none = f"Top 3 by Net Income: how did earnings grow from FY{base}?{earlier}"
    assert from_end_year(q_none, base) == base + 1
    if gap2 != gap:
        q_two = f"{q_one} Compare with FY{base + gap2}."
        assert from_end_year(q_two, base) == base + 1


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_c5_the_rewrite():
    # oracle: Requirement 2.3 by hand — the year part after "from " becomes "fiscal year B to
    # fiscal year E"; "From", the whitespace after it and every other character are kept; a
    # text with no lone from-year is returned unchanged
    assert with_from_end_year(DEFECT_A_Q, DEFECT_A_SQ) == (
        "For <<TOP:sq1:1>>, what was the accumulated earnings growth from fiscal year 2024 to "
        "fiscal year 2025?")
    q24 = "Which 3 covered companies had the largest Net Income in FY24?"
    assert with_from_end_year(
        q24, "From fiscal 2023, how did revenue change for <<TOP:sq1:1>>?") == (
        "From fiscal year 2023 to fiscal year 2024, how did revenue change for <<TOP:sq1:1>>?")
    assert with_from_end_year(
        q24, "For <<TOP:sq1:1>>, how did revenue grow from  fiscal  year 2023?") == (
        "For <<TOP:sq1:1>>, how did revenue grow from  fiscal year 2023 to fiscal year 2024?")
    unchanged = "For <<TOP:sq1:1>>, how did revenue grow from FY23 to FY24?"
    assert with_from_end_year(q24, unchanged) == unchanged


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(template=st.sampled_from(CARRIERS), form=st.sampled_from(FROM_FORMS), base=BASES,
       gap=st.integers(min_value=1, max_value=9), stated=st.booleans())
def test_c5_property_rewrite_states_both_years(template, form, base, gap, stated):
    # oracle: metamorphic, Requirement 2.3 — exactly the year part is replaced, the result's
    # years run from B to E, it has no lone from-year and a second rewrite changes nothing
    end = base + gap if stated else base + 1
    question = (f"Top 3 by Net Income in FY{end}: how did earnings grow?" if stated
                else "Top 3 by Net Income: how did earnings grow?")
    text = _carrier(template, f"from {form(base)}")
    out = with_from_end_year(question, text)
    assert out == _carrier(template, f"from fiscal year {base} to fiscal year {end}")
    years = extract_years_in_range(out)
    assert (years[0], years[-1]) == (base, end)
    assert from_base_year(out) is None
    assert with_from_end_year(question, out) == out
