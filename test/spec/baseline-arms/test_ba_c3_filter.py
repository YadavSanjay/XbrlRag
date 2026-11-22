"""C3: the filtered variant restricts to the case's ticker and fiscal year; plain never does."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.naive_rag import NaiveRagSystem


def _run(filtered, case, make_gateway, make_embedder, make_qdrant, point, reply):
    gateway, _ = make_gateway(reply(1.0))
    qdrant = make_qdrant([point("chunk-1", "Revenue was 5 million dollars.")])
    system = NaiveRagSystem(
        filtered=filtered, gateway=gateway, embedder=make_embedder(), vector_db_client=qdrant
    )
    system.answer(case)
    assert len(qdrant.calls) == 1
    return qdrant.calls[0]


@pytest.mark.criterion("C3")
def test_filtered_scalar_case(make_gateway, make_embedder, make_qdrant, point, make_case, reply):
    # oracle: requirement 4 — a case with ticker AAA and fy 2024, filtered variant
    call = _run(True, make_case(ticker="AAA", fy=2024), make_gateway, make_embedder, make_qdrant,
                point, reply)
    assert call["tickers_in_query"] == ["AAA"]
    assert call["fiscal_years_in_query"] == [2024]
    assert call["ciks_in_query"] is None


@pytest.mark.criterion("C3")
def test_filtered_cross_filer_screen_uses_year_only(make_gateway, make_embedder, make_qdrant, point,
                                                    make_case, reply):
    # oracle: requirement 4 — a screen with no ticker is filtered by fiscal year only
    case = make_case("List the top 3 companies by revenue in fiscal 2023.", ticker=None, fy=2023,
                     kind="screen")
    call = _run(True, case, make_gateway, make_embedder, make_qdrant, point, reply)
    assert call["tickers_in_query"] is None
    assert call["fiscal_years_in_query"] == [2023]
    assert call["ciks_in_query"] is None


@pytest.mark.criterion("C3")
def test_plain_ignores_ticker_and_year(make_gateway, make_embedder, make_qdrant, point, make_case,
                                       reply):
    # oracle: requirement 4 — the plain variant passes no filters whatever the case carries
    call = _run(False, make_case(ticker="BBB", fy=2022), make_gateway, make_embedder, make_qdrant,
                point, reply)
    assert call["tickers_in_query"] is None
    assert call["fiscal_years_in_query"] is None
    assert call["ciks_in_query"] is None
    assert call["limit"] == 10


_TICKER = st.one_of(st.none(), st.sampled_from(["AAA", "BBB", "MSFT", "TSM", "BRK-B"]))
_FY = st.one_of(st.none(), st.integers(min_value=2015, max_value=2030))


@pytest.mark.criterion("C3")
@given(ticker=_TICKER, fy=_FY, filtered=st.booleans())
def test_filters_follow_the_case(make_gateway, make_embedder, make_qdrant, point, make_case, reply,
                                 ticker, fy, filtered):
    # oracle: closed form of requirement 4 — [x] when filtered and set, else None
    call = _run(filtered, make_case(ticker=ticker, fy=fy), make_gateway, make_embedder, make_qdrant,
                point, reply)
    expected_tickers = [ticker] if (filtered and ticker is not None) else None
    expected_years = [fy] if (filtered and fy is not None) else None
    assert call["tickers_in_query"] == expected_tickers
    assert call["fiscal_years_in_query"] == expected_years
    assert call["ciks_in_query"] is None
    assert call["collection_name"] == "xbrl_sec10k_text"
    assert call["limit"] == 10
