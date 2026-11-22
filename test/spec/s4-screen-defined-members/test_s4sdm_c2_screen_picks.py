"""C2: the flat screen's picks per filer (requirement 2). Pure."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.defined_measure import DEFINED_FAMILIES
from src.retriever.xbrl.family_resolution import measure_column
from src.retriever.xbrl.xbrl_factstore_screen import (
    select_preferred_concept,
    select_screen_picks,
)

# A family the S4 definitions do not cover (s4-lookup requirement 1 names it).
OTHER = "LongTermDebt"


def _local(row: dict) -> str:
    return row["concept"].rsplit(":", 1)[-1]


@pytest.mark.criterion("C2")
def test_a_debt_screen_keeps_every_filer(kit):
    # oracle: requirement 2 over the committed mapping's roles: KO's and PG's first answerable
    # rows are stated substitutes, QCOM's is the presented primary LongTermDebt.
    rows = kit.Store().candidate_rows()
    kept, apart = select_screen_picks(rows, "Debt")
    assert apart == []
    assert [(r["ticker"], _local(r)) for r in kept] == [
        ("KO", "LongTermDebtAndCapitalLeaseObligations"),
        ("PG", "LongTermDebtNoncurrent"),
        ("QCOM", "LongTermDebt"),
    ]
    assert all(any(k is r for r in rows) for k in kept)  # the input dicts, unchanged


@pytest.mark.criterion("C2")
def test_another_family_is_select_preferred_concept(kit):
    # oracle: screen-candidate-order R1 (unchanged): a substitute-first filer is listed apart.
    rows = kit.Store(family=OTHER).candidate_rows()
    kept, apart = select_screen_picks(rows, OTHER)
    assert (kept, apart) == select_preferred_concept(rows)
    assert [r["ticker"] for r in kept] == ["QCOM"]
    assert [c["ticker"] for c in apart] == ["KO", "PG"]


_ROLES = ["primary", "substitute", "component", None]
_candidates = st.lists(
    st.fixed_dictionaries({
        "ticker": st.sampled_from(["AAA", "BBB", "CCC"]),
        "fiscal_year": st.sampled_from([2024, 2025]),
        "concept_role": st.sampled_from(_ROLES),
        "concept_pref_rank": st.one_of(st.none(), st.integers(1, 99)),
        "period_end": st.sampled_from([None, "2024-07-01", "2025-07-01"]),
        "presented": st.booleans(),
        "value": st.one_of(st.none(), st.floats(-1e9, 1e9, allow_nan=False)),
        "decimals": st.sampled_from([None, -6]),
        "concept": st.sampled_from(["us-gaap:A", "us-gaap:B", "us-gaap:C"]),
    }),
    min_size=1, max_size=10,
).map(lambda rs: [{**r, "fact_id": 10 + i} for i, r in enumerate(rs)])


@pytest.mark.criterion("C2")
@given(_candidates, st.sampled_from(sorted(DEFINED_FAMILIES) + [OTHER, "Revenue"]),
       st.randoms(use_true_random=False))
def test_any_rows_any_order(rows, family, rnd):
    # oracle: requirement 2 written out: select_preferred_concept outside the definitions;
    # inside, each ticker's latest-year rows give their measure_column row.
    kept, apart = select_screen_picks(rows, family)
    if family not in DEFINED_FAMILIES:
        assert (kept, apart) == select_preferred_concept(rows)
    else:
        assert apart == []
        want = []
        for t in sorted({r["ticker"] for r in rows}):
            mine = [r for r in rows if r["ticker"] == t]
            year = max(r["fiscal_year"] for r in mine)
            own = [r for r in mine if r["fiscal_year"] == year]
            want.append(own[measure_column(own)]["fact_id"])
        assert [r["fact_id"] for r in kept] == want
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    k2, a2 = select_screen_picks(shuffled, family)
    assert [r["fact_id"] for r in k2] == [r["fact_id"] for r in kept]
    assert a2 == apart
