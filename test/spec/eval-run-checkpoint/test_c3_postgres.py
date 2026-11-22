"""C3: PostgresEvalStore checkpoints in separate transactions (requirement 6).

The database driver is faked (conftest ``fake_pg``, which records committed transactions);
the store, its SQL and the payload are real.
"""

from __future__ import annotations

import json
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.base import RunStore
from src.eval.core.checkpoint.run import checkpoint_payload
from src.eval.core.report import RunReport
from src.eval.core.store import PostgresEvalStore


def _store() -> PostgresEvalStore:
    return PostgresEvalStore(conn_params={"dbname": "fake"})


def _decoded(value):
    """A JSONB parameter as the database would hold it: a JSON string, or a mapping."""
    return json.loads(value) if isinstance(value, str) else dict(value)


def _results(kit, n):
    return [kit.graded(kit.make_case(f"c{i}"), kit.answer_for(i % 2 == 0, 0.25))
            for i in range(n)]


@pytest.mark.criterion("C3")
def test_postgres_store_is_a_run_store():
    # oracle: requirement 6
    assert issubclass(PostgresEvalStore, RunStore)


@pytest.mark.criterion("C3")
def test_start_run_commits_one_running_run_row(kit, fake_pg):
    # oracle: requirement 6 — one transaction, one eval_run INSERT, status RUNNING, no cases
    report = RunReport(run_id="run-pg", suite="s", system="fake_system", n_planned=3,
                       config={"resume_identity": {"system": "fake_system"}})
    with fake_pg() as rec:
        _store().start_run(report)
    (txn,) = rec.transactions
    (row,) = rec.inserts(txn, "eval_run")
    assert row["run_id"] == "run-pg"
    assert row["status"] == "RUNNING"
    assert _decoded(row["config"])["resume_identity"] == {"system": "fake_system"}
    assert rec.inserts(txn, "eval_case_result") == []


@pytest.mark.criterion("C3")
def test_start_run_raises_when_the_database_is_down(kit, fake_pg):
    # oracle: requirement 3 — a run must not start unstored
    report = RunReport(run_id="run-pg", suite="s", system="fake_system")
    with fake_pg() as rec:
        rec.fail_connect = True
        with pytest.raises(ConnectionError):
            _store().start_run(report)


@pytest.mark.criterion("C3")
@given(n=st.integers(1, 5))
def test_each_write_case_is_its_own_transaction(kit, fake_pg, n):
    # oracle: requirement 6 — n writes give n commits, each one case INSERT whose
    # result_payload is that case's payload
    results = _results(kit, n)
    with fake_pg() as rec:
        oks = [_store().write_case("run-pg", r) for r in results]
    assert oks == [True] * n
    assert len(rec.transactions) == n
    for txn, result in zip(rec.transactions, results, strict=True):
        assert rec.inserts(txn, "eval_run") == []
        (row,) = rec.inserts(txn, "eval_case_result")
        assert row["run_id"] == "run-pg"
        assert row["case_id"] == result.case.id
        assert _decoded(row["result_payload"]) == json.loads(
            json.dumps(checkpoint_payload(result)))


@pytest.mark.criterion("C3")
def test_write_case_reports_a_down_database_without_raising(kit, fake_pg):
    # oracle: requirement 4 — a failed write returns False; the run goes on
    (result,) = _results(kit, 1)
    with fake_pg() as rec:
        rec.fail_connect = True
        assert _store().write_case("run-pg", result) is False
    assert rec.transactions == []


@pytest.mark.criterion("C3")
def test_finish_run_writes_the_scorecard_and_every_payload(kit, fake_pg):
    # oracle: requirement 6 — finish_run is persist_run with result_payload on every row
    results = _results(kit, 3)
    report = RunReport(run_id="run-pg", suite="s", system="fake_system", status="OK",
                       results=results, n_planned=3, finished_at="2026-11-03T00:00:00+00:00")
    with fake_pg() as rec:
        assert _store().finish_run(report) is True
    rows = [p for txn in rec.transactions for p in rec.inserts(txn, "eval_case_result")]
    (run_row,) = [p for txn in rec.transactions for p in rec.inserts(txn, "eval_run")]
    assert run_row["status"] == "OK"
    assert run_row["n_cases"] == 3
    assert {r["case_id"] for r in rows} == {"c0", "c1", "c2"}
    by_id = {r.case.id: r for r in results}
    for row in rows:
        assert _decoded(row["result_payload"]) == json.loads(
            json.dumps(checkpoint_payload(by_id[row["case_id"]])))


@pytest.mark.criterion("C3")
def test_the_ddl_adds_the_payload_column(eval_runs_sql):
    # oracle: requirement 6 — the exact statement, re-appliable
    pattern = (r"ALTER\s+TABLE\s+eval\.eval_case_result\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
               r"result_payload\s+JSONB\s*;")
    assert re.search(pattern, eval_runs_sql, re.IGNORECASE)
