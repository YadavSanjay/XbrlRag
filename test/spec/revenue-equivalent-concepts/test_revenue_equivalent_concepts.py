"""Spec tests for revenue-equivalent-concepts: total revenue under either standard concept
earns the rank credit (docs/specs/revenue-equivalent-concepts.md).

Pure: `rank_credit`, and `_build_result` driven with rows as the queries return them.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup, rank_credit

G25 = "http://fasb.org/us-gaap/2025:"
G23 = "http://fasb.org/us-gaap/2023:"
EQUIVALENT = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]
PREFIXES = ["us-gaap:", G25, "http://fasb.org/us-gaap/2022:", ""]


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_rank_credit_point_cases():
    # oracle: Requirement 1 by hand
    assert rank_credit("Revenue", G25 + "Revenues", 2) is True
    assert rank_credit("Revenue", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                       3) is True
    assert rank_credit("Revenue", G25 + "SalesRevenueNet", 2) is False
    assert rank_credit("DeferredRevenue", G25 + "ContractWithCustomerLiabilityCurrent", 2) is False
    assert rank_credit("NetIncome", G25 + "ProfitLoss", 2) is False
    assert rank_credit("Revenue", G25 + "Revenues", None) is False
    assert rank_credit("Revenue", G25 + "Revenues", 55) is False
    assert rank_credit("DeferredRevenue", G25 + "Anything", 1) is True


@pytest.mark.criterion("C1")
@given(st.sampled_from(["Revenue", "NetIncome", "DeferredRevenue", "Debt", "X"]),
       st.text("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20),
       st.sampled_from(PREFIXES), st.one_of(st.none(), st.integers(min_value=1, max_value=200)))
def test_rank_credit_matches_rule(family, local, prefix, rank):
    # oracle: Requirement 1 written out
    expected = rank == 1 or (
        family == "Revenue" and local in EQUIVALENT and rank is not None
        and not 50 <= rank < 100)
    assert rank_credit(family, prefix + local, rank) is expected


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.sampled_from(EQUIVALENT), st.sampled_from(PREFIXES), st.sampled_from(PREFIXES),
       st.integers(min_value=1, max_value=49))
def test_namespace_prefix_does_not_matter(local, p1, p2, rank):
    # oracle: metamorphic — only the local name is compared
    assert rank_credit("Revenue", p1 + local, rank) is rank_credit("Revenue", p2 + local, rank)
    assert rank_credit("Revenue", p1 + local, rank) is True


# --------------------------------------------------------------------------- C2
def _row(fact_id, value, concept, rank, family, period_end, period_type="duration"):
    return {"fact_id": fact_id, "value": value, "unit": "USD", "period_type": period_type,
            "period_start": None, "period_end": period_end, "concept": concept,
            "concept_family": family, "concept_pref_rank": rank, "segment_axis": None,
            "segment_member": None, "segment_label": None, "html_anchor": None,
            "page_num": None, "section_path": None, "accession_no": None,
            "linked_chunk_ids": [], "decimals": -6}


def _result(family, period_type, rows):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions("NONE", notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "dimensions": "NONE", "section_hint": None}
    return lk._build_result("s1", step, "x", 2025, family, period_type, specs, rows, notes)


def _googl(concept=G25 + "Revenues"):
    rows = [_row(f, 402_836e6, concept, 2, "Revenue", "2026-01-01")
            for f in (34375, 34694, 34730, 35024, 35948)]
    rows += [_row(f, 350_018e6, concept, 2, "Revenue", "2025-01-01")
             for f in (34374, 34693, 34728, 35022, 35947)]
    return rows


@pytest.mark.criterion("C2")
def test_alphabet_fy2025_revenue_resolves():
    # oracle: corpus facts (2026-10-16); confidence 0.4 + 0.2 + 0.2 by hand
    r = _result("Revenue", "duration", _googl())
    assert r.status.value == "OK"
    assert r.value == pytest.approx(402_836e6, rel=1e-12)  # stored value, unrounded
    assert r.confidence == pytest.approx(0.8, abs=1e-9)  # sum of fixed credits


@pytest.mark.criterion("C2")
def test_deferred_revenue_current_portion_stays_ambiguous():
    # oracle: corpus facts (2026-10-16); confidence 0.4 + 0.2 = 0.6 < 0.70, by hand
    rows = [_row(30957, 4_137e6, G23 + "ContractWithCustomerLiabilityCurrent", 2,
                 "DeferredRevenue", "2024-01-01", "instant"),
            _row(30963, 911e6, G23 + "ContractWithCustomerLiabilityNoncurrent", 3,
                 "DeferredRevenue", "2024-01-01", "instant")]
    r = _result("DeferredRevenue", "instant", rows)
    assert r.status.value == "AMBIGUOUS"
    assert r.confidence == pytest.approx(0.6, abs=1e-9)  # no rank credit
    ok = _result("Revenue", "duration", _googl())
    assert ok.status.value == "OK"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.sampled_from(EQUIVALENT), st.sampled_from(PREFIXES))
def test_either_equivalent_concept_gives_the_same_result(local, prefix):
    # oracle: metamorphic — the two total-revenue concepts are interchangeable
    a = _result("Revenue", "duration", _googl(G25 + "Revenues"))
    b = _result("Revenue", "duration", _googl(prefix + local))
    assert a.status.value == b.status.value == "OK"
    assert a.confidence == pytest.approx(b.confidence, abs=1e-9)  # same credits
    assert a.value == pytest.approx(b.value, rel=1e-12)  # same stored value
