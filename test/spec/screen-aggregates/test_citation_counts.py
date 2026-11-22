"""Spec tests for screen-aggregates C11: a statement citing an aggregate step may state its
value, the number of aggregated companies and the group's size
(docs/specs/screen-aggregates.md, Requirement 10). Pure.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.citation_integrity import unmatched_numbers

ROWS = [
    {"ticker": "WMT", "value": 2_683_000_000.0, "unit": "USD", "fact_id": "101", "rank": 1},
    {"ticker": "AMZN", "value": 2_406_000_000.0, "unit": "USD", "fact_id": "102", "rank": 2},
    {"ticker": "HD", "value": 2_066_000_000.0, "unit": "USD", "fact_id": "103", "rank": 3},
]


def _evidence(n, members):
    screen = {"step_id": "s1", "op": "screen", "status": "OK", "value": None, "unit": None,
              "citation": None, "error": None, "rows": ROWS}
    aggregate = {
        "fn": "sum", "status": "OK", "value": 7_155_000_000.0, "unit": "USD", "n": n,
        "members": ["WMT", "AMZN", "HD"], "fact_ids": ["101", "102", "103"], "middle": [],
        "universe": "retailers", "universe_members": members, "exclusions": [],
        "period_ends": {}, "period_ends_differ": False, "error": None,
    }
    step = {"step_id": "a1", "op": "aggregate", "status": "OK", "value": 7_155_000_000.0,
            "unit": "USD", "citation": None, "error": None, "aggregate": aggregate}
    plan = {"steps": [{"id": "s1", "op": "screen"},
                      {"id": "a1", "op": "aggregate", "of": "s1", "fn": "sum"}]}
    return {"status": "OK", "steps": [screen, step]}, plan


def _unmatched(statement, n=3, members=4):
    execution, plan = _evidence(n, members)
    detail = {"statement": statement, "fact_ids": ["a1"], "chunk_ids": []}
    return [x["value"] for x in unmatched_numbers(detail, execution, plan, [])]


@pytest.mark.criterion("C11")
def test_aggregate_counts_are_supported():
    # oracle: Requirement 10 — n = 3 and the group's 4 members are exact candidates; the
    # value 7,155,000,000 is stated exactly; 5 matches nothing
    assert _unmatched("The 3 ranked retailers' combined interest expense was $7.155 billion; "
                      "the group has 4 companies.") == []
    assert _unmatched("The 5 ranked retailers' combined interest expense was "
                      "$7.155 billion.") == [5.0]


@pytest.mark.criterion("C11")
@given(st.integers(1, 9), st.integers(0, 6), st.integers(1, 30))
def test_any_count_of_the_aggregate_is_supported_and_others_are_not(n, extra, k):
    # oracle: Requirement 10 — stated n and group size match; another small integer does not
    members = n + extra
    good = (f"The {n} ranked companies' combined interest expense was $7.155 billion; "
            f"the group has {members} companies.")
    assert _unmatched(good, n, members) == []
    if k in (n, members):
        k = members + 1
    bad = f"The {k} ranked companies' combined interest expense was $7.155 billion."
    assert _unmatched(bad, n, members) == [float(k)]
