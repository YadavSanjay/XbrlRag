"""C2: ``named_companies`` lists the intent's resolved tickers (docs/specs/named-company-screen.md,
R3).

The companies are real ``ResolvedCompany`` objects with synthetic CIKs. Expected values are
Requirement 3 by hand: resolved (truthy cik and ticker, not ambiguous) tickers, upper-cased,
first occurrence, no repeats.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.entity_extractor import ResolvedCompany
from src.retriever.xbrl.named_company_screen import named_companies


@pytest.mark.criterion("C2")
def test_c2_named_companies_point(kit):
    # oracle: Requirement 3 by hand — ambiguous, unresolved and ticker-less entries drop; the
    # second AAPL ("aapl") repeats one; the order is first occurrence
    companies = [
        kit.company("AAPL", "Apple"),
        kit.company("MSFT", "Microsoft"),
        kit.unresolved("Microchip", "ambiguous"),
        kit.unresolved("Initech"),
        kit.company("aapl", "apple inc"),
        ResolvedCompany(mention="Tickerless Co", cik="0000000999", ticker=None,
                        resolution="exact_name"),
        kit.company("GOOGL", "Alphabet"),
    ]
    assert named_companies(companies) == ["AAPL", "MSFT", "GOOGL"]
    assert named_companies([]) == []


@pytest.mark.criterion("C2")
def test_c2_ambiguous_with_ticker_is_not_named(kit):
    # oracle: Requirement 3 by hand — an ambiguous resolution never names a company, even with
    # a cik and ticker filled in
    amb = ResolvedCompany(mention="Micro", cik="0000000106", ticker="NVDA",
                          resolution="ambiguous")
    assert named_companies([amb, kit.company("MSFT"), kit.company("META")]) == ["MSFT", "META"]


@st.composite
def _companies(draw, kit):
    out = []
    for _ in range(draw(st.integers(0, 8))):
        if draw(st.booleans()):
            t = draw(st.sampled_from(kit.POOL))
            out.append(kit.company(draw(st.sampled_from([t, t.lower()]))))
        else:
            out.append(kit.unresolved(draw(st.sampled_from(["Foo", "Bar Inc"])),
                                      draw(st.sampled_from(["ambiguous", "unresolved"]))))
    return out


@pytest.mark.criterion("C2")
@given(st.data())
def test_c2_named_companies_rule_for_any_list(kit, data):
    # oracle: Requirement 3 computed in the test
    companies = data.draw(_companies(kit))
    expected: list[str] = []
    for c in companies:
        if c.cik and c.ticker and not c.is_ambiguous and c.ticker.upper() not in expected:
            expected.append(c.ticker.upper())
    assert named_companies(companies) == expected
