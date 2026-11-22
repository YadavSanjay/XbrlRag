"""C1: the row a defined measure's filing and date come from (requirement 1). Pure."""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import (
    ROLE_COMPONENT,
    ROLE_PRIMARY,
    candidate_order,
    measure_column,
    row_role,
)


def _local(row: dict) -> str:
    return row["concept"].rsplit(":", 1)[-1]


@pytest.mark.criterion("C1")
def test_points_on_the_sec_filings(kit):
    # oracle: the committed mapping's roles (PG DebtCurrent component, LongTermDebtNoncurrent
    # substitute; QCOM LongTermDebt primary rank 1, presented on the FSDS balance sheet) and
    # requirement 1: candidate_order's first row, else the latest, lowest-rank, larger-id row.
    rows = kit.Store().candidate_rows()
    pg = [r for r in rows if r["ticker"] == "PG"]
    qcom = [r for r in rows if r["ticker"] == "QCOM"]
    assert _local(pg[measure_column(pg)]) == "LongTermDebtNoncurrent"
    parts = [r for r in pg if r["concept_role"] == ROLE_COMPONENT]
    assert [_local(r) for r in parts] == ["DebtCurrent"]
    assert measure_column(parts) == 0
    assert _local(qcom[measure_column(qcom)]) == "LongTermDebt"
    assert measure_column([]) is None


@pytest.mark.criterion("C1")
def test_components_only_take_the_latest_lowest_rank_larger_id():
    # oracle: requirement 1's fallback, hand-ordered: the later period first, then rank 50
    # before 53, then the larger fact id; a missing date or rank sorts last.
    def row(fid, end, rank):
        return {"fact_id": fid, "period_end": end, "concept_pref_rank": rank,
                "concept_role": ROLE_COMPONENT, "value": 1.0, "presented": True}
    rows = [row(1, "2025-07-01", 50), row(2, "2024-07-01", 50), row(3, "2025-07-01", 53),
            row(4, "2025-07-01", 50), row(5, None, 50), row(6, "2025-07-01", None)]
    assert measure_column(rows) == 3          # fact 4: latest end, rank 50, larger id than 1
    assert measure_column(rows[:1] + rows[4:]) == 0   # fact 1 over a missing date or rank


_DATES = [None, "2024-07-01", "2025-07-01", "2026-01-01"]
_rows = st.lists(
    st.fixed_dictionaries({
        "concept_role": st.sampled_from([ROLE_PRIMARY, "substitute", ROLE_COMPONENT, None]),
        "concept_pref_rank": st.one_of(st.none(), st.integers(1, 99)),
        "period_end": st.sampled_from(_DATES),
        "presented": st.booleans(),
        "value": st.one_of(st.none(), st.floats(-1e12, 1e12, allow_nan=False)),
        "decimals": st.sampled_from([None, -6, -3, 0]),
    }),
    min_size=1, max_size=8,
).map(lambda rs: [{**r, "fact_id": 100 + i} for i, r in enumerate(rs)])


def _defined(rows: list[dict]) -> int:
    """Requirement 1, written out."""
    order = candidate_order(rows)
    if order:
        return order[0]
    def key(i: int):
        r = rows[i]
        end = r["period_end"]
        rank = r["concept_pref_rank"]
        return ((1, 0) if end is None else (0, -dt.date.fromisoformat(end).toordinal()),
                (1, 0) if rank is None else (0, rank), -r["fact_id"])
    return min(range(len(rows)), key=key)


@pytest.mark.criterion("C1")
@given(_rows, st.randoms(use_true_random=False))
def test_any_rows_follow_the_definition_in_any_order(rows, rnd):
    # oracle: requirement 1 written out above; candidate_order is the lookup's one rule.
    i = measure_column(rows)
    assert i == _defined(rows)
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    assert shuffled[measure_column(shuffled)]["fact_id"] == rows[i]["fact_id"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(_rows)
def test_in_the_lookups_query_order_it_is_todays_defined_column(rows):
    # oracle: the lookup's ORDER BY period_end DESC NULLS LAST, concept_pref_rank ASC NULLS
    # LAST, fact_id DESC and its `_defined_column` today: candidate_order's first, else row 0.
    ordered = sorted(rows, key=lambda r: -r["fact_id"])
    ordered.sort(key=lambda r: (r["concept_pref_rank"] is None, r["concept_pref_rank"] or 0))
    ordered.sort(key=lambda r: (r["period_end"] is not None, r["period_end"] or ""),
                 reverse=True)
    order = candidate_order(ordered)
    assert measure_column(ordered) == (order[0] if order else 0)
    if not order:
        assert all(row_role(r) == ROLE_COMPONENT for r in ordered)
