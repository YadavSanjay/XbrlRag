"""Spec tests for family-measure-equivalence: OperatingExpenses and Debt hold only their own
measure among their primaries (docs/specs/family-measure-equivalence.md).

Pure: the generated rankings with no taxonomy tree, occurrence counts or overrides, and the
filer rankings built from them; no DB.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.layer0_populator import filer_ranking
from src.ingest.util.generate_concept_mapping import (
    FAMILY_ANCHOR_QNAMES,
    FAMILY_COMPONENT_QNAMES,
    concept_mapping_rows,
    reviewed_filer_ranking,
    validate_family_overrides,
)

COMPONENT_BASE = 50  # concept-rank-integrity: the component band starts here

G = "us-gaap:"


def _ranking(family, **kwargs):
    """The family's generic ranking from the declared lists alone."""
    overrides = validate_family_overrides({}, FAMILY_ANCHOR_QNAMES, FAMILY_COMPONENT_QNAMES)
    rows, _ = concept_mapping_rows({}, {}, overrides, **kwargs)
    return [(r["qname"], r["preference_rank"]) for r in rows
            if r["concept_family"] == family and not r["cik_override"]]


# us-gaap 2025: "Operating Expenses" includes SG&A; "Operating Costs and Expenses" excludes
# it; "Costs and Expenses" is total costs of sales and operating expenses.
OPEX = [(G + "OperatingExpenses", 1), (G + "OperatingCostsAndExpenses", 50),
        (G + "CostsAndExpenses", 51)]
# us-gaap 2025: "Long-Term Debt" includes current maturities; "Long-Term Debt, Excluding
# Current Maturities" and "Long-Term Debt and Lease Obligation" (classified as noncurrent)
# are noncurrent balances. The existing components keep ranks 50-52.
DEBT = [(G + "LongTermDebt", 1), (G + "DebtCurrent", 50), (G + "ShortTermBorrowings", 51),
        (G + "NotesPayable", 52), (G + "LongTermDebtNoncurrent", 53),
        (G + "LongTermDebtAndCapitalLeaseObligations", 54)]
PRIMARIES = {"OperatingExpenses": {G + "OperatingExpenses"}, "Debt": {G + "LongTermDebt"}}
EXPECTED = {"OperatingExpenses": OPEX, "Debt": DEBT}

# Settings that only change taxonomy descendants and the cap on them; with no tree and no
# counts there are none, so the declared lists alone decide the ranking.
depth_and_cap = st.tuples(st.integers(min_value=0, max_value=3),
                          st.one_of(st.none(), st.integers(min_value=5, max_value=49)))


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_operating_expenses_ranks_only_operating_expenses_as_primary():
    # oracle: us-gaap 2025 standard labels and definitions (spec table) and the v2 lexicon
    # label "Operating Expenses"; total costs and expenses is a larger measure
    assert _ranking("OperatingExpenses") == OPEX


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(depth_and_cap)
def test_operating_expenses_ranking_holds_for_any_depth_and_cap(settings):
    # oracle: the same hand-written ranking; depth and cap touch only descendants (none here)
    max_depth, top_k = settings
    assert _ranking("OperatingExpenses", max_depth=max_depth, top_k=top_k) == OPEX


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_debt_ranks_long_term_debt_including_current_maturities_only():
    # oracle: us-gaap 2025 definitions — LongTermDebtNoncurrent and
    # LongTermDebtAndCapitalLeaseObligations are "classified as noncurrent"; the lexicon's
    # "Long-Term Debt" is the standard label of us-gaap:LongTermDebt
    assert _ranking("Debt") == DEBT


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(depth_and_cap)
def test_debt_ranking_holds_for_any_depth_and_cap(settings):
    # oracle: the same hand-written ranking; depth and cap touch only descendants (none here)
    max_depth, top_k = settings
    assert _ranking("Debt", max_depth=max_depth, top_k=top_k) == DEBT


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_a_filer_tagging_only_another_measure_gets_no_override():
    # oracle: concept-rank-integrity requirement 2 by hand on the C1/C2 rankings — the 10
    # CostsAndExpenses filers and the 11 noncurrent-debt filers of the spec get no rank 1
    opex, debt = _ranking("OperatingExpenses"), _ranking("Debt")
    assert filer_ranking(opex, {G + "CostsAndExpenses"}) is None
    assert filer_ranking(opex, {G + "OperatingCostsAndExpenses", G + "CostsAndExpenses"}) is None
    assert filer_ranking(debt, {G + "LongTermDebtNoncurrent"}) is None
    assert filer_ranking(debt, {G + "LongTermDebtAndCapitalLeaseObligations",
                                G + "LongTermDebtCurrent"}) is None
    # A filer that also reports the family's own measure keeps the generic ranking.
    assert filer_ranking(debt, {G + "LongTermDebt", G + "LongTermDebtNoncurrent"}) is None
    assert filer_ranking(opex, {G + "OperatingExpenses", G + "CostsAndExpenses"}) is None
    for family, other in (("OperatingExpenses", G + "CostsAndExpenses"),
                          ("OperatingExpenses", G + "OperatingCostsAndExpenses"),
                          ("Debt", G + "LongTermDebtNoncurrent"),
                          ("Debt", G + "LongTermDebtAndCapitalLeaseObligations")):
        with pytest.raises(ValueError):
            reviewed_filer_ranking(_ranking(family), [other])


ALL_QNAMES = sorted({q for ranking in EXPECTED.values() for q, _ in ranking}
                    | {G + "LongTermDebtCurrent",
                       G + "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"})


@pytest.mark.criterion("C3")
@given(st.sampled_from(sorted(EXPECTED)), st.sets(st.sampled_from(ALL_QNAMES)))
def test_no_reported_set_puts_another_measure_first(family, reported):
    # oracle: the band rule by hand — rank 1 is only ever a primary of the C1/C2 ranking,
    # and components keep their generic ranks
    ranking = _ranking(family)
    assert ranking == EXPECTED[family]
    out = filer_ranking(ranking, reported)
    if out is None:
        return
    assert out[0][0] in PRIMARIES[family] and out[0][1] == 1
    expected_components = [(q, r) for q, r in EXPECTED[family] if r >= COMPONENT_BASE]
    assert [(q, r) for q, r in out if r >= COMPONENT_BASE] == expected_components


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.sampled_from(sorted(EXPECTED)), st.sets(st.sampled_from(ALL_QNAMES)))
def test_adding_other_measures_never_changes_a_filer_ranking(family, reported):
    # oracle: metamorphic — reporting a component or an unmapped concept too is invisible to
    # the filer's ranking; the C1/C2 ranking is asserted first so a wrong list cannot pass
    ranking = _ranking(family)
    assert ranking == EXPECTED[family]
    others = {q for q in ALL_QNAMES if q not in PRIMARIES[family]}
    primaries_only = set(reported) & PRIMARIES[family]
    assert filer_ranking(ranking, primaries_only | others) == filer_ranking(
        ranking, primaries_only)
