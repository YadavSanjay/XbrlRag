"""Live-DB read-back of a checkpointed eval run (docs/specs/eval-run-checkpoint.md).

The spec tests have no Postgres, so this is the check that ``PostgresEvalStore`` round-trips
a run through the real ``eval`` schema: ``start_run``, ``write_case`` and ``finish_run``,
read back through ``load_run_row`` and ``load_case_payloads``, and a resume of the stored
run that restores every case without answering any. The systems are fakes: no LLM call.

It WRITES to the ``eval`` schema (one run with a unique ``itest-ckpt-`` id, deleted at the
end, case rows by cascade), so it runs only when explicitly enabled, and needs
``scripts/ddl/eval/eval_runs.sql`` applied (the ``result_payload`` column)::

    $env:XBRLRAG_LIVE_EVAL_STORE='1'
    conda run -n py312 python -m pytest -m integration -v `
        test/integration/test_eval_checkpoint_live.py
"""
from __future__ import annotations

import os
import uuid

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.run import checkpoint_payload, resume_identity, run_checkpointed
from src.eval.core.report import CaseResult
from src.eval.core.runner import RunnerConfig
from src.eval.core.store import PostgresEvalStore
from src.eval.core.system import SystemAnswer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("XBRLRAG_LIVE_EVAL_STORE") != "1",
        reason="writes to the eval schema; set XBRLRAG_LIVE_EVAL_STORE=1 to run",
    ),
]


class _Fake:
    """A deterministic arm: right on every case except ``wrong``."""

    name = "itest_fake"

    def __init__(self, wrong: set[str] | None = None) -> None:
        self.wrong = set(wrong or ())
        self.answered: list[str] = []

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.answered.append(case.id)
        value = 5.0 if case.id in self.wrong else 1.0
        return SystemAnswer(numeric_values=[value], fact_ids=[1], latency_ms=12.345678,
                            cost_usd=0.0012345678)


def _cases() -> list[EvalCase]:
    return [EvalCase(id=f"itest-{i}", question=f"itest question {i}", kind="lookup",
                     expected_value=1.0, expected_fact_ids=[1]) for i in range(3)]


def _delete(store: PostgresEvalStore, run_id: str) -> None:
    conn = store._connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM eval.eval_run WHERE run_id = %s", (run_id,))
    finally:
        conn.close()


def test_a_checkpointed_run_reads_back_and_resumes():
    store = PostgresEvalStore.from_env()
    run_id = f"itest-ckpt-{uuid.uuid4().hex[:12]}"
    cases = _cases()
    identity = resume_identity(cases, system=_Fake.name, model=None, llm_profile="itest",
                               git_sha="itest-sha", settings={"cache_groups": None})
    try:
        (report,) = run_checkpointed(
            cases, [_Fake(wrong={"itest-1"})], store=store, identities=[identity],
            run_ids=[run_id], config=RunnerConfig(max_workers=1), suite="itest",
            suite_version="v1")
        assert report.status == "OK"
        assert report.config["checkpoint_write_failures"] == 0

        row = store.load_run_row(run_id)
        assert row is not None
        assert row["run_id"] == run_id and row["system"] == _Fake.name
        assert row["status"] == "OK"
        assert row["config"]["resume_identity"]["suite_hash"] == identity["suite_hash"]
        assert len(row["config"]["segments"]) == 1
        assert isinstance(row["started_at"], str)

        payloads = {p["case_id"]: p for p in store.load_case_payloads(run_id)}
        assert set(payloads) == {c.id for c in cases}
        by_id = {r.case.id: r for r in report.results}
        for case in cases:
            restored = CaseResult.from_dict(payloads[case.id], case)
            assert restored.grade == by_id[case.id].grade
            assert restored.cost_usd == by_id[case.id].cost_usd  # exact, not NUMERIC(12,6)
            assert payloads[case.id] == checkpoint_payload(by_id[case.id])

        again = _Fake(wrong={"itest-1"})
        (resumed,) = run_checkpointed(
            cases, [again], store=store, identities=[identity], resume=run_id,
            config=RunnerConfig(max_workers=1), suite="itest", suite_version="v1")
        assert again.answered == []
        assert resumed.run_id == run_id
        assert resumed.started_at == row["started_at"]
        assert resumed.metrics == report.metrics
        assert len(store.load_run_row(run_id)["config"]["segments"]) == 2
    finally:
        _delete(store, run_id)
