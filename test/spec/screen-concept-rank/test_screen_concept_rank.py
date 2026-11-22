"""Spec tests for screen-concept-rank: a flat screen ranks each filer on the concept its
family means, and never on a component (docs/specs/screen-concept-rank.md). Pure.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_screen import select_preferred_concept

G = "http://fasb.org/us-gaap/2025:"


def _row(ticker, fy, rank, fact_id, value, concept, period_end):
    return {"ticker": ticker, "cik": "x", "fiscal_year": fy, "period_end": period_end,
            "concept": concept, "concept_pref_rank": rank, "fact_id": fact_id,
            "value": value, "unit": "USD"}


ORCL = [
    _row("ORCL", 2026, 1, 107265, 67_357e6, G + "RevenueFromContractWithCustomerExcludingAssessedTax",
         "2026-06-01"),
    _row("ORCL", 2026, 1, 109053, 67_357e6, G + "RevenueFromContractWithCustomerExcludingAssessedTax",
         "2026-06-01"),
    _row("ORCL", 2026, 2, 109105, 67_357e6, G + "Revenues", "2026-06-01"),
]
GOOGL_DA = [_row("GOOGL", 2024, 50, 32783, 15_311e6, G + "Depreciation", "2025-01-01")]
MSFT_DA = [_row("MSFT", 2025, 50, 49663, 22_000e6, G + "Depreciation", "2025-07-01"),
           _row("MSFT", 2025, 51, 49788, 6_000e6, G + "AmortizationOfIntangibleAssets",
                "2025-07-01")]


def _key(r):
    """Requirement 1's order: larger is preferred."""
    rank = r["concept_pref_rank"]
    return (r["fiscal_year"], str(r["period_end"]),
            -(rank if rank is not None else float("inf")), r["fact_id"])


def _is_component(rank):
    return rank is not None and 50 <= rank < 100


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_oracle_revenue_picks_the_rank_1_fact():
    # oracle: corpus facts (2026-10-16) — rank 1 beats rank 2; then the highest fact_id
    kept, component_only = select_preferred_concept(ORCL)
    assert [r["fact_id"] for r in kept] == [109053]
    assert kept[0] is ORCL[1]
    assert component_only == []


@pytest.mark.criterion("C1")
def test_component_only_filers_are_listed_not_ranked():
    # oracle: corpus facts — Alphabet and Microsoft tag only D&A components (ranks 50, 51)
    kept, component_only = select_preferred_concept(GOOGL_DA + MSFT_DA + ORCL)
    assert [r["ticker"] for r in kept] == ["ORCL"]
    assert component_only == [
        {"ticker": "GOOGL", "fiscal_year": 2024, "concept": G + "Depreciation"},
        {"ticker": "MSFT", "fiscal_year": 2025, "concept": G + "Depreciation"},
    ]


@pytest.mark.criterion("C1")
def test_year_and_period_come_before_rank_and_none_ranks_last():
    # oracle: Requirement 1 by hand
    rows = [_row("AAA", 2024, 1, 1, 1.0, G + "A", "2024-12-31"),
            _row("AAA", 2025, 2, 2, 2.0, G + "B", "2025-12-31"),
            _row("BBB", 2025, None, 9, 3.0, G + "C", "2025-12-31"),
            _row("BBB", 2025, 3, 4, 4.0, G + "D", "2025-12-31"),
            _row("CCC", 2025, 1, 5, 5.0, G + "E", "2025-06-30"),
            _row("CCC", 2025, 2, 6, 6.0, G + "F", "2025-12-31")]
    kept, component_only = select_preferred_concept(rows)
    assert [r["fact_id"] for r in kept] == [2, 4, 6]
    assert component_only == []


# --------------------------------------------------------------------------- C2
rows_st = st.lists(
    st.builds(
        _row,
        st.sampled_from(["AAA", "BBB", "CCC", "DDD"]),
        st.sampled_from([2024, 2025]),
        st.one_of(st.none(), st.integers(min_value=1, max_value=120)),
        st.integers(min_value=1, max_value=10**6),
        st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
        st.sampled_from([G + "A", G + "B", G + "C"]),
        st.sampled_from(["2024-12-31", "2025-06-30", "2025-12-31"]),
    ),
    min_size=0, max_size=12,
).filter(lambda rs: len({r["fact_id"] for r in rs}) == len(rs))


@pytest.mark.criterion("C1")
@given(rows_st)
def test_each_filer_gets_its_best_row_or_is_component_only(rows):
    # oracle: the definition, narrowed by docs/specs/screen-candidate-order.md: the row a
    # filer keeps is now chosen by the lookup's candidate order (that spec's C1 pins it), so
    # this test keeps what the rank-only key and the candidate order agree on: one entry per
    # filer, in ticker order; a kept row is an answerable row of the filer's latest year; a
    # filer whose latest year has only component rows is component-only
    kept, component_only = select_preferred_concept(rows)
    tickers = sorted({r["ticker"] for r in rows})
    assert sorted([r["ticker"] for r in kept] + [c["ticker"] for c in component_only]) == tickers
    assert [r["ticker"] for r in kept] == sorted(r["ticker"] for r in kept)
    assert [c["ticker"] for c in component_only] == sorted(c["ticker"] for c in component_only)
    for t in tickers:
        year = max(r["fiscal_year"] for r in rows if r["ticker"] == t)
        own = [r for r in rows if r["ticker"] == t and r["fiscal_year"] == year]
        for r in kept:
            if r["ticker"] == t:
                assert any(r is o for o in own) and not _is_component(r["concept_pref_rank"])
        if all(_is_component(r["concept_pref_rank"]) for r in own):
            assert t in [c["ticker"] for c in component_only]
            assert [c["fiscal_year"] for c in component_only if c["ticker"] == t] == [year]


@pytest.mark.criterion("C2")
def test_point_case_every_order_of_the_corpus_rows():
    # oracle: by hand — all 6 orders of the Oracle rows, mixed with the D&A rows
    import itertools

    expected = ([ORCL[1]], [
        {"ticker": "GOOGL", "fiscal_year": 2024, "concept": G + "Depreciation"},
        {"ticker": "MSFT", "fiscal_year": 2025, "concept": G + "Depreciation"},
    ])
    for perm in itertools.permutations(ORCL):
        assert select_preferred_concept(MSFT_DA[::-1] + list(perm) + GOOGL_DA) == expected


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(rows_st, st.randoms(use_true_random=False))
def test_row_order_changes_nothing(rows, rnd):
    # oracle: metamorphic — the choice depends on the rows, not their order
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    assert select_preferred_concept(rows) == select_preferred_concept(shuffled)
    kept, _ = select_preferred_concept(ORCL)
    assert [r["fact_id"] for r in kept] == [109053]
