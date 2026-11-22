"""Wiring of docs/specs/regrade-raw-values.md: the stored raw values, the re-grade's values
provenance, and compare_runs grading ``answer_for_regrade``.

Synthetic cases and answers graded by the real graders. No DB, LLM or network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.regrade import regrade_report
from src.eval.core.system import SystemAnswer

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "compare_runs.py"
_spec = importlib.util.spec_from_file_location("compare_runs_rrv", _SCRIPT)
compare_runs = importlib.util.module_from_spec(_spec)
sys.modules["compare_runs_rrv"] = compare_runs  # @dataclass resolves its module while executing
_spec.loader.exec_module(compare_runs)

# The text states 100; the stored (filtered) values have lost it.
TEXT = "Revenue was 100 USD."


def _case(case_id: str = "a", value: float = 100.0) -> EvalCase:
    return EvalCase(id=case_id, question=f"What was revenue for {case_id}?", kind="lookup",
                    expected_value=value, rel_tol=1e-4, tags=["split:dev"])


def _produced(*, raw: list[float] | None = None, text: str = TEXT) -> dict[str, Any]:
    produced: dict[str, Any] = {"text": text, "numeric_values": [999.0], "fact_ids": [1],
                                "refused": False, "hedged": False, "error": None}
    if raw is not None:
        produced["raw_numeric_values"] = raw
    return produced


def _source(system: str, produced: list[dict[str, Any]]) -> dict[str, Any]:
    cases = [_case(f"c{i}") for i in range(len(produced))]
    results = [
        {"case_id": case.id, "question": case.question, "produced": p, "grade": "INCORRECT"}
        for case, p in zip(cases, produced, strict=True)
    ]
    return {"run_id": "eval-aaaaaaaaaaaa", "system": system, "split": "dev",
            "config": {"split": "dev"}, "results": results}


def _regrade(system: str, produced: list[dict[str, Any]]):
    source = _source(system, produced)
    cases = [_case(r["case_id"]) for r in source["results"]]
    return regrade_report(source, cases, suite="s", suite_version="1",
                          corpus_fingerprint=None, regraded_at="2026-11-12T00:00:00+00:00")


def test_grade_case_stores_the_values_as_received():
    answer = SystemAnswer(text=TEXT, numeric_values=[100.0, 5.0], fact_ids=[1])
    grade = grade_case(_case(), answer)
    assert grade.produced["raw_numeric_values"] == [100.0, 5.0]
    assert grade.produced["raw_numeric_values"] is not answer.numeric_values


def test_an_error_grade_stores_the_raw_values_too():
    answer = SystemAnswer(text="", numeric_values=[3.0], error="boom")
    grade = grade_case(_case(), answer)
    assert grade.grade.value == "ERROR"
    assert grade.produced["raw_numeric_values"] == [3.0]


def test_regrade_report_rebuilds_from_text_and_records_it():
    report = _regrade("xbrlrag_full", [_produced()])
    assert report.results[0].grade.grade.value == "CORRECT"
    assert report.config["regrade"]["values"] == "rebuilt"


def test_regrade_report_records_raw_stored_and_mixed():
    assert _regrade("xbrlrag_full", [_produced(raw=[100.0])]).config["regrade"]["values"] == "raw"
    stored = _regrade("html_rag", [_produced()])
    assert stored.config["regrade"]["values"] == "stored"
    assert stored.results[0].grade.grade.value == "INCORRECT"  # the stored 999 is graded
    mixed = _regrade("xbrlrag_full", [_produced(raw=[100.0]), _produced()])
    assert mixed.config["regrade"]["values"] == "mixed"


def test_compare_runs_grades_answer_for_regrade():
    rows = [{"case_id": "a", "produced": _produced()}]
    cases = {"a": _case()}
    rebuilt, _ = compare_runs.regrade(rows, cases, "xbrlrag_full")
    assert rebuilt["a"].grade.grade.value == "CORRECT"
    stored, _ = compare_runs.regrade(rows, cases)
    assert stored["a"].grade.grade.value == "INCORRECT"
    raw_rows = [{"case_id": "a", "produced": _produced(raw=[100.0], text="")}]
    raw, _ = compare_runs.regrade(raw_rows, cases, None)
    assert raw["a"].grade.grade.value == "CORRECT"


def test_compare_runs_reads_the_system_of_a_json_report(tmp_path):
    report = tmp_path / "run.json"
    report.write_text(json.dumps({"system": "naive_rag_plain", "results": []}),
                      encoding="utf-8")
    assert compare_runs.load_system(str(report)) == "naive_rag_plain"
