"""C1: ``member_list`` reads a member-list universe (docs/specs/named-company-screen.md, R1).

Pure. Expected values are Requirement 1 applied by hand (points) or computed in the test
(property): the distinct ``strip().upper()`` tickers, sorted, when there are two or more and
every item is a non-blank string; None otherwise.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.named_company_screen import member_list

TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]


def _variants(ticker):
    return st.sampled_from([ticker, ticker.lower(), f" {ticker}", f"{ticker.title()} "])


@pytest.mark.criterion("C1")
def test_c1_member_list_points():
    # oracle: Requirement 1 by hand — stripped, upper-cased, de-duplicated, sorted
    assert member_list(["msft", " AAPL ", "GOOGL"]) == ["AAPL", "GOOGL", "MSFT"]
    assert member_list(["AAPL", "MSFT", "aapl"]) == ["AAPL", "MSFT"]
    assert member_list(["NVDA", "AMZN"]) == ["AMZN", "NVDA"]


@pytest.mark.criterion("C1")
def test_c1_not_a_member_list_is_none():
    # oracle: Requirement 1 by hand — strings, None, fewer than two distinct tickers, a
    # non-string or blank item, and non-list types read as no member list
    for value in ("covered", "big_tech", None, [], ["AAPL"], ["AAPL", "aapl"], ["AAPL", 7],
                  ["AAPL", ""], ["AAPL", "  "], ("AAPL", "MSFT"), {"AAPL": 1, "MSFT": 2},
                  ["AAPL", None], 7):
        assert member_list(value) is None, value
    # A valid list is read (so None above is a decision, not a constant).
    assert member_list(["AAPL", "MSFT"]) == ["AAPL", "MSFT"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.lists(st.sampled_from(TICKERS).flatmap(_variants), max_size=7), st.randoms())
def test_c1_member_list_rule_for_any_list(items, rnd):
    # oracle: Requirement 1 computed in the test; the result does not depend on order or case
    distinct = sorted({i.strip().upper() for i in items})
    expected = distinct if len(distinct) >= 2 else None
    assert member_list(items) == expected
    shuffled = list(items)
    rnd.shuffle(shuffled)
    assert member_list(shuffled) == expected
    assert member_list([i.swapcase() for i in items]) == expected
