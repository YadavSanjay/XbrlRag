"""C3: search_scopes, the pure rule from a router result to searches.

Results are built by hand from the resolver's own dataclasses, so each case states exactly what
the extractor would have handed over.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.html10k.scopes import search_scopes
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult

NO_COMPANY = "no company could be extracted from the query"


def _company(cik: str, *, last_seen_fy: int | None = 2024) -> ResolvedCompany:
    return ResolvedCompany(
        mention=f"company {cik}", cik=cik, ticker=f"T{cik}", last_seen_fy=last_seen_fy,
        resolution="exact_ticker", similarity=1.0,
    )


def _unresolved(mention: str = "Nobody", cik: str | None = None) -> ResolvedCompany:
    return ResolvedCompany(mention=mention, cik=cik, resolution="unresolved")


def _period(fiscal_year: int, mention: str | None = None) -> ResolvedPeriod:
    return ResolvedPeriod(mention=mention or f"fiscal {fiscal_year}", fiscal_year=fiscal_year)


def _vague(mention: str = "soon") -> ResolvedPeriod:
    return ResolvedPeriod(mention=mention, ambiguous=True, reason="unclear")


def _result(companies, periods) -> IntentRouterResult:
    return IntentRouterResult(
        intent=XBRL_INTENT.NUMERIC_LOOKUP, companies=companies, periods=periods
    )


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    "companies, periods, expected",
    [
        pytest.param(
            [_company("1")], [_period(2024)], [{"ciks": ["1"], "fiscal_years": [2024]}], id="one"
        ),
        pytest.param(
            [_company("2"), _company("1")], [_period(2024), _period(2023)],
            [{"ciks": ["1"], "fiscal_years": [2023, 2024]},
             {"ciks": ["2"], "fiscal_years": [2023, 2024]}],
            id="two-filers-two-years",
        ),
        pytest.param(
            [_company("1"), _company("1")], [_period(2024), _period(2024)],
            [{"ciks": ["1"], "fiscal_years": [2024]}], id="repeats",
        ),
        pytest.param(
            [_company("1")], [_period(2023, "FY2021 to FY2023")],
            [{"ciks": ["1"], "fiscal_years": [2021, 2022, 2023]}], id="a-range",
        ),
        pytest.param(
            [_company("1", last_seen_fy=2024), _company("2", last_seen_fy=2023)], [],
            [{"ciks": ["1"], "fiscal_years": [2024]}, {"ciks": ["2"], "fiscal_years": [2023]}],
            id="no-period-each-filer-its-own-latest",
        ),
        pytest.param(
            [_company("1", last_seen_fy=None)], [],
            [{"ciks": ["1"], "fiscal_years": []}], id="no-period-no-last-seen",
        ),
    ],
)
def test_resolved_questions(companies, periods, expected):
    # oracle: requirement 5, by hand; a range keeps every written year, as filing_targets does
    assert search_scopes(_result(companies, periods), 10) == (expected, None)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    "companies, periods, expected, reason",
    [
        pytest.param(
            [_company("1"), _unresolved()], [_period(2024)],
            [{"ciks": ["1"], "fiscal_years": [2024]}],
            "company mention 'Nobody' could not be resolved to a CIK",
            id="one-company-resolved",
        ),
        pytest.param(
            [_company("1"), _unresolved("Maybe", cik="9")], [_period(2024)],
            [{"ciks": ["1"], "fiscal_years": [2024]}],
            "company mention 'Maybe' could not be resolved to a CIK",
            id="an-ambiguous-cik-is-not-a-filter",
        ),
        pytest.param(
            [_company("1")], [_vague()], [{"ciks": ["1"], "fiscal_years": []}],
            "period mention 'soon': unclear", id="vague-period",
        ),
        pytest.param(
            [_company("1")], [_period(2024), _vague("around 2030")],
            [{"ciks": ["1"], "fiscal_years": [2024]}],
            "period mention 'around 2030': unclear", id="a-vague-mention-gives-no-year",
        ),
        pytest.param(
            [], [_period(2024)], [{"ciks": [], "fiscal_years": [2024]}], NO_COMPANY,
            id="no-company",
        ),
        pytest.param([], [], [{"ciks": [], "fiscal_years": []}], NO_COMPANY, id="nothing"),
    ],
)
def test_what_did_resolve_is_searched(companies, periods, expected, reason):
    # oracle: requirements 5-6 — BranchDispatcher's own reason strings, by hand; nothing guessed
    assert search_scopes(_result(companies, periods), 10) == (expected, reason)


@pytest.mark.criterion("C3")
def test_too_many_filers_share_one_search():
    # oracle: requirement 5 — three filers over a limit of two: one scope, sorted union of years
    companies = [_company("3", last_seen_fy=2022), _company("1"), _company("2", last_seen_fy=2023)]
    assert search_scopes(_result(companies, []), 2) == (
        [{"ciks": ["1", "2", "3"], "fiscal_years": [2022, 2023, 2024]}], None,
    )


@pytest.mark.criterion("C3")
@given(
    ciks=st.lists(st.sampled_from(["1", "2", "3", "4"]), min_size=1, max_size=6),
    years=st.lists(st.integers(2018, 2030), min_size=1, max_size=4),
)
def test_each_filer_has_exactly_the_years(ciks, years):
    # oracle: requirement 5 — computed here: sorted distinct filers, sorted distinct years
    result = _result([_company(c) for c in ciks], [_period(y) for y in years])
    scopes, reason = search_scopes(result, 10)
    assert reason is None
    assert [s["ciks"] for s in scopes] == [[c] for c in sorted(set(ciks))]
    assert all(s["fiscal_years"] == sorted(set(years)) for s in scopes)
