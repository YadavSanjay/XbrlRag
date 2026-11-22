"""C3: stored copies of period lengths and ranks are dropped from the graded values
(docs/specs/grader-v9-number-reading.md, requirement 4)."""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import unexempt_values


@pytest.mark.criterion("C3")
def test_the_note_day_counts_are_dropped(kit):
    # oracle: requirement 4 — 364 and 371 are stated only as day counts
    assert unexempt_values(kit.NOTE, [364.0, 371.0]) == []
    text = "Revenue grew 2.02% to $391.0 billion. " + kit.NOTE
    assert unexempt_values(text, [2.02, 391.0e9, 364.0, 371.0]) == [2.02, 391.0e9]


@pytest.mark.criterion("C3")
def test_a_value_also_stated_as_an_amount_is_kept():
    # oracle: requirement 4 — "3rd" is a rank (exempt), "3 stores" an amount (n_A = 1): of
    # two stored 3s the earliest is dropped
    text = "DEVA ranked 3rd; it has 3 stores."
    assert unexempt_values(text, [3.0, 3.0]) == [3.0]


@pytest.mark.criterion("C3")
def test_a_signed_rank_and_order():
    # oracle: requirement 4 — the old reader signed "fell to #3" as -3; |v| matches the rank
    assert unexempt_values("DEVA fell to #3.", [-3.0]) == []
    text = "DEVA ranked 1st with $500 million; DEVB ranked 2nd with $400 million."
    assert unexempt_values(text, [1.0, 500e6, 2.0, 400e6]) == [500e6, 400e6]
    assert unexempt_values(text, [400e6, 7.0, 500e6]) == [400e6, 7.0, 500e6]


AMOUNTS = st.sampled_from([1.0, 2.0, 3.0, 364.0, 371.0, 53.0, 300e6, 0.25, -3.0])
PROSE = st.sampled_from(["", "DEVA has 3 stores.", "Revenue was $300 million.",
                         "There were 2 deals and 53 sites."])
# Each tail, with the values it states only as ranks or period lengths (by hand).
EXEMPT = {
    "": set(),
    "DEVA ranked 1st, DEVB 2nd and DEVC #3.": {1.0, 2.0, 3.0},
    "Fiscal 2024 had 53 weeks.": {53.0},
    ("2023-10-01 to 2024-09-28 has 364 days and 2022-09-25 to 2023-09-30 has 371 days."):
        {364.0, 371.0},
}
TAILS = st.sampled_from(sorted(EXEMPT))


@pytest.mark.criterion("C3")
@given(prose=PROSE, tail=TAILS, values=st.lists(AMOUNTS, max_size=8))
def test_only_removes_in_order_and_is_idempotent(prose, tail, values):
    # oracle: requirement 4 — a sub-multiset of values, in order; a second pass changes
    # nothing; a text with no exempt number keeps everything; a copy the prose states
    # as an amount survives
    text = f"{prose} {tail}".strip()
    out = unexempt_values(text, values)
    assert not Counter(out) - Counter(values)
    it = iter(values)
    assert all(any(v == w for w in it) for v in out)
    assert unexempt_values(text, out) == out
    assert unexempt_values(prose, values) == list(values)
    words = prose.replace(".", " ").split()
    for v in set(values):
        if abs(v) in EXEMPT[tail]:
            # the prose states only positive integers as amounts
            stated = sum(1 for w in words if v > 0 and w == f"{int(v)}")
            assert out.count(v) == min(values.count(v), stated)
        else:
            assert out.count(v) == values.count(v)
