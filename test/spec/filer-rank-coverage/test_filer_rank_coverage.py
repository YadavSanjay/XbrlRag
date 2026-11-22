"""Spec tests for filer-rank-coverage: a filer's rank 1 is the eligible concept it uses
in the most fiscal years (docs/specs/filer-rank-coverage.md). Pure: no DB.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.layer0_populator import consistent_concepts, filer_ranking

RFC = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
REV = "us-gaap:Revenues"
# NVIDIA's revenue concepts by fiscal year (2026-10-15 corpus).
NVDA_YEARS = {RFC: {2022}, REV: {2022, 2023, 2024, 2025, 2026}}
REVENUE_GLOBAL = [(RFC, 1), (REV, 2), ("us-gaap:SalesRevenueNet", 3)]


def _oracle(years_reported, eligible):
    """C1 written out."""
    counts = {q: len(set(years_reported.get(q, ()))) for q in eligible}
    best = max(counts.values(), default=0)
    return {q for q, n in counts.items() if n == best} if best > 0 else set()


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_point_cases():
    # oracle: the definition, by hand
    assert consistent_concepts(NVDA_YEARS, [RFC, REV]) == {REV}
    assert consistent_concepts({"a:A": {2024, 2025}, "a:B": {2024, 2025}},
                               ["a:A", "a:B"]) == {"a:A", "a:B"}
    # A component with more years is not eligible, so the primary wins.
    da = {"us-gaap:Depreciation": {2021, 2022, 2023, 2024, 2025},
          "us-gaap:DepreciationDepletionAndAmortization": {2024, 2025}}
    assert consistent_concepts(da, ["us-gaap:DepreciationDepletionAndAmortization"]) == {
        "us-gaap:DepreciationDepletionAndAmortization"}
    assert consistent_concepts({}, [RFC]) == set()
    assert consistent_concepts({RFC: set()}, [RFC]) == set()


qn = st.sampled_from([f"a:Q{i}" for i in range(6)])
years = st.sets(st.integers(min_value=2020, max_value=2026), max_size=7)
usage = st.dictionaries(qn, years, max_size=6)


@pytest.mark.criterion("C1")
@given(usage, st.sets(qn, min_size=1), qn, st.sets(st.integers(2020, 2026), min_size=1))
def test_matches_the_rule(years_reported, eligible, anchor, anchor_years):
    # oracle: C1 written out (`_oracle`); the anchored copy guarantees a non-empty answer
    assert consistent_concepts(years_reported, eligible) == _oracle(years_reported, eligible)
    anchored = {**years_reported, anchor: anchor_years}
    elig = set(eligible) | {anchor}
    assert consistent_concepts(anchored, elig) == _oracle(anchored, elig) != set()


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_composed_with_filer_ranking():
    # oracle: filer_ranking's rule by hand — Revenues is reported, RFC (rank 1) is not
    reported = consistent_concepts(NVDA_YEARS, [q for q, _ in REVENUE_GLOBAL])
    assert filer_ranking(REVENUE_GLOBAL, reported) == [
        (REV, 1), (RFC, 2), ("us-gaap:SalesRevenueNet", 3)]
    # Every eligible QName tied: the global rank 1 is among them, so no override.
    tied = {q: {2025} for q, _ in REVENUE_GLOBAL}
    assert filer_ranking(REVENUE_GLOBAL, consistent_concepts(tied, list(tied))) is None


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.integers(min_value=1, max_value=5), st.integers(min_value=0, max_value=4))
def test_more_years_wins_regardless_of_rank(n_rev, n_rfc):
    # oracle: the definition — the concept with strictly more years becomes rank 1
    used = {REV: set(range(2026 - n_rev, 2026)), RFC: set(range(2026 - min(n_rfc, n_rev - 1),
                                                                2026)) if n_rev > 1 else set()}
    reported = consistent_concepts(used, [RFC, REV])
    assert reported == {REV}
    assert filer_ranking(REVENUE_GLOBAL, reported)[0] == (REV, 1)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(usage, st.sets(qn, min_size=1), st.randoms(use_true_random=False), qn)
def test_properties(years_reported, eligible, rnd, extra_year_for):
    # oracle: the C3 properties, checked directly
    anchored = {**years_reported, sorted(eligible)[0]: {2020}}
    out = consistent_concepts(anchored, eligible)
    assert out and out <= set(eligible)
    counts = {len(set(anchored[q])) for q in out}
    assert len(counts) == 1
    top = counts.pop()
    assert all(len(set(anchored.get(q, ()))) <= top for q in eligible)
    items = list(anchored.items())
    rnd.shuffle(items)
    elig = list(eligible)
    rnd.shuffle(elig)
    assert consistent_concepts(dict(items), elig) == out
    # A non-member at the maximum count, given one more year, becomes the only member.
    if extra_year_for in eligible and extra_year_for not in out:
        grown = {**anchored, extra_year_for: set(range(2000, 2000 + top + 1))}
        assert consistent_concepts(grown, eligible) == {extra_year_for}


@pytest.mark.criterion("C3")
def test_properties_point_case():
    # oracle: the C3 properties on NVIDIA — order does not matter; one more year for
    # RFC than Revenues makes RFC the only member
    assert consistent_concepts(dict(reversed(list(NVDA_YEARS.items()))), [REV, RFC]) == {REV}
    grown = {**NVDA_YEARS, RFC: set(range(2020, 2026))}
    assert consistent_concepts(grown, [RFC, REV]) == {RFC}
