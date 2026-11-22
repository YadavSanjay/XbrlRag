"""Spec tests for screen-aggregates C9: `PlanExecutor` runs an `aggregate` step
(docs/specs/screen-aggregates.md, Requirement 8). The screen reaches the executor through its
own `screen_fn` seam as a fixture `StepResult` (the database is never involved); the
executor, the aggregate and the calculator are the product's own.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_executor import PlanExecutor, StepResult, StepStatus

REL = 1e-12  # short decimals; only float rounding separates them

ROWS = [
    {"ticker": "WMT", "value": 2_683_000_000.0, "unit": "USD", "fact_id": "101",
     "period_end": "2025-01-31", "rank": 1},
    {"ticker": "AMZN", "value": 2_406_000_000.0, "unit": "USD", "fact_id": "102",
     "period_end": "2024-12-31", "rank": 2},
    {"ticker": "HD", "value": 2_066_000_000.0, "unit": "USD", "fact_id": "103",
     "period_end": "2025-02-02", "rank": 3},
]
COST_EXCL = {"ticker": "COST", "reason": "period_not_filed", "latest_fiscal_year": 2023}
DISCLOSURE = {
    "universe": "retailers", "fiscal_year": 2024, "members": 4,
    "ranked": ["WMT", "AMZN", "HD"], "exclusions": [COST_EXCL],
    "period_ends": {"WMT": "2025-01-31", "AMZN": "2024-12-31", "HD": "2025-02-02"},
    "period_ends_differ": False,
}
SCREEN = {"id": "s1", "op": "screen", "fy": 2024, "dimension": "NONE", "metric_kind": "value",
          "order": "desc", "concept_family": "InterestExpense", "top_n": None,
          "universe": "retailers"}


def _executor(rows):
    def screen_fn(step):
        return StepResult(step_id=step["id"], op="screen", status=StepStatus.OK,
                          rows=copy.deepcopy(rows), disclosure=copy.deepcopy(DISCLOSURE))
    return PlanExecutor(factstore_lookup=None, screen_fn=screen_fn)


def _steps(result):
    return {s["step_id"]: s for s in result.to_dict()["steps"]}


@pytest.mark.criterion("C9")
def test_aggregate_steps_and_a_compute_over_them():
    # oracle: Notes — sum 7,155,000,000; median AMZN 2,406,000,000; c1 = a1 / a2 =
    # 7,155,000,000 / 2,406,000,000
    plan = {"steps": [SCREEN,
                      {"id": "a1", "op": "aggregate", "of": "s1", "fn": "sum"},
                      {"id": "a2", "op": "aggregate", "of": "s1", "fn": "median"},
                      {"id": "c1", "op": "compute", "expr": "a1 / a2"}]}
    result = _executor(ROWS).execute(plan)
    steps = _steps(result)
    a1, a2, c1 = steps["a1"], steps["a2"], steps["c1"]
    assert a1["op"] == "aggregate" and a1["status"] == "OK" and a1["unit"] == "USD"
    assert a1["value"] == pytest.approx(7_155_000_000.0, rel=REL)  # float sum
    agg = a1["aggregate"]
    assert agg["fn"] == "sum" and agg["n"] == 3
    assert agg["members"] == ["WMT", "AMZN", "HD"]
    assert agg["fact_ids"] == ["101", "102", "103"]
    assert agg["universe"] == "retailers" and agg["exclusions"] == [COST_EXCL]
    assert a2["value"] == pytest.approx(2_406_000_000.0, rel=REL)  # a row's value
    assert a2["aggregate"]["middle"] == ["AMZN"]
    assert result.bindings["a1"] == pytest.approx(7_155_000_000.0, rel=REL)  # float sum
    assert c1["status"] == "OK"
    assert c1["value"] == pytest.approx(7_155_000_000 / 2_406_000_000, rel=REL)  # one division


@pytest.mark.criterion("C9")
def test_unknown_of_is_an_error_step():
    # oracle: Requirement 8 — an `of` naming no earlier step is ERROR naming the value
    plan = {"steps": [SCREEN, {"id": "a9", "op": "aggregate", "of": "zz9", "fn": "sum"}]}
    a9 = _steps(_executor(ROWS).execute(plan))["a9"]
    assert a9["op"] == "aggregate" and a9["status"] == "ERROR"
    assert "zz9" in str(a9["error"]) and a9["value"] is None


def _hand(fn, values):
    if fn == "sum":
        return float(sum(values))
    if fn == "mean":
        return sum(values) / len(values)
    if fn == "count":
        return len(values)
    s = sorted(values)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


@pytest.mark.criterion("C9")
@given(st.lists(st.integers(-10**12, 10**12), min_size=1, max_size=8),
       st.sampled_from(["sum", "median", "mean", "count"]))
def test_executor_value_is_the_hand_aggregate(values, fn):
    # oracle: closed forms on the integer values (sum, sum / n, sorted middle, n)
    rows = [{"ticker": f"T{i}", "value": float(v), "unit": "USD", "fact_id": str(700 + i),
             "period_end": "2024-12-31", "rank": i + 1} for i, v in enumerate(values)]
    plan = {"steps": [SCREEN, {"id": "a1", "op": "aggregate", "of": "s1", "fn": fn}]}
    a1 = _steps(_executor(rows).execute(plan))["a1"]
    assert a1["status"] == "OK"
    assert a1["value"] == pytest.approx(_hand(fn, values), rel=REL, abs=1e-6)  # one float op
    assert a1["aggregate"]["fact_ids"] == [r["fact_id"] for r in rows]
