"""Spec tests for plan-cagr-and-ratios C7: `is_ratio_expr` and `operation_plan_errors`.

docs/specs/plan-cagr-and-ratios.md, Requirement 7. Pure.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.operation_cues import is_ratio_expr, operation_plan_errors

CIK = "0000000001"


def _lookup(sid, family, fy=2024):
    return {"id": sid, "op": "lookup", "cik": CIK, "fy": fy, "concept_family": family,
            "dimensions": "NONE", "section_hint": "Balance Sheet"}


CA, CL = _lookup("ca", "CurrentAssets"), _lookup("cl", "CurrentLiabilities")
R22, R25 = _lookup("r22", "Revenue", 2022), _lookup("r25", "Revenue", 2025)


@pytest.mark.criterion("C7")
def test_is_ratio_expr_points():
    # oracle: Requirement 7 applied by hand to each expression's parse tree
    for expr in ("a / b", "(a) / (b)", "a / b * 100", "100 * a / b", "(a - b) / b",
                 "(ca - inv) / cl"):
        assert is_ratio_expr(expr) is True, expr
    for expr in ("a - b", "a * b", "a / 2", "2 / 4", "a / b - 1", "", "a /", None, 3,
                 "a"):
        assert is_ratio_expr(expr) is False, expr


@pytest.mark.criterion("C7")
def test_operation_plan_errors_points():
    # oracle: Requirement 7 — one error until the plan does the cued operation; [] when
    # exempt or nothing is cued
    lookups = {"steps": [CA, CL]}
    errors = operation_plan_errors(lookups, "ratio")
    assert len(errors) == 1 and "ratio" in errors[0] and "compute" in errors[0]
    with_ratio = {"steps": [CA, CL, {"id": "c1", "op": "compute", "expr": "ca / cl"}]}
    assert operation_plan_errors(with_ratio, "ratio") == []
    difference = {"steps": [CA, CL, {"id": "c1", "op": "compute", "expr": "ca - cl"}]}
    assert len(operation_plan_errors(difference, "ratio")) == 1

    stops_at_ratio = {"steps": [R22, R25, {"id": "c1", "op": "compute", "expr": "r25 / r22"}]}
    errors = operation_plan_errors(stops_at_ratio, "cagr")
    assert len(errors) == 1 and "cagr" in errors[0].lower()
    with_cagr = {"steps": [R22, R25, {"id": "g1", "op": "cagr", "start": "r22", "end": "r25"}]}
    assert operation_plan_errors(with_cagr, "cagr") == []

    screen = {"steps": [{"id": "s1", "op": "screen", "fy": 2024, "dimension": "NONE",
                         "metric_kind": "derived:CurrentRatio", "order": "desc"}]}
    assert operation_plan_errors(screen, "ratio") == []
    assert operation_plan_errors(lookups, None) == []
    assert operation_plan_errors(lookups, "difference") == []
    assert operation_plan_errors({"error": "x"}, "ratio") == []
    assert operation_plan_errors("not a plan", "cagr") == []


_step = st.one_of(
    st.builds(lambda i: {"id": f"l{i}", "op": "lookup"}, st.integers(0, 5)),
    st.builds(lambda e: {"id": "c", "op": "compute", "expr": e},
              st.sampled_from(["l0 / l1", "l0 - l1", "l0 * 2", "l1 / 4", ""])),
    st.just({"id": "g", "op": "cagr", "start": "l0", "end": "l1"}),
    st.just({"id": "s", "op": "screen"}),
)


@pytest.mark.criterion("C7")
@given(st.lists(_step, max_size=6), st.sampled_from(["cagr", "ratio"]))
def test_at_most_one_error_and_none_without_a_cue(steps, op):
    # oracle: Requirement 7 — one error at most; [] for no operation; the plan is not changed
    plan = {"steps": steps}
    before = copy.deepcopy(plan)
    errors = operation_plan_errors(plan, op)
    assert len(errors) <= 1
    assert operation_plan_errors(plan, None) == []
    assert plan == before
    has_screen = any(s["op"] == "screen" for s in steps)
    if op == "cagr":
        done = any(s["op"] == "cagr" for s in steps)
    else:
        done = any(s["op"] == "compute" and s["expr"] == "l0 / l1" for s in steps)
    assert (errors == []) == (has_screen or done)
