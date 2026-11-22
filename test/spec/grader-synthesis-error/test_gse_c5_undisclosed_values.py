"""C5: numbers a disclosure sentence states are not produced values
(docs/specs/grader-synthesis-error.md, requirement 4)."""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import undisclosed_values

U = "Universe: the retailers group (7 companies; 5 ranked for FY2025)."


@pytest.mark.criterion("C5")
def test_the_meta_values_are_all_disclosed(kit):
    # oracle: requirement 4 — 33 and 14 are stated only inside the universe sentence
    assert undisclosed_values(kit.META_TEXT, [33.0, 14.0]) == []


@pytest.mark.criterion("C5")
def test_a_value_also_stated_outside_is_kept():
    # oracle: requirement 4 — "5 stores" states 5 outside (n_A=1), U states 7 and 5 (n_D=1
    # each); of two stored 5s the earliest is dropped, 7 goes, the amounts stay in order
    text = f"DEVA has 5 stores and revenue of $500 million. {U}"
    assert undisclosed_values(text, [5.0, 500e6, 7.0, 5.0]) == [500e6, 5.0]


@pytest.mark.criterion("C5")
def test_order_and_unrelated_values_are_kept():
    # oracle: requirement 4 — values no disclosure sentence states are never touched
    text = f"Revenue was $300 million, then $200 million. {U}"
    assert undisclosed_values(text, [300e6, 7.0, 200e6, 5.0]) == [300e6, 200e6]
    assert undisclosed_values("Revenue was $300 million.", [300e6, 7.0]) == [300e6, 7.0]


AMOUNTS = st.sampled_from([5.0, 7.0, 14.0, 33.0, 300e6, 0.25])
PROSE = st.sampled_from(["", "DEVA has 5 stores.", "There were 7 deals and 33 sites.",
                         "Revenue was $300 million."])
TAILS = st.sampled_from(["", U,
                         "Universe: the covered companies (33 companies; 14 ranked for FY2022).",
                         U + " Not ranked: DEVX (metric not reported); DEVY (no fsds)."])


@pytest.mark.criterion("C5")
@given(prose=PROSE, tail=TAILS, values=st.lists(AMOUNTS, max_size=8))
def test_only_removes_in_order_and_is_idempotent(prose, tail, values):
    # oracle: requirement 4 — a sub-multiset of values, in their order; a second pass
    # changes nothing; a text with no disclosure sentence keeps everything
    text = f"{prose} {tail}".strip()
    out = undisclosed_values(text, values)
    assert not Counter(out) - Counter(values)
    it = iter(values)
    assert all(any(v == w for w in it) for v in out)
    assert undisclosed_values(text, out) == out
    assert undisclosed_values(prose, values) == list(values)
    if tail:
        # 7, 5 (or 33, 14) are stated in the tail; they survive only as copies the prose states
        for v in set(values):
            stated = prose.count(f"{int(v)} ") if v in (5.0, 7.0, 33.0, 14.0) else None
            if stated is not None and str(int(v)) in tail:
                assert out.count(v) == min(values.count(v), stated)
