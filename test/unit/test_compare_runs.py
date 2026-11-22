"""scripts/eval/compare_runs.py: re-grade two runs' stored answers and diff them.

Pure: hand-built cases and answer payloads, no DB, no LLM.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from src.eval.core.case import EvalCase

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "compare_runs.py"
_spec = importlib.util.spec_from_file_location("compare_runs", _SCRIPT)
compare_runs = importlib.util.module_from_spec(_spec)
sys.modules["compare_runs"] = compare_runs  # @dataclass resolves its module while executing
_spec.loader.exec_module(compare_runs)

CASES = {
    c.id: c
    for c in (
        EvalCase(id="a", question="A?", expected_value=100.0),
        EvalCase(id="b", question="B?", expected_value=200.0),
        EvalCase(id="c", question="C?", expected_value=300.0),
    )
}


def _row(case_id: str, values: list[float], **flags) -> dict:
    return {
        "case_id": case_id,
        "produced": {"numeric_values": values, "fact_ids": [1] if values else [], **flags},
    }


def test_regrade_uses_current_cases_and_skips_unknown_ones():
    rows = [_row("a", [100.0]), _row("gone", [1.0])]
    results, skipped = compare_runs.regrade(rows, CASES)
    assert results["a"].grade.grade.value == "CORRECT"
    assert skipped == ["gone"]


def test_regrade_applies_todays_grader_to_old_answers():
    # A hedged answer showing a nearby number is a HEDGED_MISS under grader v2, whatever
    # the stored grade said.
    results, _ = compare_runs.regrade([_row("a", [250.0], hedged=True)], CASES)
    assert results["a"].grade.grade.value == "HEDGED_MISS"


def test_compare_counts_gains_losses_and_transitions():
    base, _ = compare_runs.regrade(
        [_row("a", [100.0]), _row("b", [], refused=True), _row("c", [300.0])], CASES
    )
    cand, _ = compare_runs.regrade(
        [_row("a", [999.0]), _row("b", [200.0]), _row("c", [300.0])], CASES
    )
    diff = compare_runs.compare(base, cand)
    assert diff.gained == ["b"]
    assert diff.lost == ["a"]
    assert diff.transitions[("REFUSED", "CORRECT")] == 1
    assert diff.transitions[("CORRECT", "INCORRECT")] == 1
    assert ("CORRECT", "CORRECT") not in diff.transitions


def test_load_rows_reads_a_json_report(tmp_path):
    report = tmp_path / "run.json"
    report.write_text(json.dumps({"results": [_row("a", [100.0])]}), encoding="utf-8")
    assert compare_runs.load_rows(str(report))[0]["case_id"] == "a"
