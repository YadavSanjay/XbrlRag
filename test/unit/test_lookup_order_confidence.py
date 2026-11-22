"""The lookup's preferred-concept credit follows candidate order (presented before
notes-only), so a presented primary that wins is not marked down for its rank, while a
real ambiguity still lowers confidence. Pure: no DB, no LLM.

Shapes from the store: AMD FY2025 pre-tax income, where the income statement presents
fact 114594 (rank 2, 4,140,000,000) and a note carries fact 115758 (rank 1,
4,166,000,000).
"""

from __future__ import annotations

import pytest

from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup, order_credit

G = "http://fasb.org/us-gaap/2025:"
RANK1 = (G + "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
         "ExtraordinaryItemsNoncontrollingInterest")
RANK2 = (G + "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
         "MinorityInterestAndIncomeLossFromEquityMethodInvestments")
EXT = "http://www.amd.com/20251227:PretaxIncomeOther"
AMD_CIK = "0000002488"


def _row(fact_id, value, concept, rank, presented, period_end="2025-12-27", role="primary"):
    return {"fact_id": fact_id, "value": value, "unit": "USD", "period_type": "duration",
            "period_start": None, "period_end": period_end, "concept": concept,
            "concept_family": "PretaxIncome", "concept_pref_rank": rank,
            "concept_role": role, "substitution_code": None, "substitution_note": None,
            "presented": presented, "segment_axis": None, "segment_member": None,
            "segment_label": None, "html_anchor": None, "page_num": None,
            "section_path": None, "accession_no": None, "linked_chunk_ids": [],
            "decimals": -6}


def _result(rows):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, _ = lk._parse_dimensions("NONE", notes)
    step = {"id": "s1", "op": "lookup", "dimensions": "NONE", "section_hint": None}
    return lk._build_result("s1", step, AMD_CIK, 2025, "PretaxIncome", "duration", specs,
                            rows, notes)


PRESENTED = _row(114594, 4_140_000_000, RANK2, 2, presented=True)
NOTES_ONLY = _row(115758, 4_166_000_000, RANK1, 1, presented=False)


def test_presented_rank2_beating_a_notes_only_rank1_is_ok():
    r = _result([NOTES_ONLY, PRESENTED])
    assert r.citation.fact_id == 114594
    assert r.value == pytest.approx(4_140_000_000, rel=1e-12)
    assert r.confidence == pytest.approx(0.8)  # 0.4 one value + 0.2 order + 0.2 no dim
    assert r.status.value == "OK", r.resolution_notes


def test_prior_year_comparatives_do_not_matter():
    prior = [_row(9001, 3_000_000_000, RANK2, 2, True, "2024-12-28"),
             _row(9002, 3_100_000_000, RANK1, 1, False, "2024-12-28")]
    r = _result([NOTES_ONLY, PRESENTED, *prior])
    assert (r.citation.fact_id, r.status.value) == (114594, "OK")


def test_two_presented_primaries_that_disagree_stay_ambiguous():
    rival = _row(115800, 4_100_000_000, EXT, 3, presented=True)
    r = _result([NOTES_ONLY, PRESENTED, rival])
    assert r.citation.fact_id == 114594
    assert r.confidence == pytest.approx(0.6)
    assert r.status.value == "AMBIGUOUS"


def test_a_presented_primary_that_agrees_does_not_block():
    agreeing = _row(115800, 4_140_000_000, EXT, 3, presented=True)
    r = _result([NOTES_ONLY, PRESENTED, agreeing])
    assert (r.citation.fact_id, r.status.value) == (114594, "OK")


def test_winner_by_tie_break_among_presented_rows_stays_ambiguous():
    # Two presented primaries at rank 2: the larger fact id wins, a tie-break only.
    tied = _row(114000, 4_100_000_000, EXT, 2, presented=True)
    r = _result([NOTES_ONLY, PRESENTED, tied])
    assert r.citation.fact_id == 114594
    assert r.confidence == pytest.approx(0.2)  # no one-value, no rank/order credit
    assert r.status.value == "AMBIGUOUS"


def test_notes_only_winner_earns_nothing_from_order():
    # Nothing presented: rank 1 wins as before; a rank-2 winner cannot arise this way.
    r = _result([{**NOTES_ONLY}, {**PRESENTED, "presented": False}])
    assert (r.citation.fact_id, r.status.value) == (115758, "OK")


def test_presented_fallback_alone_keeps_the_fallback_score():
    # No better-ranked fact was set aside: presentation decided nothing.
    r = _result([PRESENTED])
    assert r.confidence == pytest.approx(0.6)
    assert r.status.value == "AMBIGUOUS"


def test_presented_rank1_winner_is_unchanged():
    r = _result([_row(1, 5.0, RANK1, 1, True), _row(2, 6.0, RANK2, 2, False)])
    assert (r.citation.fact_id, r.confidence, r.status.value) == (1, pytest.approx(0.8), "OK")


def test_order_credit_rules():
    win = PRESENTED
    assert order_credit(win, [win, NOTES_ONLY])
    assert not order_credit(win, [win])  # nothing outranked
    assert not order_credit({**win, "presented": False}, [win, NOTES_ONLY])
    assert not order_credit({**win, "concept_role": "substitute"}, [win, NOTES_ONLY])
    worse_notes = _row(3, 1.0, EXT, 3, presented=False)
    assert not order_credit(win, [win, worse_notes])  # a worse rank is not outranked
    sub = {**NOTES_ONLY, "concept_role": "substitute"}
    assert not order_credit(win, [win, sub])  # a substitute is not a better primary
