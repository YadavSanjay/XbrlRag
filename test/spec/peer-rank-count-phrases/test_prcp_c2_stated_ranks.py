"""C2: ``stated_ranks`` drops count phrases (docs/specs/peer-rank-count-phrases.md, req. 2).

Expected ranks are read by hand from each text with peer-rank-grading's rules, after
removing the count phrase.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import stated_ranks

NAMES = {
    "MCD": ["McDonald's", "McDonald's Corporation"],
    "AAPL": ["Apple", "Apple Inc."],
    "INTC": ["Intel", "Intel Corporation"],
    "NFLX": ["Netflix"],
    "META": ["Meta"],
    "MRK": ["Merck"],
}
COUNT = "I ranked 32 of the 33 covered companies. "


@pytest.mark.criterion("C2")
def test_mcdonalds_answer_reads_17(kit):
    # oracle: the key's rank 17; "I ranked 32" is the author's count (requirement 2)
    assert stated_ranks(kit.MCD_TEXT, "MCD", NAMES) == [17]


@pytest.mark.criterion("C2")
def test_we_ranked_count_then_apple_third():
    # oracle: requirement 2 — "We ranked 24" is a count; "Apple is 3rd" is AAPL's rank 3
    assert stated_ranks("We ranked 24 of the 30 filers. Apple is 3rd.", "AAPL", NAMES) == [3]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("rest", "ticker", "expected"), [
    ("Intel ranked 14 of 14.", "INTC", [14]),
    ("Netflix holds rank 19.", "NFLX", [19]),
    ("Meta is #2.", "META", [2]),
    ("Merck ranks 17th.", "MRK", [17]),
    ("It ranks 20th of the 33.", "MCD", [20]),
])
def test_other_forms_keep_their_meaning_after_a_count(rest, ticker, expected):
    # oracle: requirement 2 — only the first-person phrase is dropped; the rest is read with
    # peer-rank-grading requirement 1 by hand ("It ranks 20th" names no one: the default)
    assert stated_ranks(COUNT + rest, ticker, NAMES) == expected


@pytest.mark.criterion("C2")
def test_only_count_phrases_give_no_rank():
    # oracle: requirement 2 — both numbered ranks have a listed subject ("I have", "we've")
    assert stated_ranks("I have ranked 30 companies; we've ranked 5 more.", "MCD", NAMES) == []


_RESTS = [
    "McDonald's is 17th.",
    "Intel ranked 14 of 14. McDonald's ranks 3rd.",
    "Apple is #2, just ahead of Intel (rank 3).",
    "Merck had the highest value. That ranks first of the 33.",
    "The 12th-ranked (MRK) trails Netflix, which is No. 11.",
    "",
    "No rank is stated.",
]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(rest=st.sampled_from(_RESTS),
       ticker=st.sampled_from(sorted(NAMES)),
       subject=st.sampled_from(["I", "We", "I've", "We've", "I have", "We have"]),
       word=st.sampled_from(["rank", "ranks", "ranked", "ranking"]),
       n=st.integers(min_value=1, max_value=999),
       m=st.integers(min_value=1, max_value=999))
def test_a_count_prefix_never_changes_the_ranks(rest, ticker, subject, word, n, m):
    # oracle: requirement 2 — the prefix mentions no candidate and holds only a count phrase,
    # so it adds no expression and moves no owner
    prefix = f"{subject} {word} {n} of the {m} covered companies. "
    assert stated_ranks(prefix + rest, ticker, NAMES) == stated_ranks(rest, ticker, NAMES)
