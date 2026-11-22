"""Builders for the eval-regrade spec tests.

Everything is synthetic: hand-built cases and answers, sources graded by the real graders and
serialised the way ``run_suite.py --out`` (``RunReport.to_dict()``) and the store
(``checkpoint_payload``) write them. The only fake is the in-memory ``RunStore`` (the database
boundary). Fixtures are session-scoped factories so hypothesis tests can use them.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.base import RunStore
from src.eval.core.checkpoint.run import checkpoint_payload
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.system import SystemAnswer

SOURCE_RUN_ID = "eval-aaaaaaaaaaaa"
SOURCE_SUITE_HASH = "sha256:" + "ab" * 32


class MemoryStore(RunStore):
    """An in-memory run store: one run row and its case payloads per run id."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.payloads: dict[str, list[dict[str, Any]]] = {}
        self.finished: list[RunReport] = []

    def start_run(self, report: RunReport) -> None:
        raise AssertionError("a re-grade never starts a run")

    def write_case(self, run_id: str, result: CaseResult) -> bool:
        raise AssertionError("a re-grade never checkpoints a case")

    def finish_run(self, report: RunReport) -> bool:
        self.finished.append(report)
        return True

    def load_run_row(self, run_id: str) -> dict[str, Any] | None:
        row = self.rows.get(run_id)
        return copy.deepcopy(row) if row is not None else None

    def load_case_payloads(self, run_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self.payloads.get(run_id, []))


@pytest.fixture(scope="session")
def lookup_case():
    """Factory: a dev-split `lookup` case expecting `value` (rel_tol 1e-4)."""

    def make(
        case_id: str,
        value: float,
        *,
        split: str = "dev",
        question: str | None = None,
        concept: str = "Revenues",
    ) -> EvalCase:
        tags = [f"split:{split}"] if split != "legacy" else []
        return EvalCase(
            id=case_id,
            question=question or f"What was {concept} for {case_id} in fiscal 2024?",
            kind="lookup",
            expected_value=value,
            rel_tol=1e-4,
            ticker=case_id.upper(),
            fy=2024,
            concept=concept,
            tags=tags,
        )

    return make


@pytest.fixture(scope="session")
def text_case():
    """Factory: a dev-split text-graded case (expected text and key points)."""

    def make(case_id: str, expected_text: str, *, key_points: list[str] | None = None) -> EvalCase:
        return EvalCase(
            id=case_id,
            question=f"Describe the main risk {case_id} reports.",
            kind="narrative",
            expected_text=expected_text,
            key_points=list(["the risk"] if key_points is None else key_points),
            tags=["split:dev", "text"],
        )

    return make


@pytest.fixture(scope="session")
def answer():
    """Factory: an answer stating `values`, cited with one fact id per value."""

    def make(
        values: list[float] | None = None,
        *,
        refused: bool = False,
        hedged: bool = False,
        cited: bool = True,
        error: str | None = None,
        text: str = "synthetic answer",
        query_id: str | None = None,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
    ) -> SystemAnswer:
        values = list(values or [])
        return SystemAnswer(
            text=text,
            numeric_values=values,
            fact_ids=list(range(1, len(values) + 1)) if cited else [],
            refused=refused,
            hedged=hedged,
            error=error,
            query_id=query_id,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    return make


@pytest.fixture(scope="session")
def build_report():
    """Factory: a graded source run, as the runner builds it, over (case, answer) pairs."""

    def make(
        pairs: list[tuple[EvalCase, SystemAnswer]],
        *,
        run_id: str = SOURCE_RUN_ID,
        split: str = "dev",
        n_planned: int | None = None,
        status: str = "OK",
        judge: dict[str, dict[str, Any]] | None = None,
    ) -> RunReport:
        results = []
        for case, ans in pairs:
            result = CaseResult.build(case, ans, grade_case(case, ans))
            if judge and case.id in judge:
                result.grade.judge_scores = dict(judge[case.id])
            results.append(result)
        return RunReport(
            run_id=run_id,
            suite="xbrl_v2_dev",
            suite_version="1",
            system="xbrlrag_full",
            tier="standard",
            corpus_fingerprint="pm:source",
            git_sha="1234abc",
            started_at="2026-11-04T10:00:00+00:00",
            finished_at="2026-11-04T11:00:00+00:00",
            status=status,
            results=results,
            n_planned=len(results) if n_planned is None else n_planned,
            config={
                "split": split,
                "resume_identity": {"suite_hash": SOURCE_SUITE_HASH, "system": "xbrlrag_full"},
                "keyfree": {"n_pairs": 0},
            },
        )

    return make


@pytest.fixture(scope="session")
def as_json_source():
    """Turns a report into the source a `run_suite.py --out` JSON report gives."""

    def make(report: RunReport) -> dict[str, Any]:
        return json.loads(json.dumps(report.to_dict(), default=str))

    return make


@pytest.fixture(scope="session")
def as_payload_source():
    """Turns a report into a source whose results are stored checkpoint payloads."""

    def make(report: RunReport) -> dict[str, Any]:
        data = json.loads(json.dumps(report.to_dict(include_results=False), default=str))
        data["results"] = [
            json.loads(json.dumps(checkpoint_payload(r), default=str)) for r in report.results
        ]
        return data

    return make


@pytest.fixture(scope="session")
def store_with():
    """Factory: a MemoryStore holding one run's row and payloads."""

    def make(report: RunReport, *, status: str | None = None, payloads: bool = True,
             row_split: bool = True) -> MemoryStore:
        store = MemoryStore()
        row: dict[str, Any] = {
            "run_id": report.run_id,
            "suite": report.suite,
            "suite_version": report.suite_version,
            "system": report.system,
            "tier": report.tier,
            "git_sha": report.git_sha,
            "status": status or report.status,
            "started_at": report.started_at,
            "finished_at": report.finished_at,
            "config": json.loads(json.dumps(report.config, default=str)),
            "n_planned": report.n_planned,
            "grader_version": "3",
            "corpus_fingerprint": report.corpus_fingerprint,
        }
        if row_split:
            row["split"] = report.config["split"]
        store.rows[report.run_id] = row
        if payloads:
            store.payloads[report.run_id] = [
                json.loads(json.dumps(checkpoint_payload(r), default=str))
                for r in report.results
            ]
        return store

    return make


@pytest.fixture(scope="session")
def rekey():
    """Factory: a copy of a case with a new expected value (the corrected key)."""

    def make(case: EvalCase, value: float) -> EvalCase:
        data = case.to_dict()
        data["expected_value"] = value
        return EvalCase.from_dict(data)

    return make
