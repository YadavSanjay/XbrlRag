"""C2: filing_targets, the pure rule from a router result to filings.

Results are built by hand from the resolver's own dataclasses, so each case below states
exactly what the extractor would have handed over.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context_arms import filing_targets
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult


def _company(ticker: str, *, last_seen_fy: int | None = 2024) -> ResolvedCompany:
    return ResolvedCompany(
        mention=ticker, cik=f"000000{ticker}", ticker=ticker, last_seen_fy=last_seen_fy,
        resolution="exact_ticker", similarity=1.0,
    )


def _period(fiscal_year: int, mention: str | None = None) -> ResolvedPeriod:
    return ResolvedPeriod(mention=mention or f"fiscal {fiscal_year}", fiscal_year=fiscal_year)


def _result(companies: list[ResolvedCompany], periods: list[ResolvedPeriod]) -> IntentRouterResult:
    return IntentRouterResult(
        intent=XBRL_INTENT.NUMERIC_LOOKUP, companies=companies, periods=periods
    )


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    "companies, periods, expected",
    [
        (["AAA"], [2024], [("AAA", 2024)]),
        (["AAA"], [2024, 2022], [("AAA", 2022), ("AAA", 2024)]),
        (["BBB", "AAA"], [2024], [("AAA", 2024), ("BBB", 2024)]),
        (["BBB", "AAA"], [2024, 2023],
         [("AAA", 2023), ("AAA", 2024), ("BBB", 2023), ("BBB", 2024)]),
        (["AAA", "AAA"], [2024, 2024], [("AAA", 2024)]),
    ],
)
def test_product_of_companies_and_years(companies, periods, expected):
    # oracle: requirement 3, by hand — sorted, distinct product
    result = _result([_company(t) for t in companies], [_period(y) for y in periods])
    assert filing_targets(result) == (expected, None)


@pytest.mark.criterion("C2")
def test_a_range_mention_gives_every_year_in_it():
    # oracle: requirement 3 — the extractor keeps only 2023 from this mention; 2021, 2022 and
    # 2023 are the years it writes
    result = _result([_company("AAA")], [_period(2023, "FY2021 to FY2023")])
    assert filing_targets(result) == ([("AAA", 2021), ("AAA", 2022), ("AAA", 2023)], None)


@pytest.mark.criterion("C2")
def test_no_period_takes_each_companys_last_seen_year():
    # oracle: requirement 3 — one target per company, each at its own year, not a product
    result = _result(
        [_company("AAA", last_seen_fy=2024), _company("CCC", last_seen_fy=2023)], []
    )
    assert filing_targets(result) == ([("AAA", 2024), ("CCC", 2023)], None)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    "result",
    [
        pytest.param(_result([], [_period(2024)]), id="no-company"),
        pytest.param(
            _result([ResolvedCompany(mention="Acme", resolution="ambiguous")], [_period(2024)]),
            id="ambiguous-company",
        ),
        pytest.param(
            _result(
                [_company("AAA"), ResolvedCompany(mention="Acme", resolution="unresolved")],
                [_period(2024)],
            ),
            id="one-of-two-companies-ambiguous",
        ),
        pytest.param(
            _result(
                [_company("AAA")],
                [ResolvedPeriod(mention="soon", ambiguous=True, reason="could_not_resolve_year")],
            ),
            id="ambiguous-period",
        ),
        pytest.param(
            _result(
                [_company("AAA")],
                [_period(2024),
                 ResolvedPeriod(mention="calendar 2023", fiscal_year=2023, ambiguous=True,
                                reason="calendar vs fiscal")],
            ),
            id="one-of-two-periods-ambiguous",
        ),
        pytest.param(_result([_company("AAA", last_seen_fy=None)], []), id="no-last-seen-year"),
        pytest.param(
            _result(
                [ResolvedCompany(mention="Acme", cik="0000000009", ticker=None,
                                 resolution="fuzzy_name", similarity=0.9)],
                [_period(2024)],
            ),
            id="company-without-a-ticker",
        ),
    ],
)
def test_cannot_choose_gives_no_targets_and_a_reason(result):
    # oracle: requirements 2-3 — BranchDispatcher._unresolved_reasons' three conditions, plus
    # the two that leave no filing to name
    targets, reason = filing_targets(result)
    assert targets == []
    assert isinstance(reason, str) and reason


@pytest.mark.criterion("C2")
@given(
    tickers=st.lists(st.sampled_from(["AAA", "BBB", "CCC"]), min_size=1, max_size=4),
    years=st.lists(st.integers(2018, 2035), min_size=1, max_size=4),
)
def test_equals_the_sorted_product_whatever_the_order(tickers, years):
    # oracle: the sorted product, computed here from requirement 3
    expected = sorted({(t, y) for t in tickers for y in years})
    forward = _result([_company(t) for t in tickers], [_period(y) for y in years])
    backward = _result(
        [_company(t) for t in reversed(tickers)], [_period(y) for y in reversed(years)]
    )
    assert filing_targets(forward) == (expected, None)
    assert filing_targets(backward) == (expected, None)
