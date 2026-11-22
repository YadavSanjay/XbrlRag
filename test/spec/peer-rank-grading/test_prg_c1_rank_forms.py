"""C1: the rank expressions ``stated_ranks`` reads (docs/specs/peer-rank-grading.md, req. 1).

Every text names only the subject DEVA, so each expression is DEVA's; the expected ranks are
read by hand from the text.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import stated_ranks

NAMES = {"DEVA": ["Deva Corp"]}

# Written out independently of the implementation: the ordinal words 1-20, then tens.
_UNITS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
          "ninth"]
_TEENS = ["tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth",
          "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth"]


def word_ordinal(n: int, sep: str = "-") -> str:
    if n <= 9:
        return _UNITS[n - 1]
    if n <= 20:
        return _TEENS[n - 10]
    tens = {2: "twenty", 3: "thirty", 4: "forty"}[n // 10]
    if n % 10 == 0:
        return {30: "thirtieth", 40: "fortieth"}[n]
    return f"{tens}{sep}{_UNITS[n % 10 - 1]}"


def numeric_ordinal(n: int) -> str:
    if n % 100 in (11, 12, 13):
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("text", "expected"), [
    ("DEVA ranks 2nd of the 4 peers.", [2]),
    ("DEVA ranks second of the 4 peers.", [2]),
    ("DEVA is #2 among the peers.", [2]),
    ("DEVA is No. 2 among the peers.", [2]),
    ("DEVA holds rank 2 among the peers.", [2]),
    ("DEVA ranked 2 of 4.", [2]),
    ("DEVA had the second-highest revenue.", [2]),
    ("DEVA ranks twenty-first.", [21]),
    ("DEVA ranks 21st of 33.", [21]),
    ("DEVA had the highest revenue of the 4 peers.", [1]),
    ("Deva Corp ranked 14th of the 14 covered companies.", [14]),
])
def test_rank_forms(text, expected):
    # oracle: requirement 1 — each form read by hand ("of the 4", "of 33" are counts, not
    # ranks: no rank word directly precedes them)
    assert stated_ranks(text, "DEVA", NAMES) == expected


@pytest.mark.criterion("C1")
def test_not_ranks_beside_a_rank():
    # oracle: requirement 1 "Not ranks" — each sentence holds one non-rank and the first
    # sentence the one real rank (3); only [3] is read.
    text = ("DEVA ranks 3rd of the 4 peers. DEVA had the second-lowest margin. "
            "DEVA grew in the first quarter. DEVA's year ended October 30th. "
            "DEVA ranked last on cash. DEVA ranked FY2022 revenue above FY2021. "
            "DEVA keeps rank 2022 data. The next-highest was DEVA.")
    assert stated_ranks(text, "DEVA", NAMES) == [3]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "DEVA had the second-lowest margin.",
    "DEVA grew in the first quarter.",
    "DEVA's year ended October 30th.",
    "DEVA ranked last on cash.",
    "The next-highest was DEVA.",
])
def test_each_non_rank_reads_nothing_but_a_rank_after_it_does(text):
    # oracle: requirement 1 — the non-rank reads nothing; a following "ranks 4th" sentence
    # is the only rank
    assert stated_ranks(text + " DEVA ranks 4th.", "DEVA", NAMES) == [4]


@pytest.mark.criterion("C1")
@given(n=st.integers(min_value=1, max_value=49), sep=st.sampled_from(["-", " "]))
def test_ordinal_forms_agree(n, sep):
    # oracle: requirement 1 — the numeric ordinal, the word ordinal and "#n" are one rank n
    for form in (numeric_ordinal(n), word_ordinal(n, sep), f"#{n}"):
        assert stated_ranks(f"DEVA ranks {form} among the peers.", "DEVA", NAMES) == [n]


_FILLER = st.sampled_from([
    "The comparison uses fiscal years.",
    "Period ends differ across companies.",
    "The median could not be stated.",
    "Amounts are in USD as reported.",
])


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(filler=_FILLER, where=st.integers(min_value=0, max_value=2))
def test_filler_sentences_change_nothing(filler, where):
    # oracle: requirement 1 — a sentence with no digits, ordinals or mentions holds no rank
    # expression and is no mention, so inserting it anywhere leaves DEVA's ranks [2, 2]
    sentences = ["DEVA ranks 2nd of the peers.", "Its revenue was large.",
                 "DEVA is second by revenue."]
    sentences.insert(where, filler)
    assert stated_ranks(" ".join(sentences), "DEVA", NAMES) == [2, 2]
