"""Wiring for eval-run-checkpoint that the locked spec tests don't reach.

docs/specs/eval-run-checkpoint.md: ``run_suite.py``'s ``--resume`` / ``--retry-errors``
plumbing into ``run_checkpointed`` (the spec tests check only the parser's refusals),
the arms storing ``error_text`` instead of ``repr``, a system's ``RunHalt`` stopping
``run_many``, the per-segment service tier, and ``PostgresEvalStore``'s read-back
methods against a fake driver. No network, database or LLM: the run step is replaced
by a recorder, the store by an in-memory one, the pipeline and the driver by fakes.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.base import NullRunStore, RunStore
from src.eval.core.checkpoint.run import (
    ResumeRefused,
    checkpoint_payload,
    error_origin,
    error_text,
    resume_identity,
    run_checkpointed,
)
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.runner import EvalRunner, RunnerConfig
from src.eval.core.store import PostgresEvalStore
from src.eval.core.system import CallableSystem, SystemAnswer
from src.llm.request_options import SERVICE_TIER_ENV
from src.llm.run_budget import CircuitBreakerOpen
from src.observability.trace_context import get_audit_sink, set_audit_sink

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "run_suite.py"


class _NoArgsError(Exception):
    """Like Qdrant's UnexpectedResponse: no args, so repr() drops the message."""

    def __str__(self) -> str:
        return "Unexpected Response: 503 (Service Unavailable)"


def _case(cid: str) -> EvalCase:
    return EvalCase(id=cid, question=f"question {cid}", kind="lookup", expected_value=1.0,
                    expected_fact_ids=[1])


class _Right:
    name = "fake_system"

    def __init__(self) -> None:
        self.answered: list[str] = []

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.answered.append(case.id)
        return SystemAnswer(numeric_values=[1.0], fact_ids=[1], latency_ms=1.0)


class _Memory(RunStore):
    """An in-memory RunStore (the database boundary)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.cases: dict[str, dict[str, dict[str, Any]]] = {}
        self.finished: list[str] = []

    def start_run(self, report: RunReport) -> None:
        self.rows[report.run_id] = {"run_id": report.run_id, "system": report.system,
                                    "status": report.status, "started_at": report.started_at,
                                    "config": json.loads(json.dumps(report.config))}

    def write_case(self, run_id: str, result: CaseResult) -> bool:
        payload = json.loads(json.dumps(checkpoint_payload(result)))
        self.cases.setdefault(run_id, {})[result.case.id] = payload
        return True

    def finish_run(self, report: RunReport) -> bool:
        self.finished.append(report.run_id)
        self.start_run(report)
        for result in report.results:
            self.write_case(report.run_id, result)
        return True

    def load_run_row(self, run_id: str) -> dict[str, Any] | None:
        return self.rows.get(run_id)

    def load_case_payloads(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.cases.get(run_id, {}).values())


def _identity(cases, name="fake_system"):
    return resume_identity(cases, system=name, model=None, llm_profile="deepseek",
                           git_sha="abc1234", settings={"cache_groups": None})


# ---------------------------------------------------------------------------- error_text


def test_callable_system_stores_error_text():
    def boom(_question):
        raise _NoArgsError()

    answer = CallableSystem("c", boom).answer(_case("a"))
    assert answer.error == "_NoArgsError('Unexpected Response: 503 (Service Unavailable)')"
    assert answer.error == error_text(_NoArgsError())
    assert error_origin(answer.error) == "transport"


def test_stage_llm_arm_stores_error_text():
    from src.eval.systems._stage_llm import answer_in_trace

    def produce():
        raise _NoArgsError()

    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        answer = answer_in_trace("q", produce)
    finally:
        set_audit_sink(previous)
    assert answer.error == error_text(_NoArgsError())


class _FailingPipeline:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def generate(self, _question):
        raise self.exc


def _full_system(monkeypatch, exc: BaseException):
    from src.eval.systems import xbrlrag_full

    monkeypatch.setattr(xbrlrag_full, "_install_sink", lambda _persist: None)
    return xbrlrag_full.XbrlRagFullSystem(_FailingPipeline(exc), transient_retries=0)


def test_xbrlrag_full_stores_error_text(monkeypatch):
    answer = _full_system(monkeypatch, _NoArgsError()).answer(_case("a"))
    assert answer.error == error_text(_NoArgsError())


def test_xbrlrag_full_lets_a_run_halt_through(monkeypatch):
    system = _full_system(monkeypatch, CircuitBreakerOpen("3 consecutive failures"))
    with pytest.raises(CircuitBreakerOpen):
        system.answer(_case("a"))


def test_xbrlrag_full_construction_failure_keeps_its_prefix(monkeypatch):
    from src.eval.systems import xbrlrag_full

    system = xbrlrag_full.XbrlRagFullSystem()

    def fail():
        raise _NoArgsError()

    monkeypatch.setattr(system, "_get_pipeline", fail)
    answer = system.answer(_case("a"))
    assert answer.error == "pipeline construction failed: " + error_text(_NoArgsError())
    assert error_origin(answer.error) == "arm"  # requirement 13's table


def test_perturbation_runner_stores_error_text():
    from src.eval.keyfree.runner import _PairRunner

    class _Raises:
        name = "raises"

        def answer(self, _case):
            raise _NoArgsError()

    answer = _PairRunner(_Raises(), None)._answer(_case("a"))
    assert answer.error == error_text(_NoArgsError())


# ---------------------------------------------------------------------------- runner


def test_run_many_stops_on_a_system_halt():
    class _Halts:
        name = "halts"

        def answer(self, _case):
            raise CircuitBreakerOpen("open")

    with pytest.raises(CircuitBreakerOpen):
        EvalRunner(RunnerConfig(max_workers=1)).run_many([_case("a")], [_Halts()])


def test_run_many_on_result_fires_before_the_next_case_of_a_group():
    seen: list[tuple[str, int]] = []
    results: list[CaseResult] = []

    class _Watch(_Right):
        def answer(self, case):
            seen.append((case.id, len(results)))
            return super().answer(case)

    cases = [EvalCase(id=f"c{i}", question="q", kind="lookup", expected_value=1.0,
                      ticker="AAA", fy=2024) for i in range(3)]
    EvalRunner(RunnerConfig(max_workers=1, cache_groups="filing")).run(
        cases, _Watch(), on_result=results.append)
    assert seen == [("c0", 0), ("c1", 1), ("c2", 2)]


def test_each_segment_records_its_service_tier(monkeypatch):
    cases = [_case("a"), _case("b")]
    store = _Memory()
    monkeypatch.setenv(SERVICE_TIER_ENV, "flex")

    class _CrashAtB(_Right):
        def answer(self, case):
            if case.id == "b":
                raise KeyboardInterrupt  # a process death, not graded
            return super().answer(case)

    with pytest.raises(KeyboardInterrupt):
        run_checkpointed(cases, [_CrashAtB()], store=store, identities=[_identity(cases)],
                         run_ids=["run-1"], config=RunnerConfig(max_workers=1))
    monkeypatch.setenv(SERVICE_TIER_ENV, "default")
    (report,) = run_checkpointed(cases, [_Right()], store=store,
                                 identities=[_identity(cases)], resume="run-1",
                                 config=RunnerConfig(max_workers=1))
    assert [s["service_tier"] for s in report.config["segments"]] == ["flex", "default"]
    assert report.config["service_tier"] == "default"
    # the tier is not part of the identity: the resume above was accepted
    assert "service_tier" not in report.config["resume_identity"]["settings"]


def test_null_store_runs_unstored_and_keeps_a_halt():
    class _HaltAtB(_Right):
        def answer(self, case):
            if case.id == "b":
                raise CircuitBreakerOpen("open")
            return super().answer(case)

    cases = [_case("a"), _case("b")]
    (report,) = run_checkpointed(cases, [_HaltAtB()], store=NullRunStore(),
                                 identities=[_identity(cases)],
                                 config=RunnerConfig(max_workers=1))
    assert report.status == "ABORTED"
    assert [r.case.id for r in report.results] == ["a"]
    with pytest.raises(ResumeRefused):
        run_checkpointed(cases, [_Right()], store=NullRunStore(),
                         identities=[_identity(cases)], resume=report.run_id)


# ---------------------------------------------------------------------------- run_suite


@pytest.fixture(scope="module")
def run_suite():
    spec = importlib.util.spec_from_file_location("run_suite_erc_wiring", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_erc_wiring"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def suite_file(tmp_path):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({"name": "unit", "version": "v1", "cases": [
        {"id": "c1", "question": "What was AAA revenue in 2024?", "expected_value": 1.0,
         "ticker": "AAA", "fy": 2024}]}), encoding="utf-8")
    return path


def _recording(run_suite, monkeypatch, *, raises: BaseException | None = None):
    """Replaces run_checkpointed and the store; returns the recorded calls and the store."""
    calls: list[dict[str, Any]] = []
    store = _Memory()

    def fake_run(cases, systems, **kwargs):
        calls.append({"cases": cases, "systems": systems, **kwargs})
        if raises is not None:
            raise raises
        return [RunReport(run_id=kwargs.get("resume") or "eval-new", suite="unit",
                          system=s.name, status="OK", n_planned=len(cases)) for s in systems]

    monkeypatch.setattr(run_suite, "run_checkpointed", fake_run)
    monkeypatch.setattr(run_suite, "_store", lambda _args: store)
    return calls, store


def test_run_suite_passes_resume_and_retry_errors(run_suite, monkeypatch, suite_file,
                                                  tmp_path):
    calls, store = _recording(run_suite, monkeypatch)
    out = tmp_path / "report.json"
    rc = run_suite.main(["--suite-path", str(suite_file), "--system", "closed_book",
                         "--llm-profile", "deepseek", "--resume", "eval-0123456789ab",
                         "--retry-errors", "transport", "--max-cost-usd", "2",
                         "--workers", "3", "--out", str(out)])
    assert rc == 0
    (call,) = calls
    assert call["resume"] == "eval-0123456789ab"
    assert call["retry_errors"] == "transport"
    assert call["store"] is store
    assert call["config"].max_cost_usd == 2.0 and call["config"].max_workers == 3
    (identity,) = call["identities"]
    assert identity["system"] == "closed_book"
    assert identity["llm_profile"] == "deepseek"
    settings = identity["settings"]
    assert set(settings) == {"cache_groups", "judge_model", "judge_version", "sec_as_of",
                             "sec_universes_sha256", "max_input_tokens", "max_steps",
                             "max_tool_calls", "max_case_cost_usd", "with_system"}
    assert "max_cost_usd" not in settings and "max_workers" not in settings
    # the run is finalised again with run_suite's extras, and written out
    assert store.finished == ["eval-0123456789ab"]
    assert store.rows["eval-0123456789ab"]["config"]["llm_profile"] == "deepseek"
    assert json.loads(out.read_text(encoding="utf-8"))["run_id"] == "eval-0123456789ab"


def test_run_suite_new_run_has_no_resume_and_partial_tier_up_front(run_suite, monkeypatch,
                                                                   suite_file):
    calls, _store = _recording(run_suite, monkeypatch)
    rc = run_suite.main(["--suite-path", str(suite_file), "--system", "closed_book",
                         "--llm-profile", "deepseek", "--limit", "1"])
    assert rc == 0
    (call,) = calls
    assert call["resume"] is None and call["retry_errors"] is None
    assert call["config"].tier == "partial"  # on the RUNNING row, not only at the end


def test_run_suite_reports_a_refused_resume(run_suite, monkeypatch, suite_file, capsys):
    _recording(run_suite, monkeypatch, raises=ResumeRefused("git_sha differs"))
    rc = run_suite.main(["--suite-path", str(suite_file), "--system", "closed_book",
                         "--llm-profile", "deepseek", "--resume", "eval-0123456789ab"])
    assert rc == 2
    assert "git_sha differs" in capsys.readouterr().out


def test_run_suite_no_store_uses_the_null_store(run_suite):
    args = run_suite.argparse.Namespace(no_store=True)
    assert isinstance(run_suite._store(args), NullRunStore)


# ---------------------------------------------------------------------------- Postgres reads


class _ReadCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        self.description = [(c,) for c in self.conn.columns]

    def fetchone(self):
        return self.conn.rows[0] if self.conn.rows else None

    def fetchall(self):
        return list(self.conn.rows)


class _ReadConn:
    def __init__(self, columns, rows):
        self.columns, self.rows = columns, rows
        self.executed: list[tuple[str, Any]] = []
        self.closed = False

    def cursor(self):
        return _ReadCursor(self)

    def close(self):
        self.closed = True


class _ReadStore(PostgresEvalStore):
    def __init__(self, conn):
        super().__init__()
        self._conn = conn

    def _connect(self):
        return self._conn


def test_load_run_row_returns_a_dict_with_iso_times():
    started = datetime.datetime(2026, 11, 2, 8, 0, tzinfo=datetime.UTC)
    columns = ["run_id", "suite", "suite_version", "system", "tier", "git_sha", "status",
               "started_at", "finished_at", "config"]
    conn = _ReadConn(columns, [("r1", "s", "v1", "sys", None, "abc", "RUNNING", started,
                                None, '{"segments": []}')])
    row = _ReadStore(conn).load_run_row("r1")
    assert row["run_id"] == "r1" and row["status"] == "RUNNING"
    assert row["started_at"] == "2026-11-02T08:00:00+00:00"
    assert row["config"] == {"segments": []}
    assert conn.executed[0][1] == ("r1",) and conn.closed


def test_load_run_row_is_none_for_an_unknown_run():
    assert _ReadStore(_ReadConn(["run_id"], [])).load_run_row("nope") is None


def test_load_case_payloads_decodes_each_payload():
    case = _case("a")
    answer = SystemAnswer(numeric_values=[1.0], fact_ids=[1])
    payload = checkpoint_payload(CaseResult.build(case, answer, grade_case(case, answer)))
    conn = _ReadConn(["result_payload"], [(json.dumps(payload),), (dict(payload),)])
    got = _ReadStore(conn).load_case_payloads("r1")
    assert got == [json.loads(json.dumps(payload)), payload]
    assert "result_payload IS NOT NULL" in conn.executed[0][0]
