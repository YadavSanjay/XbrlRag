"""C5: within one role, the line presented on a primary statement beats a notes-only fact.

Oracle: R2 of docs/specs/concept-family-coverage.md (D13's rule), and the filers'
presentation linkbases (fixture `presented`): e.g. Alphabet's FY2025 income statement shows
"Other income (expense), net" 29,787 ($M), tagged NonoperatingIncomeExpense, while
OtherNonoperatingIncomeExpense (1,667) appears only in a note.
"""

from __future__ import annotations

import datetime as dt

import pytest
from cfc_support import lookup, store_rows, substitution_of
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import candidate_order

# (case, family, expected fact id, value, why)
PRESENTED = [
    ("googl_fy2025_nonop", "OtherNonOperatingIncome", 35429, 29_787_000_000,
     "total presented, 'other' 1,667 only in a note"),
    ("intc_fy2025_nonop", "OtherNonOperatingIncome", 157691, 3_257_000_000,
     "'Interest and other, net' presented, 'other' -416 in a note"),
    ("meta_fy2025_nonop", "OtherNonOperatingIncome", 42295, 2_656_000_000,
     "'Interest and other income, net' presented"),
    ("nvda_fy2026_nonop", "OtherNonOperatingIncome", 104, 9_022_000_000,
     "both presented: rank 1 'other' keeps the answer"),
    ("amzn_fy2025_nonop", "OtherNonOperatingIncome", 8680, 15_229_000_000,
     "both presented: 'Other income (expense), net' keeps the answer"),
    ("ko_fy2025_interest", "InterestExpense", 195390, 1_654_000_000,
     "InterestExpenseNonoperating presented; InterestExpense (same value) only in a note"),
    ("ko_fy2025_ap", "AccountsPayable", 196344, 5_649_000_000,
     "a presented component (accounts payable and accrued, 14,813) never answers"),
    ("nvda_fy2025_sti", "ShortTermInvestments", 64428, 34_621_000_000,
     "MarketableSecuritiesCurrent presented; the extension only in a note"),
    ("tsla_fy2025_da", "DepreciationAndAmortization", 113156, 6_148_000_000,
     "presented substitute beats the notes-only Depreciation substitute"),
    ("ceg_fy2025_da", "DepreciationAndAmortization", 128772, 2_601_000_000,
     "presented substitute beats the notes-only Depreciation substitute"),
]


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("case_id,family,fact_id,value,why", PRESENTED,
                         ids=[p[0] for p in PRESENTED])
def test_presented_line_wins(case_id, family, fact_id, value, why):
    # oracle: the filer's statement (`why`); R2 rules 1-4 by hand on the fixture rows
    rows = store_rows(case_id, family)
    assert rows[candidate_order(rows)[0]]["fact_id"] == fact_id
    r = lookup(case_id, family)
    assert r.status.value == "OK", r.resolution_notes
    assert r.citation.fact_id == fact_id
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded


@pytest.mark.criterion("C5")
def test_googl_no_longer_answers_the_note():
    # oracle: GOOGL FY2025 income statement 29,787 vs the note's 1,667 ($M)
    r = lookup("googl_fy2025_nonop", "OtherNonOperatingIncome")
    assert r.value != pytest.approx(1_667_000_000, rel=1e-12)  # the notes-only fact
    assert substitution_of(r) is None


def _role(r):
    if r.get("concept_role"):
        return r["concept_role"]
    rank = r.get("concept_pref_rank")
    return "component" if rank is not None and 50 <= rank < 100 else "primary"


def _oracle(rows):
    """R2 written out: indices of answerable rows, best first."""
    def key(i):
        r = rows[i]
        pe = r.get("period_end")
        rank = r.get("concept_pref_rank")
        dec = r.get("decimals")
        return (0 if _role(r) == "primary" else 1,
                1 if pe is None else 0, -dt.date.fromisoformat(pe).toordinal() if pe else 0,
                0 if r.get("presented") else 1,
                1 if rank is None else 0, rank or 0,
                1 if r.get("value") is None else 0,
                0 if dec is None else 1, -(dec or 0),
                -r["fact_id"])
    return sorted((i for i, r in enumerate(rows) if _role(r) != "component"), key=key)


row = st.fixed_dictionaries({
    "concept_role": st.sampled_from([None, "primary", "substitute", "component"]),
    "concept_pref_rank": st.sampled_from([None, 1, 2, 3, 50, 51, 53, 100, 101]),
    "period_end": st.sampled_from([None, "2024-01-01", "2025-01-01", "2026-01-01"]),
    "presented": st.booleans(),
    "value": st.sampled_from([None, 0, 5, 1_000_000, -7]),
    "decimals": st.sampled_from([None, -8, -6, -5, 0, 2]),
})
rows_st = st.lists(row, min_size=1, max_size=8).flatmap(
    lambda rs: st.permutations(range(1, len(rs) + 1)).map(
        lambda ids: [{**r, "fact_id": 100 + i} for r, i in zip(rs, ids, strict=True)]))


@pytest.mark.criterion("C5")
@given(rows_st)
def test_candidate_order_matches_the_rule(rows):
    # oracle: R2 written out (`_oracle`), on generated rows; keys may be missing (role)
    rows = [{k: v for k, v in r.items() if v is not None or k != "concept_role"} for r in rows]
    assert candidate_order(rows) == _oracle(rows)
    assert candidate_order([dict(r) for r in rows]) == candidate_order(rows)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(rows_st, st.data())
def test_presented_precedes_notes_only_at_one_role_and_period(rows, data):
    # oracle: R2 rule 3 — at one role and period, presentation outranks rank and fact id
    order = candidate_order(rows)
    assert sorted(order) == [i for i, r in enumerate(rows) if _role(r) != "component"]
    pos = {i: p for p, i in enumerate(order)}
    for i in order:
        for j in order:
            a, b = rows[i], rows[j]
            if (_role(a) == _role(b) and a["period_end"] == b["period_end"]
                    and a["presented"] and not b["presented"]):
                assert pos[i] < pos[j]
    if order:
        i = data.draw(st.sampled_from(order))
        flipped = [dict(r) for r in rows]
        flipped[i]["presented"] = True
        new = candidate_order(flipped)
        assert new.index(i) <= order.index(i)  # presenting a row never moves it down
