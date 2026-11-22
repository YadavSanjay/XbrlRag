"""Spec tests for screen-aggregates C6: `PlanValidator` checks the `aggregate` op
(docs/specs/screen-aggregates.md, Requirement 5). Pure.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_validator import PlanValidator

FNS = ("sum", "median", "mean", "count")

SCREEN = {"id": "s1", "op": "screen", "fy": 2024, "dimension": "NONE", "metric_kind": "value",
          "order": "desc", "concept_family": "Capex", "top_n": None, "universe": "retailers"}
LOOKUP = {"id": "l1", "op": "lookup", "cik": "0000104169", "fy": 2024, "concept_family": "Capex",
          "dimensions": "NONE", "section_hint": None}


def _agg(of="s1", fn="sum", sid="a1"):
    step = {"id": sid, "op": "aggregate", "fn": fn}
    if of is not None:
        step["of"] = of
    return step


def _errors(steps):
    return PlanValidator().validate({"steps": steps})


@pytest.mark.criterion("C6")
def test_valid_aggregate_plans():
    # oracle: Requirement 5 — an aggregate of an earlier screen, each fn, and a later compute
    # over aggregate ids, are valid
    for fn in FNS:
        assert _errors([SCREEN, _agg(fn=fn)]) == [], fn
    plan = [SCREEN, _agg(fn="sum"), _agg(fn="median", sid="a2"),
            {"id": "c1", "op": "compute", "expr": "a1 / a2"}]
    assert _errors(plan) == []
    assert _errors([LOOKUP, SCREEN, _agg()]) == []


@pytest.mark.criterion("C6")
def test_bad_of_is_one_error():
    # oracle: Requirement 5 — `of` missing, forward, unknown or not a screen: exactly one
    # error naming 'of', the index, the id and the value
    cases = [
        ([SCREEN, _agg(of=None)], 1, None),
        ([_agg(of="s1"), SCREEN], 0, "s1"),
        ([SCREEN, _agg(of="zz9")], 1, "zz9"),
        ([LOOKUP, _agg(of="l1")], 1, "l1"),
    ]
    for steps, index, value in cases:
        errors = _errors(steps)
        assert len(errors) == 1, (steps, errors)
        e = errors[0]
        assert "'of'" in e and f"step[{index}]" in e and "a1" in e, e
        if value is not None:
            assert value in e


@pytest.mark.criterion("C6")
@given(st.text(alphabet=string.ascii_letters + string.digits + "_", min_size=0, max_size=12))
def test_any_unknown_fn_is_one_error(fn):
    # oracle: Requirement 5 — any fn outside the four is one error naming 'fn', the index,
    # the id and the value
    if fn in FNS:
        fn += "_x"
    errors = _errors([SCREEN, _agg(fn=fn)])
    assert len(errors) == 1
    assert "'fn'" in errors[0] and "step[1]" in errors[0] and "a1" in errors[0]
    assert repr(fn) in errors[0] or fn in errors[0]
