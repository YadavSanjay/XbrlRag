"""C6: among duplicates of one concept and period, the finer `decimals` wins.

Oracle: R2 rule 6 of docs/specs/concept-family-coverage.md; XBRL 2.1 §4.6.5 (`decimals`:
a larger value is more precise; INF is exact) and §4.6.7. The real pairs are a statement
table value and its rounded narrative restatement in the same filing; SEC companyfacts
carries only the finer value (fixture `companyfacts`: `match` for the finer fact,
`mismatch:<finer>` for the coarser).
"""

from __future__ import annotations

import pytest
from cfc_support import lookup, row_of, store_rows
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import candidate_order

# (case, family, finer fact id, coarser fact id, value of the finer)
DUPLICATES = [
    ("intc_fy2022_sbc", "ShareBasedCompensation", 151009, 152546, 3_128_000_000),
    ("mrk_fy2025_da", "DepreciationAndAmortization", 232104, 232259, 3_045_000_000),
    ("wmt_fy2026_da", "DepreciationAndAmortization", 261492, 260715, 14_203_000_000),
    ("mcd_fy2025_da", "DepreciationAndAmortization", 217763, 218235, 2_199_000_000),
    ("mcd_fy2025_pretax", "PretaxIncome", 218499, 217606, 10_897_000_000),
]


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("case_id,family,finer,coarser,value", DUPLICATES,
                         ids=[d[0] for d in DUPLICATES])
def test_finer_duplicate_answers(case_id, family, finer, coarser, value):
    # oracle: the fixture's `decimals` (finer > coarser) and the statement value; SEC
    # companyfacts holds the finer value where cached
    assert row_of(case_id, finer)["decimals"] > row_of(case_id, coarser)["decimals"]
    rows = store_rows(case_id, family)
    assert rows[candidate_order(rows)[0]]["fact_id"] == finer
    r = lookup(case_id, family)
    assert r.status.value == "OK", r.resolution_notes
    assert r.citation.fact_id == finer
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded


@pytest.mark.criterion("C6")
def test_intel_sbc_is_not_the_rounded_figure():
    # oracle: INTC FY2022 cash-flow statement "Share-based compensation" 3,128 ($M); the
    # narrative's "$3.1 billion" (decimals -8) is the same fact rounded
    r = lookup("intc_fy2022_sbc", "ShareBasedCompensation")
    assert r.value == pytest.approx(3_128_000_000, rel=1e-12)  # stored value
    assert r.value != pytest.approx(3_100_000_000, rel=1e-12)  # the coarser duplicate


def _dup(fact_id, decimals, value=1_000_000):
    return {"concept_role": "primary", "concept_pref_rank": 1, "period_end": "2026-01-01",
            "presented": True, "value": value, "decimals": decimals, "fact_id": fact_id}


precisions = st.one_of(st.none(), st.integers(min_value=-9, max_value=4))


@pytest.mark.criterion("C6")
@given(st.lists(precisions, min_size=2, max_size=6), st.permutations(range(6)))
def test_finest_duplicate_is_first_whatever_the_fact_ids(decs, perm):
    # oracle: R2 rule 6 — None (exact) first, then the largest decimals; fact ids only
    # break ties among equally precise duplicates
    rows = [_dup(1000 + perm[i], d) for i, d in enumerate(decs)]
    first = rows[candidate_order(rows)[0]]
    best = None if None in decs else max(decs)
    assert first["decimals"] == best
    tied = [r["fact_id"] for r in rows if r["decimals"] == best]
    assert first["fact_id"] == max(tied)


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(st.integers(min_value=-9, max_value=4), st.integers(min_value=1, max_value=8),
       st.integers(min_value=1, max_value=10**6))
def test_a_coarser_or_nil_duplicate_never_wins(dec, coarser_by, fact_id):
    # oracle: R2 rules 5-6 — adding a coarser duplicate (any fact id) or a nil one never
    # changes the pick
    base = [_dup(500_000, dec)]
    assert candidate_order(base) == [0]
    coarse = [*base, _dup(500_000 + fact_id, dec - coarser_by, 1_100_000)]
    assert coarse[candidate_order(coarse)[0]]["fact_id"] == 500_000
    nil = [*base, {**_dup(500_000 + fact_id, None), "value": None}]
    assert nil[candidate_order(nil)[0]]["fact_id"] == 500_000
    exact = [*base, _dup(400_000, None)]
    assert exact[candidate_order(exact)[0]]["fact_id"] == 400_000
