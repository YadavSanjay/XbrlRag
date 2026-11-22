"""C3: "total" as an amount word asks for the group's sum
(docs/specs/group-sum-cue.md, Requirement 1 step 3, second rule).

Every phrasing is written for this spec. ``_words`` lower-cases and splits on anything but
``[a-z0-9&]``: "vs." reads as ``vs``, "long-term" as ``long`` ``term``, "R&D" as ``r&d``,
"FY2022" as one word ``fy2022`` (not a digit token, so not a count) and "3" as ``3``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.universe_routing import aggregate_cue, group_aggregate_cue

# Requirement 1's blocking words, written out. "median" is left out of the metamorphic
# insertion: it is also an aggregate_cue word, so step 1 returns "median" before step 3.
RANK_WORDS = [
    "top", "bottom", "highest", "lowest", "largest", "smallest", "biggest", "most", "least",
    "rank", "ranks", "ranked", "ranking", "which", "who", "whose", "each", "every", "per",
    "versus", "vs", "compare", "compared", "comparison", "than", "list", "breakdown", "split",
    "average", "mean",
]
PER_MEMBER_PAIRS = ["by company", "by companies", "by filer", "by member", "by ticker"]
NUMBER_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
                "eighteen", "nineteen", "twenty"]

# One-group "total" questions written for this spec, with no blocking word.
GROUPS = [("the retailers", "retailers"), ("the semiconductor companies", "semiconductors"),
          ("the big tech companies", "big_tech"), ("the covered companies", "covered")]
METRICS = ["revenue", "net income", "R&D expense", "long-term debt", "inventories",
           "capital expenditures", "interest expense", "assets"]
TOTAL_WORD = ["total", "totals", "Total", "TOTAL"]
TEMPLATES = [
    "{t} {m}, {g}",
    "What was the {t} {m} of {g}?",
    "{g}: {t} {m}",
    "Give the {t} for {m} across {g}",
]
YEARS = ["2000", "2021", "2024", "2099"]


def _q(template, total, metric, group_text, year=None):
    q = template.format(t=total, m=metric, g=group_text)
    return q if year is None else f"{q} in fiscal {year}"


def _insert(q, word, where):
    if where == "prefix":
        return f"{word} {q}"
    if where == "suffix":
        return f"{q} {word}"
    head, _, tail = q.partition(" ")
    return f"{head} {word} {tail}"


@pytest.mark.criterion("C3")
def test_total_as_an_amount_word_is_a_sum():
    # oracle: Requirement 1 step 3 by hand — "total"/"totals" present, one universe, no
    # company, and none of the blocking words, pairs or counts ("fiscal", "year", "across",
    # "give", "for", "spend" are not listed; 2022-2025 are years; "fy2022" is not all digits).
    cases = [
        ("total long-term debt, the semiconductor companies, fiscal 2025", "semiconductors"),
        ("What was the total R&D spend across the big tech companies in fiscal year 2023?",
         "big_tech"),
        ("Give the totals for net income of the retailers, FY2022", "retailers"),
    ]
    for q, universe in cases:
        assert aggregate_cue(q) is None
        assert group_aggregate_cue(q, [universe], 0) == "sum"


@pytest.mark.criterion("C3")
def test_total_with_a_blocking_word_or_precondition_failure_is_none():
    # oracle: Requirement 1 by hand — each None case names its rule; the control question has
    # "total" and nothing blocking.
    control = "total revenue of the retailers in 2023"
    assert group_aggregate_cue(control, ["retailers"], 0) == "sum"
    # rank words "which", "highest"
    assert group_aggregate_cue("Which retailer had the highest total assets in 2024?",
                               ["retailers"], 0) is None
    # rank word "top" and the count "3"
    assert group_aggregate_cue("top 3 covered companies by total revenue, fiscal 2025",
                               ["covered"], 0) is None
    # the per-member pair "by company"
    assert group_aggregate_cue("total revenue by company for the retailers in 2023",
                               ["retailers"], 0) is None
    # the number word "five"
    assert group_aggregate_cue("the five big tech companies' total assets in 2024",
                               ["big_tech"], 0) is None
    # "vs." reads as the rank word "vs", and the question names two universes
    assert group_aggregate_cue("total revenue, the retailers vs. the semiconductor companies",
                               ["retailers", "semiconductors"], 0) is None
    # one resolved company (step 2)
    assert group_aggregate_cue("Apple's total revenue, fiscal 2024", [], 1) is None
    assert group_aggregate_cue("Apple's total revenue, fiscal 2024", ["big_tech"], 1) is None


@pytest.mark.criterion("C3")
def test_a_year_is_not_a_count_but_other_digit_tokens_are():
    # oracle: Requirement 1 step 3 by hand — a digit token is a count unless it is an integer
    # from 2000 to 2099; the bounds 2000 and 2099 are years, 1999 and 2100 are counts.
    def cue(q):
        return group_aggregate_cue(q, ["retailers"], 0)

    assert cue("total revenue of the retailers in 2024") == "sum"
    assert cue("total revenue of the retailers in 2000") == "sum"
    assert cue("total revenue of the retailers in 2099") == "sum"
    assert cue("total revenue of the 12 retailers in 2024") is None
    assert cue("total revenue of the retailers in 1999") is None
    assert cue("total revenue of the retailers in 2100") is None


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("word", RANK_WORDS)
def test_every_listed_blocking_word_blocks_total(word):
    # oracle: Requirement 1 step 3's list by hand — the base question is a sum; adding one
    # listed word (median excepted, see RANK_WORDS) makes it None.
    base = "total revenue of the retailers in 2024"
    assert group_aggregate_cue(base, ["retailers"], 0) == "sum"
    assert group_aggregate_cue(f"{base} {word}", ["retailers"], 0) is None
    assert group_aggregate_cue(f"{word.upper()} {base}", ["retailers"], 0) is None


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("blocker", PER_MEMBER_PAIRS + NUMBER_WORDS)
def test_every_per_member_pair_and_number_word_blocks_total(blocker):
    # oracle: Requirement 1 step 3's lists by hand — each pair or number word blocks; "by"
    # alone in the base question ("by fiscal 2024") is not a listed pair.
    base = "total revenue of the retailers by fiscal 2024"
    assert group_aggregate_cue(base, ["retailers"], 0) == "sum"
    assert group_aggregate_cue(f"{base}, {blocker}", ["retailers"], 0) is None


@pytest.mark.criterion("C3")
@given(
    template=st.sampled_from(TEMPLATES),
    total=st.sampled_from(TOTAL_WORD),
    metric=st.sampled_from(METRICS),
    group=st.sampled_from(GROUPS),
    year=st.sampled_from(YEARS),
    blocker=st.one_of(
        st.sampled_from(RANK_WORDS),
        st.sampled_from(NUMBER_WORDS),
        st.integers(min_value=0, max_value=1999).map(str),
        st.integers(min_value=2100, max_value=999999).map(str),
    ),
    where=st.sampled_from(["prefix", "suffix", "middle"]),
)
def test_a_blocking_word_or_non_year_number_turns_a_total_sum_into_none(
        template, total, metric, group, year, blocker, where):
    # oracle: Requirement 1 step 3 — the generated question has "total"/"totals" and nothing
    # blocking, so it is a sum; adding one listed blocking word, number word or non-year digit
    # token as a whole word makes it None.
    group_text, universe = group
    base = _q(template, total, metric, group_text, year)
    assert aggregate_cue(base) is None
    assert group_aggregate_cue(base, [universe], 0) == "sum"
    assert group_aggregate_cue(_insert(base, blocker, where), [universe], 0) is None


@pytest.mark.criterion("C3")
@given(
    template=st.sampled_from(TEMPLATES),
    total=st.sampled_from(TOTAL_WORD),
    metric=st.sampled_from(METRICS),
    group=st.sampled_from(GROUPS),
    year=st.sampled_from(YEARS),
    blocker=st.one_of(st.none(), st.sampled_from(RANK_WORDS), st.sampled_from(NUMBER_WORDS)),
)
def test_adding_or_removing_a_year_never_changes_the_result(template, total, metric, group,
                                                            year, blocker):
    # oracle: Requirement 1 step 3 — an integer from 2000 to 2099 is not a count and no other
    # rule reads years, so the question with and without "in fiscal <year>" agree: a sum when
    # nothing blocks, None when a blocker is present.
    group_text, universe = group
    without = _q(template, total, metric, group_text)
    with_year = _q(template, total, metric, group_text, year)
    if blocker is not None:
        without, with_year = f"{blocker} {without}", f"{blocker} {with_year}"
    expected = "sum" if blocker is None else None
    assert group_aggregate_cue(without, [universe], 0) == expected
    assert group_aggregate_cue(with_year, [universe], 0) == expected
    # Guard: the unblocked form of this question is a sum, so the test fails a None answer.
    assert group_aggregate_cue(_q(template, total, metric, group_text, year), [universe],
                               0) == "sum"


@pytest.mark.criterion("C3")
@given(
    template=st.sampled_from(TEMPLATES),
    metric=st.sampled_from(METRICS),
    group=st.sampled_from(GROUPS),
    year=st.sampled_from(YEARS),
    blocker=st.one_of(st.none(), st.sampled_from(RANK_WORDS)),
    flips=st.lists(st.booleans(), min_size=200, max_size=200),
)
def test_letter_case_never_matters(template, metric, group, year, blocker, flips):
    # oracle: Requirement 1 step 3 reads the question as _words does, which lower-cases first,
    # so any per-letter case change gives the same result as the lower-case question.
    group_text, universe = group
    q = _q(template, "total", metric, group_text, year)
    if blocker is not None:
        q = f"{blocker} {q}"
    flipped = "".join(c.upper() if f else c.lower() for c, f in zip(q, flips + [False] * len(q),
                                                                    strict=False))
    expected = "sum" if blocker is None else None
    assert group_aggregate_cue(q.lower(), [universe], 0) == expected
    assert group_aggregate_cue(flipped, [universe], 0) == expected
    assert group_aggregate_cue(q.upper(), [universe], 0) == expected
    # Guard: the unblocked question is a sum.
    assert group_aggregate_cue(_q(template, "TOTAL", metric, group_text, year), [universe],
                               0) == "sum"
