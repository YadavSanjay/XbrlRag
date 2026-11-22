"""Wiring of the screen `aggregate` op (docs/specs/screen-aggregates.md, Part B).

The locked spec tests cover the aggregate itself; these cover the unlocked wiring: the
`row_cap` and `aggregate` keys of `StepResult.to_dict`, the executor's `numeric_lineage`
event and the verifier's evidence for an aggregate step.
"""

from __future__ import annotations

from unittest.mock import patch

from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.plan_executor import PlanExecutor, StepResult, StepStatus

ROWS = [
    {"ticker": "WMT", "value": 2_683_000_000.0, "unit": "USD", "fact_id": "101",
     "period_end": "2025-01-31", "rank": 1},
    {"ticker": "AMZN", "value": 2_406_000_000.0, "unit": "USD", "fact_id": "102",
     "period_end": "2024-12-31", "rank": 2},
]
SCREEN = {"id": "s1", "op": "screen", "fy": 2024, "dimension": "NONE", "metric_kind": "value",
          "order": "desc", "concept_family": "InterestExpense", "top_n": None}


def _executor(row_cap=None):
    def screen_fn(step):
        return StepResult(step_id=step["id"], op="screen", status=StepStatus.OK,
                          rows=[dict(r) for r in ROWS], row_cap=row_cap)
    return PlanExecutor(factstore_lookup=None, screen_fn=screen_fn)


def test_step_result_emits_row_cap_and_aggregate_only_when_set():
    plain = StepResult(step_id="s1", op="screen", status=StepStatus.OK).to_dict()
    assert "row_cap" not in plain and "aggregate" not in plain
    cap = {"limit": 1, "rows_before_cap": 2, "returned": 1}
    capped = StepResult(step_id="s1", op="screen", status=StepStatus.OK, row_cap=cap)
    assert capped.to_dict()["row_cap"] == cap


def test_a_capped_screen_is_never_aggregated():
    plan = {"steps": [SCREEN, {"id": "a1", "op": "aggregate", "of": "s1", "fn": "sum"}]}
    cap = {"limit": 2, "rows_before_cap": 3, "returned": 2}
    a1 = _executor(row_cap=cap).execute(plan).step("a1")
    assert a1 is not None and a1.status is StepStatus.ERROR and a1.value is None
    assert "row_cap" in str(a1.error)


def test_aggregate_lineage_event_names_fn_of_and_row_fact_ids():
    plan = {"steps": [SCREEN, {"id": "a1", "op": "aggregate", "of": "s1", "fn": "sum"}]}
    with patch("src.retriever.xbrl.plan_executor.log_event") as log:
        _executor().execute(plan)
    events = [c.kwargs for c in log.call_args_list
              if c.args == ("numeric_lineage",) and c.kwargs.get("op") == "aggregate"]
    assert len(events) == 1
    event = events[0]
    assert event["fn"] == "sum" and event["of"] == "s1" and event["step_id"] == "a1"
    assert event["source_fact_ids"] == ["101", "102"] and event["n"] == 2
    assert event["result"] == 2_683_000_000.0 + 2_406_000_000.0


def test_verifier_evidence_shows_what_an_aggregate_covers():
    plan = {"steps": [SCREEN, {"id": "a1", "op": "aggregate", "of": "s1", "fn": "sum"}]}
    execution = _executor().execute(plan).to_dict()
    by_step, by_fact = XbrlFaithfulnessVerifier._index_fact_steps(execution)
    evidence = XbrlFaithfulnessVerifier._resolve_fact_evidence(["a1"], by_step, by_fact)
    assert len(evidence) == 1
    agg = evidence[0]["aggregate"]
    assert agg["fn"] == "sum" and agg["n"] == 2
    assert agg["members"] == ["WMT", "AMZN"] and agg["fact_ids"] == ["101", "102"]
    assert evidence[0]["value"] == 2_683_000_000.0 + 2_406_000_000.0
