"""Spec tests for within-concept-confidence: the "one distinct value" credit is judged
within the winning concept, with a same-rank tie guard and a component guard for Debt
(docs/specs/within-concept-confidence.md).

Pure: `one_value_credit`, and `_build_result` driven with rows as the queries return
them (no DB).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup, one_value_credit

G24 = "http://fasb.org/us-gaap/2024:"
G25 = "http://fasb.org/us-gaap/2025:"


def _local(q):
    return q.split(":")[-1]


def _oracle(winner, winner_rank, current, components_block):
    """C1 written out."""
    mine = {v for q, _, v in current if _local(q) == _local(winner) and v is not None}
    if len(mine) != 1:
        return False
    (v,) = mine
    for q, r, val in current:
        if _local(q) == _local(winner) or val is None or val == v:
            continue
        if r == winner_rank or (components_block and r is not None and 50 <= r < 100):
            return False
    return True


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_point_cases():
    # oracle: the definition, by hand on the 2026-10-15 corpus
    orcl = [(G24 + "NetIncomeLoss", 1, 10_467e6), (G24 + "ProfitLoss", 2, 10_653e6)]
    assert one_value_credit(G24 + "NetIncomeLoss", 1, orcl, False) is True
    tie = [(G24 + "NetIncomeLoss", 1, 10_467e6), (G24 + "ProfitLoss", 1, 10_653e6)]
    assert one_value_credit(G24 + "NetIncomeLoss", 1, tie, False) is False
    dated = [(G24 + "NetIncomeLoss", 1, 5.0), (G25 + "NetIncomeLoss", 1, 5.0)]
    assert one_value_credit(G25 + "NetIncomeLoss", 1, dated, False) is True
    split = [(G24 + "NetIncomeLoss", 1, 5.0), (G25 + "NetIncomeLoss", 1, 6.0)]
    assert one_value_credit(G25 + "NetIncomeLoss", 1, split, False) is False
    amzn_da = [(G24 + "DepreciationDepletionAndAmortization", 1, 52_795e6),
               (G24 + "Depreciation", 50, 32_100e6),
               (G24 + "AmortizationOfIntangibleAssets", 51, 838e6)]
    assert one_value_credit(G24 + "DepreciationDepletionAndAmortization", 1, amzn_da, False)
    tsla_debt = [(G24 + "LongTermDebt", 1, 434e6), (G24 + "DebtCurrent", 50, 54e6)]
    assert one_value_credit(G24 + "LongTermDebt", 1, tsla_debt, True) is False
    assert one_value_credit(G24 + "LongTermDebt", 1, tsla_debt, False) is True
    ifrs = [(G24 + "NetIncomeLoss", 1, 7.0), ("ifrs-full:ProfitLoss", 100, 8.0)]
    assert one_value_credit(G24 + "NetIncomeLoss", 1, ifrs, True) is True
    assert one_value_credit(G24 + "NetIncomeLoss", 1, [(G24 + "NetIncomeLoss", 1, None)],
                            False) is False


locals_ = st.sampled_from(["A", "B", "C", "D"])
ranks = st.sampled_from([1, 2, 3, 50, 51, 100, None])
values = st.sampled_from([1.0, 2.0, 3.0, None])
entries = st.lists(st.tuples(locals_.map(lambda n: "x:" + n), ranks, values), max_size=6)


@pytest.mark.criterion("C1")
@given(entries, st.booleans())
def test_matches_the_rule(current, block):
    # oracle: C1 written out (`_oracle`); a fixed winning entry anchors a True case
    assert one_value_credit("x:W", 1, current, block) == _oracle("x:W", 1, current, block)
    anchored = [("x:W", 1, 9.0), *current]
    assert one_value_credit("x:W", 1, anchored, block) == _oracle("x:W", 1, anchored, block)
    assert one_value_credit("x:W", 1, [("x:W", 1, 9.0), ("x:A", 2, 1.0)], block) is True


# --------------------------------------------------------------------------- C2
def _flat(fact_id, value, concept, rank, family):
    return {"fact_id": fact_id, "value": value, "unit": "USD", "period_type": "duration",
            "period_start": None, "period_end": "2024-05-31", "concept": concept,
            "concept_family": family, "concept_pref_rank": rank, "segment_axis": None,
            "segment_member": None, "segment_label": None, "html_anchor": None,
            "page_num": None, "section_path": None, "accession_no": None,
            "linked_chunk_ids": []}


def _multi(fact_id, value, concept, rank, labels):
    return {**_flat(fact_id, value, concept, rank, None), "period_type": "instant",
            "period_end": "2024-12-31", "dimension_labels": labels}


def _result(family, period_type, dims, rows):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions(dims, notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "dimensions": dims, "section_hint": None}
    return lk._build_result("s1", step, "x", 2024, family, period_type, specs, rows, notes)


TSLA_LABELS = {G24 + "DebtInstrumentAxis": "EnergyAssetBackedNotesMember",
               G24 + "LongtermDebtTypeAxis": "NonrecourseDebtMember"}


@pytest.mark.criterion("C2")
def test_build_result_point_cases():
    # oracle: _score_confidence by hand — 0.4 (one value in the winning concept) + 0.2
    # (rank 1) + 0.2 (no dimensions) = 0.8 >= 0.70; the Debt guard leaves 0.2 + 0.2 = 0.4
    orcl = _result("NetIncome", "duration", "NONE", [
        _flat(71001, 10_467e6, G24 + "NetIncomeLoss", 1, "NetIncome"),
        _flat(71002, 10_653e6, G24 + "ProfitLoss", 2, "NetIncome")])
    assert orcl.status.value == "OK"
    assert orcl.value == pytest.approx(10_467e6, rel=1e-12)  # the stored fact, unrounded
    assert orcl.confidence == pytest.approx(0.8, abs=1e-9)  # sum of fixed credits
    tsla = _result("Debt", "instant", {"a": "energy asset backed notes",
                                       "b": "nonrecourse debt"}, [
        _multi(112162, 434e6, G24 + "LongTermDebt", 1, TSLA_LABELS),
        _multi(112161, 54e6, G24 + "DebtCurrent", 50, TSLA_LABELS)])
    assert tsla.status.value == "AMBIGUOUS"
    assert tsla.confidence == pytest.approx(0.4, abs=1e-9)  # sum of fixed credits


@pytest.mark.criterion("C2")
@given(st.sampled_from([2, 3, 49, 100, 101]), st.floats(min_value=1e6, max_value=1e12))
def test_worse_ranked_rival_never_blocks_outside_debt(rank, rival_value):
    # oracle: the rule — a rival of another concept at a worse primary rank does not block
    r = _result("NetIncome", "duration", "NONE", [
        _flat(1, 10_467e6, G24 + "NetIncomeLoss", 1, "NetIncome"),
        _flat(2, rival_value, G24 + "ProfitLoss", rank, "NetIncome")])
    assert r.status.value == "OK"


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(entries, st.booleans(), st.randoms(use_true_random=False),
       st.sampled_from([2, 3, 49]), values)
def test_properties(current, block, rnd, worse_rank, v):
    # oracle: the C3 properties, checked directly
    base = [("x:W", 1, 9.0)] + [(q, r, val) for q, r, val in current
                                 if r not in (1, 50, 51)]
    assert one_value_credit("x:W", 1, base, block) is True
    shuffled = list(base)
    rnd.shuffle(shuffled)
    assert one_value_credit("x:W", 1, shuffled, block) is True
    assert one_value_credit("x:W", 1, [*base, ("x:Z", worse_rank, v)], block) is True
    assert one_value_credit("x:W", 1, [*base, ("x:Z", 1, 7.0)], block) is False
    for cur in (current, base):
        if one_value_credit("x:W", 1, cur, True):
            assert one_value_credit("x:W", 1, cur, False)


@pytest.mark.criterion("C3")
def test_properties_point_case():
    # oracle: the C3 properties on Oracle and Tesla
    orcl = [(G24 + "NetIncomeLoss", 1, 10_467e6), (G24 + "ProfitLoss", 2, 10_653e6)]
    assert one_value_credit(G24 + "NetIncomeLoss", 1, list(reversed(orcl)), True) is True
    assert one_value_credit(G24 + "NetIncomeLoss", 1,
                            [*orcl, (G24 + "ProfitLoss", 1, 1.0)], False) is False
    tsla = [(G24 + "LongTermDebt", 1, 434e6), (G24 + "DebtCurrent", 50, 54e6)]
    assert (one_value_credit(G24 + "LongTermDebt", 1, tsla, True)
            <= one_value_credit(G24 + "LongTermDebt", 1, tsla, False))
