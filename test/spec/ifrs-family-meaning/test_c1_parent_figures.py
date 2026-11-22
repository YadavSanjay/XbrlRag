"""C1: NetIncome, StockholdersEquity and ComprehensiveIncome answer the parent's figure (R1, R6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ifm_support import (
    CIKS,
    EXT_NAMESPACES,
    IFRS_DATES,
    TSM,
    YEARS,
    answer_tag,
    candidate_rows,
    fsds_value,
    half_unit,
    lookup,
    mapping_rows,
    presented_tag,
    tag_text,
    variant_rows,
)

from src.ingest.sec_edgar.ingest.layer0_populator import filer_ranking
from src.retriever.xbrl.family_resolution import family_member, same_measure

# family -> (parent element, NCI-inclusive total, the parent line's label, its statement)
PARENT = {
    "NetIncome": ("ProfitLossAttributableToOwnersOfParent", "ProfitLoss",
                  "Shareholders of the parent", "IS"),
    "StockholdersEquity": ("EquityAttributableToOwnersOfParent", "Equity",
                           "Equity attributable to shareholders of the parent", "BS"),
    "ComprehensiveIncome": ("ComprehensiveIncomeAttributableToOwnersOfParent",
                            "ComprehensiveIncome", "Shareholders of the parent", "IS"),
}
US_GAAP_MEANING = {  # family -> its us-gaap rank-1 element, whose label says "Parent"
    "NetIncome": "NetIncomeLoss",
    "StockholdersEquity": "StockholdersEquity",
    "ComprehensiveIncome": "ComprehensiveIncomeNetOfTax",
}
IFRS = "ifrs-full:"
COMPONENT_BAND = range(50, 100)

# Hand-copied FY2024 values (FSDS 2025q2), checked against the fixture in the test.
FY2024 = {"NetIncome": 1_158_380_200_000, "StockholdersEquity": 4_244_266_500_000,
          "ComprehensiveIncome": 1_230_949_100_000}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("fy", YEARS)
@pytest.mark.parametrize("family", sorted(PARENT))
def test_parent_figure_answers(family, fy):
    # oracle: FSDS num at the fiscal-period end for the element the filer presents as
    # "Shareholders of the parent" (pre); the us-gaap family's element is "Attributable to
    # Parent" (FSDS tag)
    parent, total, label, stmt = PARENT[family]
    if family == "StockholdersEquity":
        assert presented_tag(fy, stmt, label) == parent
    else:  # two "Shareholders of the parent" lines (profit, comprehensive income) on the IS
        assert parent.endswith("AttributableToOwnersOfParent")
    for el in US_GAAP_MEANING.values():
        assert "Parent" in tag_text(el, "us-gaap/2024").split(" :: ")[0]
    want = fsds_value(fy, parent)
    assert want != fsds_value(fy, total)  # the NCI share makes them differ
    if fy == 2024:
        assert want == pytest.approx(FY2024[family], abs=half_unit(parent))
    rows = candidate_rows(fy, family)
    r = lookup(fy, family, rows)
    assert r.status.value == "OK", r.resolution_notes
    assert answer_tag(r, rows) == parent
    assert r.value == pytest.approx(want, abs=half_unit(parent))  # as filed, to its decimals
    assert r.unit == "TWD"
    assert r.substitution is None


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("family", sorted(PARENT))
def test_parent_element_is_the_measure_and_ranks_first(family):
    # oracle: R1 (the us-gaap family means the parent; the NCI-inclusive total is a
    # near-miss, as revenue-equivalent-concepts decided for us-gaap:ProfitLoss)
    parent, total, _, _ = PARENT[family]
    for cik in CIKS:
        p = family_member(family, cik, IFRS + parent)
        t = family_member(family, cik, IFRS + total)
        assert p is not None and p["role"] == "primary" and p["substitution"] is None
        assert same_measure(family, cik, IFRS + parent) is True
        assert same_measure(family, cik, IFRS + total) is False
        if t is not None and t["role"] == "primary":
            assert p["rank"] < t["rank"]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("family", sorted(PARENT))
def test_layer0_filer_ranking_keeps_the_parent_first(family):
    # oracle: R1 — the facts-count ranking promotes the first eligible element (in generic
    # rank order) the filer reports; TSMC reports both elements in every year (FSDS num)
    parent, total, _, _ = PARENT[family]
    generic = [(r["qname"], r["preference_rank"]) for r in mapping_rows()
               if r["concept_family"] == family and not r["cik_override"]]
    reported = {IFRS + parent, IFRS + total}
    for fy in YEARS:  # both filed in every year
        assert fsds_value(fy, parent) > 0 and fsds_value(fy, total) > 0
    ranking = filer_ranking(generic, reported) or generic
    eligible = sorted((r, q) for q, r in ranking if q in reported and r not in COMPONENT_BAND)
    assert eligible and eligible[0][1] == IFRS + parent


@pytest.mark.criterion("C1")
@given(cik=st.sampled_from(CIKS), ifrs_date=st.sampled_from(IFRS_DATES),
       ext_ns=st.sampled_from(EXT_NAMESPACES), rnd=st.randoms(use_true_random=False))
def test_parent_figure_for_any_ifrs_filer(cik, ifrs_date, ext_ns, rnd):
    # oracle: R6 — the rule is of the standard elements: the FSDS parent value whatever the
    # CIK, taxonomy date, extension namespace, row order and fact ids
    for family, (parent, _, _, _) in sorted(PARENT.items()):
        for fy in YEARS:
            rows = variant_rows(fy, family, cik, ifrs_date, ext_ns, rnd)
            r = lookup(fy, family, rows, cik=cik)
            assert r.status.value == "OK", (family, fy, r.resolution_notes)
            assert answer_tag(r, rows) == parent
            assert r.value == pytest.approx(fsds_value(fy, parent), abs=half_unit(parent))
            assert r.substitution is None


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_tsmc_and_another_filer_agree():
    # oracle: metamorphic (R6) — swapping TSMC's CIK and namespaces for another filer's
    # changes neither status, value nor answering element
    for family, (parent, _, _, _) in sorted(PARENT.items()):
        for fy in YEARS:
            a = lookup(fy, family, candidate_rows(fy, family, TSM), cik=TSM)
            rows_b = candidate_rows(fy, family, CIKS[1], IFRS_DATES[0], EXT_NAMESPACES[1])
            b = lookup(fy, family, rows_b, cik=CIKS[1])
            assert (a.status, a.value, a.unit) == (b.status, b.value, b.unit)
            assert answer_tag(b, rows_b) == parent
