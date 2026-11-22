"""C4: ``PlanValidator`` accepts a member-list universe (docs/specs/named-company-screen.md,
R1 and R2).

Pure. A screen step's ``universe`` may be a list read by Requirement 1; any other list is one
``universe`` error naming the step's index, id and value. Expected values are computed in the
test from Requirement 1.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_validator import PlanValidator


def _plan(universe):
    return {"steps": [{"id": "s1", "op": "screen", "fy": 2024, "dimension": "NONE",
                       "metric_kind": "value", "order": "desc",
                       "concept_family": "ResearchAndDevelopment", "top_n": 1,
                       "universe": universe}]}


def _universe_errors(universe):
    return [e for e in PlanValidator().validate(_plan(universe)) if "universe" in e]


def _reads(items):
    # Requirement 1, by hand.
    if not all(isinstance(i, str) and i.strip() for i in items):
        return False
    return len({i.strip().upper() for i in items}) >= 2


@pytest.mark.criterion("C4")
def test_c4_valid_member_lists():
    # oracle: Requirement 2 — a list Requirement 1 reads is no error; the plan is valid
    for universe in (["AAPL", "MSFT"], ["aapl", "msft", "googl"], [" GOOGL", "AAPL", "aapl"]):
        assert PlanValidator().validate(_plan(universe)) == [], universe


@pytest.mark.criterion("C4")
def test_c4_invalid_lists_one_error_each():
    # oracle: Requirement 2 — any other list is exactly one error with `universe`, the step id
    # s1, its index 0 and the value; and a valid list beside them is no error
    assert _universe_errors(["AAPL", "MSFT"]) == []
    for bad in ([], ["AAPL"], ["AAPL", "aapl"], ["AAPL", 7], ["AAPL", ""]):
        errors = _universe_errors(bad)
        assert len(errors) == 1, (bad, errors)
        assert "s1" in errors[0] and "0" in errors[0] and repr(bad) in errors[0], errors[0]


_ITEM = st.one_of(st.sampled_from(["AAPL", "msft", " GOOGL ", "Amzn", "", "  "]),
                  st.integers(0, 9))


@pytest.mark.criterion("C4")
@given(st.lists(_ITEM, max_size=5))
def test_c4_universe_error_exactly_when_unread(items):
    # oracle: Requirements 1 and 2 computed in the test
    errors = _universe_errors(items)
    if _reads(items):
        assert errors == []
    else:
        assert len(errors) == 1
        assert repr(items) in errors[0]
    # Always: a readable two-ticker list is accepted (keeps the rule two-sided).
    assert _universe_errors(["MSFT", "AAPL"]) == []
