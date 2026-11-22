"""scripts/eval/regrade_run.py: load, re-grade, print, write and store a re-graded run.

Synthetic cases and answers graded by the real graders; the only fake is the run store
(the database boundary). No DB, LLM or network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase, EvalSuite
from src.eval.core.checkpoint.base import RunStore
from src.eval.core.checkpoint.run import checkpoint_payload
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.system import SystemAnswer

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "regrade_run.py"
_spec = importlib.util.spec_from_file_location("regrade_run", _SCRIPT)
regrade_run = importlib.util.module_from_spec(_spec)
sys.modules["regrade_run"] = regrade_run
_spec.loader.exec_module(regrade_run)

SOURCE_ID = "eval-aaaaaaaaaaaa"


class _Store(RunStore):
    """An in-memory run store holding one source run."""

    def __init__(self, report: RunReport | None = None, *, finish_ok: bool = True,
                 status: str | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.payloads: dict[str, list[dict[str, Any]]] = {}
        self.finished: list[RunReport] = []
        self.finish_ok = finish_ok
        if report is not None:
            self.rows[report.run_id] = {
                "run_id": report.run_id, "suite": report.suite,
                "suite_version": report.suite_version, "system": report.system,
                "tier": report.tier, "git_sha": report.git_sha,
                "status": status or report.status, "started_at": report.started_at,
                "finished_at": report.finished_at,
                "config": json.loads(json.dumps(report.config)),
                "n_planned": report.n_planned, "split": report.config["split"],
                "grader_version": "3", "corpus_fingerprint": report.corpus_fingerprint,
            }
            self.payloads[report.run_id] = [
                json.loads(json.dumps(checkpoint_payload(r), default=str))
                for r in report.results
            ]

    def start_run(self, report: RunReport) -> None:
        raise AssertionError("a re-grade never starts a run")

    def write_case(self, run_id: str, result: CaseResult) -> bool:
        raise AssertionError("a re-grade never checkpoints a case")

    def finish_run(self, report: RunReport) -> bool:
        self.finished.append(report)
        return self.finish_ok

    def load_run_row(self, run_id: str) -> dict[str, Any] | None:
        return self.rows.get(run_id)

    def load_case_payloads(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.payloads.get(run_id, []))


def _case(case_id: str, value: float, split: str = "dev") -> EvalCase:
    return EvalCase(id=case_id, question=f"What was revenue for {case_id}?", kind="lookup",
                    expected_value=value, rel_tol=1e-4, tags=[f"split:{split}"])


def _source(split: str = "dev") -> tuple[list[EvalCase], RunReport]:
    cases = [_case("a1", 100.0, split), _case("a2", 200.0, split)]
    answers = [SystemAnswer(numeric_values=[100.0], fact_ids=[1], query_id="q-1"),
               SystemAnswer(numeric_values=[250.0], fact_ids=[1], query_id="q-2")]
    results = [CaseResult.build(c, a, grade_case(c, a)) for c, a in zip(cases, answers,
                                                                          strict=True)]
    report = RunReport(
        run_id=SOURCE_ID, suite="xbrl_v2_dev", suite_version="1", system="xbrlrag_full",
        status="OK", results=results, n_planned=2,
        config={"split": split, "resume_identity": {"suite_hash": "sha256:x"}},
    )
    return cases, report


def _suite_file(tmp_path: Path, cases: list[EvalCase], *, fix_a2: bool) -> Path:
    data = [c.to_dict() for c in cases]
    if fix_a2:
        data[1]["expected_value"] = 250.0
    path = tmp_path / "suite.json"
    EvalSuite.from_dict({"name": "xbrl_v2_dev", "version": "2", "cases": data,
                         "corpus_fingerprint": "pm:new"}).save(str(path))
    return path


def _report_file(tmp_path: Path, report: RunReport) -> Path:
    path = tmp_path / "source.json"
    path.write_text(json.dumps(report.to_dict(), default=str), encoding="utf-8")
    return path


def test_a_json_report_regrades_writes_out_and_stores(tmp_path, capsys):
    cases, report = _source()
    store = _Store()
    out = tmp_path / "out" / "regraded.json"
    code = regrade_run.main(
        ["--source", str(_report_file(tmp_path, report)),
         "--suite", str(_suite_file(tmp_path, cases, fix_a2=True)),
         "--run-id", "eval-bbbbbbbbbbbb", "--out", str(out)],
        store=lambda: store,
    )
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["run_id"] == "eval-bbbbbbbbbbbb"
    assert written["suite"] == "xbrl_v2_dev" and written["suite_version"] == "2"
    assert written["corpus_fingerprint"] == "pm:new"
    assert written["config"]["regrade"]["n_grade_changed"] == 1
    assert [r["grade"] for r in written["results"]] == ["CORRECT", "CORRECT"]
    assert [r.run_id for r in store.finished] == ["eval-bbbbbbbbbbbb"]
    printed = capsys.readouterr().out
    assert "INCORRECT -> CORRECT" in printed
    assert "a2" in printed


def test_a_stored_run_id_loads_through_the_store(tmp_path):
    cases, report = _source()
    store = _Store(report)
    code = regrade_run.main(
        ["--source", SOURCE_ID, "--suite", str(_suite_file(tmp_path, cases, fix_a2=False))],
        store=lambda: store,
    )
    assert code == 0
    (new,) = store.finished
    assert new.run_id != SOURCE_ID
    assert new.config["regrade"]["source_run_id"] == SOURCE_ID
    assert new.config["regrade"]["n_grade_changed"] == 0
    assert [r.query_id for r in new.results] == ["q-1", "q-2"]


def test_no_store_never_builds_the_store_for_a_report(tmp_path):
    cases, report = _source()

    def no_store() -> RunStore:
        raise AssertionError("the store must not be built")

    code = regrade_run.main(
        ["--source", str(_report_file(tmp_path, report)),
         "--suite", str(_suite_file(tmp_path, cases, fix_a2=False)), "--no-store"],
        store=no_store,
    )
    assert code == 0


def test_a_refused_regrade_exits_2_and_stores_nothing(tmp_path, capsys):
    cases, report = _source()
    store = _Store(report, status="RUNNING")
    code = regrade_run.main(
        ["--source", SOURCE_ID, "--suite", str(_suite_file(tmp_path, cases, fix_a2=False))],
        store=lambda: store,
    )
    assert code == 2
    assert store.finished == []
    assert "RUNNING" in capsys.readouterr().err


def test_a_case_missing_from_the_suite_exits_2(tmp_path, capsys):
    cases, report = _source()
    store = _Store()
    code = regrade_run.main(
        ["--source", str(_report_file(tmp_path, report)),
         "--suite", str(_suite_file(tmp_path, cases[:1] + [_case("zz", 1.0)], fix_a2=False))],
        store=lambda: store,
    )
    assert code == 2
    assert store.finished == []
    assert "a2" in capsys.readouterr().err


def test_a_failed_store_exits_1(tmp_path):
    cases, report = _source()
    store = _Store(finish_ok=False)
    code = regrade_run.main(
        ["--source", str(_report_file(tmp_path, report)),
         "--suite", str(_suite_file(tmp_path, cases, fix_a2=False))],
        store=lambda: store,
    )
    assert code == 1


@pytest.mark.parametrize("split", ["heldout_filers", "external", "temporal"])
def test_a_heldout_run_prints_no_case_ids(tmp_path, capsys, split):
    cases, report = _source(split)
    code = regrade_run.main(
        ["--source", str(_report_file(tmp_path, report)),
         "--suite", str(_suite_file(tmp_path, cases, fix_a2=True)), "--no-store"],
        store=_Store,
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "INCORRECT -> CORRECT" in printed
    assert "What was" not in printed
    assert "lookup" not in printed  # no per-case line (kind column)
