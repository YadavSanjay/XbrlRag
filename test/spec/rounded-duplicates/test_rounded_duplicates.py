"""Spec tests for rounded-duplicates: a fact tagged again at lower precision is one value,
not two (docs/specs/rounded-duplicates.md).

Pure: `most_precise_consistent`, and `_build_result` driven with rows as the queries return
them (no DB).
"""

from __future__ import annotations

import random
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_lookup import (
    XbrlFactstoreLookup,
    most_precise_consistent,
)

G25 = "http://fasb.org/us-gaap/2025:"
G24 = "http://fasb.org/us-gaap/2024:"


def _oracle(facts):
    """Requirement 1 written out."""
    if not facts:
        return None

    def key(d):
        return float("inf") if d is None else d

    best = max(key(d) for _, d in facts)
    top_i = next(i for i, (_, d) in enumerate(facts) if key(d) == best)
    top = Decimal(repr(facts[top_i][0]))
    for v, d in facts:
        dv = Decimal(repr(v))
        if key(d) == best and dv != top:
            return None  # a tie at the top precision with a different value: a conflict
        if d is None:
            if dv != top:
                return None
        elif abs(dv - top) > Decimal(5) * Decimal(10) ** (-d - 1):
            return None
    return top_i


def _round(value, d):
    """value rounded half-even to `decimals` d, as a float."""
    q = Decimal(1).scaleb(-d)
    return float(Decimal(repr(value)).quantize(q, rounding=ROUND_HALF_EVEN))


# Precise values in millions (decimals -6), as in the corpus.
millions = st.integers(min_value=1, max_value=999_999).map(lambda k: float(k * 1_000_000))
facts_st = st.lists(
    st.tuples(millions, st.sampled_from([None, -6, -7, -8, -9])), min_size=0, max_size=6
)


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_point_cases():
    # oracle: Requirement 1 by hand — |value - top| <= 0.5 * 10**-d, inclusive
    assert most_precise_consistent([(20.8e9, -8), (20.832e9, -6)]) == 1
    assert most_precise_consistent([(9.265e9, -6)] * 4 + [(9.3e9, -8)]) == 0
    assert most_precise_consistent([(20.9e9, -8), (20.832e9, -6)]) is None
    assert most_precise_consistent([(20.85e9, -6), (20.8e9, -8)]) == 0
    assert most_precise_consistent([(20.851e9, -6), (20.8e9, -8)]) is None


@pytest.mark.criterion("C1")
def test_point_cases_exact_and_positive_decimals():
    # oracle: Requirement 1 by hand — None is exact; positive decimals round fractions
    assert most_precise_consistent([(5.0, None), (5.0, -6)]) == 0
    assert most_precise_consistent([(5.0, None), (6.0, None)]) is None
    assert most_precise_consistent([(1.23, 2), (1.2, 1)]) == 0
    assert most_precise_consistent([(1.2, 1), (1.23, 2)]) == 1
    assert most_precise_consistent([]) is None
    # A tie at the top precision with different values is a conflict, even within half a
    # unit (re-lock: the dispute's falsifying example).
    assert most_precise_consistent([(1e6, -7), (2e6, -7)]) is None


@pytest.mark.criterion("C1")
@given(facts_st)
@example([(1e6, -7), (2e6, -7)])
def test_matches_rule(facts):
    # oracle: Requirement 1 written out in _oracle (exact Decimal arithmetic)
    assert most_precise_consistent(facts) == _oracle(facts)


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_point_case_permutation():
    # oracle: by hand — the precise NVIDIA fact wins wherever it sits
    assert most_precise_consistent([(20.832e9, -6), (20.8e9, -8)]) == 0
    assert most_precise_consistent([(20.8e9, -8), (20.8e9, -8), (20.832e9, -6)]) == 2


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(facts_st, st.randoms(use_true_random=False))
@example([(1e6, -7), (2e6, -7)], random.Random(1))
def test_permutation_follows_the_fact(facts, rnd):
    # oracle: metamorphic — the winner is a property of the facts, not their order
    order = list(range(len(facts)))
    rnd.shuffle(order)
    permuted = [facts[i] for i in order]
    a, b = most_precise_consistent(facts), most_precise_consistent(permuted)
    assert a == _oracle(facts)
    if a is None:
        assert b is None
    else:
        assert permuted[b] == facts[a]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(millions, st.sampled_from([-7, -8, -9]), st.integers(min_value=0, max_value=3))
def test_adding_a_rounded_copy_keeps_the_winner(top, d, n_copies):
    # oracle: metamorphic — top rounded to its own decimals is consistent by construction
    facts = [(top, -6)] + [(_round(top, d), d)] * n_copies
    assert most_precise_consistent(facts) == 0


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(millions, st.sampled_from([-7, -8, -9]), st.integers(min_value=1, max_value=9))
def test_adding_a_far_value_breaks_consistency(top, d, k):
    # oracle: metamorphic — a value more than half a unit of d away is a different fact
    unit = 10.0 ** (-d)
    far = _round(top, d) + k * unit
    assume(abs(Decimal(repr(far)) - Decimal(repr(top))) > Decimal(repr(unit)) / 2)
    assert most_precise_consistent([(top, -6), (far, d)]) is None


# --------------------------------------------------------------------------- C3
def _row(fact_id, value, decimals, concept, family, period_end, **extra):
    row = {"fact_id": fact_id, "value": value, "unit": "USD", "period_type": "instant",
           "period_start": None, "period_end": period_end, "concept": concept,
           "concept_family": family, "concept_pref_rank": 1, "segment_axis": None,
           "segment_member": None, "segment_label": None, "html_anchor": None,
           "page_num": None, "section_path": None, "accession_no": None,
           "linked_chunk_ids": [], **extra}
    if decimals != "absent":
        row["decimals"] = decimals
    return row


def _result(family, period_type, rows):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions("NONE", notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "dimensions": "NONE", "section_hint": None}
    return lk._build_result("s1", step, "x", 2026, family, period_type, specs, rows, notes)


NVDA = [_row(554, 20_800_000_000.0, -8, G25 + "Goodwill", "Goodwill", "2026-01-26"),
        _row(157, 20_832_000_000.0, -6, G25 + "Goodwill", "Goodwill", "2026-01-26")]


@pytest.mark.criterion("C3")
def test_nvidia_goodwill_resolves_to_the_precise_fact():
    # oracle: corpus facts 554/157 (2026-10-16); confidence 0.4 + 0.2 + 0.2 by hand
    for rows in (NVDA, NVDA[::-1]):
        r = _result("Goodwill", "instant", rows)
        assert r.status.value == "OK"
        assert r.value == pytest.approx(20_832_000_000.0, rel=1e-12)  # stored value, unrounded
        assert r.citation.fact_id == 157
        assert r.confidence == pytest.approx(0.8, abs=1e-9)  # sum of fixed credits


@pytest.mark.criterion("C3")
def test_amazon_tax_resolves_and_inconsistent_rows_stay_ambiguous():
    # oracle: corpus facts (2026-10-16); 9.3e9 is 9.265e9 rounded to decimals -8
    amzn = [_row(i, 9_265_000_000.0, -6, G24 + "IncomeTaxExpenseBenefit", "TaxExpense",
                 "2025-01-01", period_type="duration") for i in (1180, 1327, 194, 1215)]
    amzn.insert(2, _row(1146, 9_300_000_000.0, -8, G24 + "IncomeTaxExpenseBenefit",
                        "TaxExpense", "2025-01-01", period_type="duration"))
    r = _result("TaxExpense", "duration", amzn)
    assert r.status.value == "OK"
    assert r.value == pytest.approx(9_265_000_000.0, rel=1e-12)  # stored value, unrounded
    bad = [_row(554, 20_900_000_000.0, -8, G25 + "Goodwill", "Goodwill", "2026-01-26"),
           _row(157, 20_832_000_000.0, -6, G25 + "Goodwill", "Goodwill", "2026-01-26")]
    assert _result("Goodwill", "instant", bad).status.value == "AMBIGUOUS"
    no_precision = [{**row} for row in NVDA]
    for row in no_precision:
        del row["decimals"]
    assert _result("Goodwill", "instant", no_precision).status.value == "AMBIGUOUS"


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(millions, st.sampled_from([-7, -8, -9]), st.integers(min_value=1, max_value=3),
       st.randoms(use_true_random=False))
def test_rounded_copies_never_make_a_lookup_ambiguous(top, d, n_copies, rnd):
    # oracle: metamorphic — adding rounded copies of the winner changes nothing in the result
    base = [_row(1, top, -6, G25 + "Goodwill", "Goodwill", "2026-01-26")]
    copies = [_row(100 + i, _round(top, d), d, G25 + "Goodwill", "Goodwill", "2026-01-26")
              for i in range(n_copies)]
    rows = base + copies
    rnd.shuffle(rows)
    alone, with_copies = _result("Goodwill", "instant", base), _result("Goodwill", "instant", rows)
    assert with_copies.status.value == alone.status.value == "OK"
    assert with_copies.value == pytest.approx(top, rel=1e-12)  # the precise stored value
    assert with_copies.citation.fact_id == 1
    assert with_copies.confidence == pytest.approx(alone.confidence, abs=1e-9)  # same credits
