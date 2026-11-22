"""Wiring of the S1 selection (docs/specs/restatement-s1.md) beyond the locked spec tests.

Synthetic rows; the expected answers follow from the requirements by hand.
"""

from __future__ import annotations

from src.retriever.xbrl.restatement import current_column, latest_restatement, yoy_pairs
from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup


def _row(accn, fy, end, value, start=None, filed="2025-02-01", fact_id=1, canonical=True,
         dpe=None, concept="http://fasb.org/us-gaap/2024:Revenues"):
    return {
        "accession_no": accn, "fiscal_year": fy, "fiscal_period": "FY", "filed_date": filed,
        "is_amendment": False, "is_canonical_filing": canonical,
        "document_period_end": dpe, "concept": concept, "unit": "USD",
        "segment_axis": None, "segment_member": None, "period_start": start,
        "period_end": end, "value": value, "fact_id": fact_id, "concept_pref_rank": 1,
    }


def test_rows_without_dei_end_pass_through_the_lookup_selection():
    # An older fake of the flat query returns rows without the S1 columns: kept as given.
    legacy = [{"fact_id": 7, "value": 1.0, "period_end": "2024-01-01"}]
    assert XbrlFactstoreLookup._select_s1(legacy, 2023, "FY", False) == legacy


def test_lookup_selection_orders_latest_period_then_rank_then_fact_id():
    # Two own-column rows of one filing at the same end: lower rank first, then the
    # greater fact_id; dates given as ISO strings.
    a = _row("A", 2024, "2025-01-01", 5.0, start="2024-01-01", fact_id=1, dpe="2024-12-31")
    b = dict(a, fact_id=2, concept="http://fasb.org/us-gaap/2024:Other",
             concept_pref_rank=2)
    c = dict(a, fact_id=3)
    got = XbrlFactstoreLookup._select_s1([a, b, c], 2024, "FY", False)
    assert [r["fact_id"] for r in got] == [3, 1, 2]


def test_an_older_filing_never_restates():
    # requirement 2: only a later-labelled filing is a candidate
    own = _row("B", 2024, "2025-01-01", 10.0, start="2024-01-01", dpe="2024-12-31")
    older = _row("A", 2023, "2025-01-01", 99.0, start="2024-01-01", filed="2026-01-01",
                 fact_id=2)
    assert latest_restatement(current_column([own, older], 2024), [own, older]) == [own]


def test_a_zero_prior_has_no_growth():
    # requirement 5: yoy_pct_change is None when the prior value is 0
    cur = _row("B", 2024, "2025-01-01", 10.0, start="2024-01-01", dpe="2024-12-31")
    prior = _row("B", 2024, "2024-01-01", 0.0, start="2023-01-01", fact_id=2,
                 dpe="2024-12-31")
    (pair,) = yoy_pairs([cur, prior], 2024)
    assert pair["same_filing"] is True and pair["yoy_pct_change"] is None
