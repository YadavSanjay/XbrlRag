"""Wiring of v2 grounding into run_suite and regrade_run (docs/specs/v2-grounding.md req. 14).

Synthetic v2 cases graded by the real graders; the fakes are the run store and the fact
store (the database boundaries). No DB, LLM or network.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase, EvalSuite
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.grounding_v2 import ground_results
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.system import SystemAnswer
from src.eval.sec_check.base import FactRecord, FactStore

ROOT = Path(__file__).resolve().parents[2]
CIK = 1000
FY = ("2025-01-01", "2025-12-31")
US_GAAP = "http://fasb.org/us-gaap/2025"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


regrade_run = _load("regrade_run_grounding", "scripts/eval/regrade_run.py")


class _FactStore(FactStore):
    """An in-memory fact store that records its reads."""

    def __init__(self, records: dict[int, FactRecord], *, fail: bool = False) -> None:
        self.records = records
        self.calls: list[list[int]] = []
        self.fail = fail
        self.closed = False

    def facts(self, fact_ids: list[int]) -> dict[int, FactRecord]:
        self.calls.append(list(fact_ids))
        if self.fail:
            raise ConnectionError("xbrl_db unreachable")
        return {i: self.records[i] for i in fact_ids if i in self.records}

    def metric_components(self, ticker: str, fiscal_year: int, metric: str) -> dict | None:
        return None

    def close(self) -> None:
        self.closed = True


def _record(fact_id: int, local: str, value: float) -> FactRecord:
    return FactRecord(fact_id=fact_id, ticker="AAA", cik=str(CIK).zfill(10),
                      accession_no="0000001000-26-000001", concept=f"{US_GAAP}:{local}",
                      unit="USD", period_start=FY[0], period_end=FY[1], value=value,
                      dimensional=False)


RECORDS = {1: _record(1, "Revenues", 1.0e11), 2: _record(2, "CostOfRevenue", 6.0e10)}


def _v2_case(case_id: str = "v2-a") -> EvalCase:
    key_input = {"cik": str(CIK).zfill(10), "concept": "us-gaap:Revenues", "unit": "USD",
                 "period_start": FY[0], "period_end": FY[1], "value": 1.0e11,
                 "decimals": -6, "accession": "0000001000-26-000001"}
    return EvalCase.from_dict({
        "id": case_id, "question": f"What was revenue for {case_id}?", "kind": "lookup",
        "expected_value": 1.0e11, "rel_tol": 1e-4, "tags": ["split:dev", "shape:lookup"],
        "key": {"inputs": [key_input], "source": "sec_companyfacts"},
    })


def _v1_case(case_id: str = "v1-a") -> EvalCase:
    return EvalCase(id=case_id, question=f"What was revenue for {case_id}?", kind="lookup",
                    expected_value=1.0e11, rel_tol=1e-4, expected_fact_ids=[1],
                    tags=["split:dev"])


def _result(case: EvalCase, fact_ids: list[int]) -> CaseResult:
    answer = SystemAnswer(numeric_values=[1.0e11], fact_ids=fact_ids, query_id="q")
    return CaseResult.build(case, answer, grade_case(case, answer))


def _report(results: list[CaseResult], fingerprint: str | None = "pm:run") -> RunReport:
    return RunReport(run_id="eval-aaaaaaaaaaaa", suite="xbrl_v2_dev", suite_version="1",
                     system="xbrlrag_full", status="OK", results=results,
                     n_planned=len(results), corpus_fingerprint=fingerprint,
                     config={"split": "dev"})


# --- ground_results ---------------------------------------------------------------------


def test_no_v2_case_never_builds_the_store():
    built: list[int] = []
    results = [_result(_v1_case(), [1])]
    before = (results[0].grade.grounding_precision, results[0].grade.grounding_recall)
    outcome = ground_results(results, lambda: built.append(1) or _FactStore(RECORDS))
    assert outcome == {"status": "not_applicable"}
    assert built == []
    assert (results[0].grade.grounding_precision,
            results[0].grade.grounding_recall) == before


def test_attaches_counts_and_closes_the_store():
    store = _FactStore(RECORDS)
    results = [_result(_v2_case("v2-a"), [1, 2, 9]), _result(_v2_case("v2-b"), [1])]
    outcome = ground_results(results, lambda: store, run_fingerprint="pm:x",
                             store_fingerprint=lambda: "pm:x")
    assert outcome == {"status": "attached", "n_cases": 2, "n_citations": 4,
                       "n_unresolved": 1, "store_fingerprint": "pm:x"}
    assert store.calls == [[1, 2, 9]]  # one read over every cited id
    assert store.closed
    assert results[0].grade.grounding_precision == pytest.approx(1 / 3)
    assert (results[1].grade.grounding_precision, results[1].grade.grounding_recall) == (1, 1)


def test_a_fingerprint_mismatch_leaves_none_and_reads_nothing():
    built: list[int] = []
    results = [_result(_v2_case(), [1])]
    outcome = ground_results(results, lambda: built.append(1) or _FactStore(RECORDS),
                             run_fingerprint="pm:old", store_fingerprint=lambda: "pm:new")
    assert outcome["status"] == "fingerprint_mismatch"
    assert outcome["store_fingerprint"] == "pm:new"
    assert built == []
    assert results[0].grade.grounding_precision is None
    assert results[0].grade.grounding_recall is None


@pytest.mark.parametrize("factory_fails", [True, False])
def test_an_unreachable_store_keeps_none_never_zero(factory_fails):
    def factory() -> FactStore:
        if factory_fails:
            raise OSError("no route to xbrl_db")
        return _FactStore(RECORDS, fail=True)

    results = [_result(_v2_case("v2-a"), [1]), _result(_v2_case("v2-b"), [])]
    outcome = ground_results(results, factory)
    assert outcome["status"] == "store_unavailable"
    assert [(r.grade.grounding_precision, r.grade.grounding_recall) for r in results] == [
        (None, None), (None, None)]


# --- run_suite --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_suite():
    return _load("run_suite_grounding", "scripts/eval/run_suite.py")


def test_run_suite_attaches_before_the_report_is_stored(run_suite, capsys):
    store = _FactStore(RECORDS)
    report = _report([_result(_v2_case("v2-a"), [1, 2]), _result(_v1_case(), [1])])
    run_suite._attach_grounding(report, fact_store=lambda: store)
    assert report.config["grounding_v2"]["status"] == "attached"
    assert report.config["grounding_v2"]["n_cases"] == 1
    assert report.results[0].grade.grounding_precision == 0.5
    assert report.metrics.grounding_precision == pytest.approx((0.5 + 1.0) / 2)
    assert "v2 grounding attached to 1 case(s)" in capsys.readouterr().out


def test_run_suite_skips_an_arm_that_cites_nothing(run_suite):
    built: list[int] = []
    report = _report([_result(_v2_case(), [])])
    run_suite._attach_grounding(report, fact_store=lambda: built.append(1) or _FactStore({}))
    assert built == []
    assert "grounding_v2" not in report.config
    assert report.metrics.grounding_precision is None


def test_run_suite_finish_calls_the_attach(run_suite, monkeypatch):
    calls: list[RunReport] = []
    monkeypatch.setattr(run_suite, "_attach_grounding", calls.append)
    monkeypatch.setattr(run_suite, "_print_report", lambda *a: None)
    monkeypatch.setattr(run_suite, "_print_extras", lambda *a: None)
    report = _report([_result(_v2_case(), [1])])
    system = argparse.Namespace(name="xbrlrag_full")
    args = argparse.Namespace(perturb_twins=False, no_store=True, top_failures=0,
                              rerun_failed=None, sample=None, limit=None, kind=None,
                              tag=None, ticker=None)
    monkeypatch.setattr(run_suite, "_selection_note", lambda *a: "")
    run_suite._finish(report, system, [], args, "openai", None, out=None)
    assert calls == [report]


# --- regrade_run ------------------------------------------------------------------------


class _RunStore:
    def __init__(self) -> None:
        self.finished: list[RunReport] = []

    def finish_run(self, report: RunReport) -> bool:
        self.finished.append(report)
        return True


def _files(tmp_path: Path, fingerprint: str | None) -> tuple[Path, Path]:
    cases = [_v2_case("v2-a"), _v2_case("v2-b")]
    results = [_result(cases[0], [1, 2]), _result(cases[1], [1])]
    source = tmp_path / "source.json"
    source.write_text(json.dumps(_report(results, fingerprint).to_dict(), default=str),
                      encoding="utf-8")
    suite = tmp_path / "suite.json"
    EvalSuite.from_dict({"name": "xbrl_v2_dev", "version": "2",
                         "cases": [c.to_dict() for c in cases],
                         "corpus_fingerprint": "pm:suite"}).save(str(suite))
    return source, suite


def _regrade(tmp_path: Path, run_fp: str, store_fp: str, *extra: str) -> tuple[int, Any]:
    source, suite = _files(tmp_path, run_fp)
    store = _RunStore()
    code = regrade_run.main(
        ["--source", str(source), "--suite", str(suite), "--run-id", "eval-bbbbbbbbbbbb",
         *extra],
        store=lambda: store, fact_store=lambda: _FactStore(RECORDS),
        fingerprint=lambda: store_fp,
    )
    return code, store


def test_regrade_fills_grounding_when_the_fingerprint_matches(tmp_path, capsys):
    code, store = _regrade(tmp_path, "pm:same", "pm:same")
    assert code == 0
    (report,) = store.finished
    got = [(r.grade.grounding_precision, r.grade.grounding_recall) for r in report.results]
    assert got == [(0.5, 1.0), (1.0, 1.0)]
    assert report.metrics.grounding_precision == pytest.approx(0.75)
    assert report.config["grounding_v2"]["status"] == "attached"
    assert report.config["grounding_v2"]["n_unresolved"] == 0
    assert "regrade" in report.config
    printed = capsys.readouterr().out
    assert "v2 grounding: 2 case(s), 3 citation(s), 0 unresolved" in printed


def test_regrade_refuses_grounding_on_another_corpus(tmp_path, capsys):
    code, store = _regrade(tmp_path, "pm:old", "pm:new")
    assert code == 0
    (report,) = store.finished
    assert all(r.grade.grounding_precision is None for r in report.results)
    assert report.metrics.grounding_precision is None
    assert report.config["grounding_v2"]["status"] == "fingerprint_mismatch"
    assert "NOT attached (fingerprint_mismatch)" in capsys.readouterr().out


def test_regrade_no_grounding_reads_no_fact_store(tmp_path):
    source, suite = _files(tmp_path, "pm:same")
    store = _RunStore()

    def boom() -> str:
        raise AssertionError("--no-grounding must not read the fact store")

    code = regrade_run.main(
        ["--source", str(source), "--suite", str(suite), "--no-grounding"],
        store=lambda: store, fact_store=boom, fingerprint=boom,
    )
    assert code == 0
    assert "grounding_v2" not in store.finished[0].config


def test_a_run_without_a_fingerprint_needs_the_operators_statement(tmp_path):
    source, suite = _files(tmp_path, None)  # every stored v2 run so far recorded none
    store = _RunStore()

    def regrade(*extra: str) -> RunReport:
        code = regrade_run.main(
            ["--source", str(source), "--suite", str(suite), *extra],
            store=lambda: store, fact_store=lambda: _FactStore(RECORDS),
            fingerprint=lambda: "pm:now",
        )
        assert code == 0
        return store.finished[-1]

    unstated = regrade()
    assert unstated.config["grounding_v2"]["status"] == "fingerprint_mismatch"
    assert unstated.metrics.grounding_precision is None
    stated = regrade("--answered-on-corpus", "pm:now")
    assert stated.config["grounding_v2"]["status"] == "attached"
    assert stated.config["grounding_v2"]["answered_on_corpus_asserted"] == "pm:now"
    assert stated.metrics.grounding_precision == pytest.approx(0.75)


def test_the_statement_never_overrides_a_recorded_fingerprint(tmp_path):
    code, store = _regrade(tmp_path, "pm:old", "pm:now", "--answered-on-corpus", "pm:now")
    assert code == 0
    assert store.finished[0].config["grounding_v2"]["status"] == "fingerprint_mismatch"
