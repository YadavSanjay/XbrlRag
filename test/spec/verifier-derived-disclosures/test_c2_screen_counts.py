"""C2: ``screen_count_values``, the counts a statement citing a screen may state (Requirement 2)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.screen_evidence import screen_count_values


@pytest.mark.criterion("C2")
def test_stored_screens_counts(vd):
    # oracle: Requirement 2 by hand. ap_screen: 4 ranked, 4 members, 4 rows, 0 exclusions.
    # ppe_screen: 23 ranked, 33 members, 3 rows, 10 exclusions.
    assert screen_count_values(vd.ap_screen()) == [0, 4]
    assert screen_count_values(vd.ppe_screen()) == [3, 10, 23, 33]


@pytest.mark.criterion("C2")
def test_counts_without_disclosure_and_for_other_steps(vd):
    # oracle: Requirement 2 by hand: ranked 12 (row cap), 5 rows, 1 exclusion, no members
    rows = [vd.screen_row(t, "1", 10 + i, 1e9 * (9 - i), i, "2024-12-31", 2024, "Revenue")
            for i, t in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"], start=1)]
    step = {"op": "screen", "step_id": "s9", "status": "OK", "value": None, "rows": rows,
            "excluded": [{"ticker": "FFF", "reason": "period_not_filed"}],
            "row_cap": {"limit": 5, "rows_before_cap": 12, "returned": 5}}
    assert screen_count_values(step) == [1, 5, 12]
    assert screen_count_values(vd.wmt_lookup()) == []
    # a bool is not a count
    no_members = {**vd.ap_screen()}
    no_members["disclosure"] = {**no_members["disclosure"], "members": True}
    assert screen_count_values(no_members) == [0, 4]


@pytest.mark.criterion("C2")
@given(n_ranked=st.integers(0, 40), extra_members=st.integers(0, 10),
       n_rows=st.integers(0, 40), n_excl=st.integers(0, 10), with_members=st.booleans())
def test_counts_are_the_four_counts(n_ranked, extra_members, n_rows, n_excl, with_members):
    # oracle: Requirement 2: the sorted set of ranked, members, rows and exclusions, counted
    # here from the inputs
    n_rows = min(n_rows, n_ranked)
    ranked = [f"T{i:02d}" for i in range(n_ranked)]
    rows = [{"ticker": t, "rank": i + 1, "value": 1e9, "fact_id": str(500 + i)}
            for i, t in enumerate(ranked[:n_rows])]
    disclosure = {"universe": "covered", "fiscal_year": 2024, "ranked": ranked,
                  "exclusions": [{"ticker": f"X{i}", "reason": "metric_not_tagged"}
                                 for i in range(n_excl)],
                  "period_ends": {}, "period_ends_differ": False}
    members = n_ranked + n_excl + extra_members
    if with_members:
        disclosure["members"] = members
    step = {"op": "screen", "step_id": "s1", "status": "OK", "rows": rows,
            "disclosure": disclosure}
    expected = {n_ranked, n_rows, n_excl} | ({members} if with_members else set())
    assert screen_count_values(step) == sorted(expected)
