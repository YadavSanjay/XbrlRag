"""C4: a compound question's dependent sub-query reaches the router with the prior screen's
companies and their ranks (docs/specs/ranked-top-injection.md, Requirements 1 and 3)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

SQ2 = ("For the company ranked first among <<TOP:sq1:3>>, state the growth in its accounts "
       "receivable for fiscal year 2024.")


def _labels(tickers, n):
    """Requirement 1 by hand for rows that all name a ticker."""
    top = tickers[:n]
    if n == 1:
        return top[0]
    return ", ".join(f"{t} (#{i})" for i, t in enumerate(top, start=1))


@pytest.mark.criterion("C4")
def test_the_leader_question_reaches_the_router_with_ranks(h):
    # oracle: Requirements 1 and 3 by hand — the screen returned CCC, AAA, BBB in that order
    routed = h.Kit(SQ2, ["CCC", "AAA", "BBB"]).routed_queries()
    assert routed == [
        h.SQ1,
        "For the company ranked first among CCC (#1), AAA (#2), BBB (#3), state the growth in its "
        "accounts receivable for fiscal year 2024.",
    ]


@pytest.mark.criterion("C4")
def test_a_top_one_token_stays_bare_while_a_top_two_token_is_labelled(h):
    # oracle: Requirement 1 by hand — n == 1 injects the rank-1 ticker with no label (as today);
    # n == 2 in the same sub-query injects the first two with their ranks
    sq2 = ("For <<TOP:sq1:1>>, compared with <<TOP:sq1:2>>, state the growth in its accounts "
           "receivable for fiscal year 2024.")
    routed = h.Kit(sq2, ["CCC", "AAA", "BBB"]).routed_queries()
    assert routed[1] == ("For CCC, compared with CCC (#1), AAA (#2), state the growth in its "
                         "accounts receivable for fiscal year 2024.")


# Words around the tokens; each starts and ends with a letter so the routed text is exact.
_TEXT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ,", max_size=20).map(lambda s: f"x{s}y")
# The fixed tail keeps the sub-query's own year, so no year is inherited
# (docs/specs/dependent-subquery-period.md).
_TAIL = " state the growth for fiscal year 2024."


@pytest.mark.criterion("C4")
@settings(max_examples=15, deadline=None)
@given(order=st.permutations(["AAA", "BBB", "CCC", "DDD"]), n=st.integers(1, 6),
       m=st.integers(1, 6), before=_TEXT, between=_TEXT, two=st.booleans())
def test_any_screen_order_and_token_is_injected_by_the_rule(h, order, n, m, before, between, two):
    # oracle: Requirements 1 and 3 by hand — each token replaced by its own text, nothing else moves
    sq2 = f"{before} <<TOP:sq1:{n}>> {between}"
    expected = f"{before} {_labels(order, n)} {between}"
    if two:
        sq2 += f" <<TOP:sq1:{m}>>"
        expected += f" {_labels(order, m)}"
    routed = h.Kit(sq2 + _TAIL, list(order)).routed_queries()
    assert routed == [h.SQ1, expected + _TAIL]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_reversing_the_screen_moves_the_first_rank(h):
    # oracle: Requirement 1 — an ascending screen of the same filers reports them in reverse, so
    # the other end carries (#1); the injector never re-sorts
    desc = h.Kit(SQ2, ["AAA", "BBB", "CCC"]).routed_queries()[1]
    asc = h.Kit(SQ2, ["CCC", "BBB", "AAA"]).routed_queries()[1]
    assert "AAA (#1), BBB (#2), CCC (#3)" in desc
    assert "CCC (#1), BBB (#2), AAA (#3)" in asc
