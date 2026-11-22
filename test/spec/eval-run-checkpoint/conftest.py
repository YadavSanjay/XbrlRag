"""Fakes and builders for the eval-run-checkpoint spec tests.

Spec: docs/specs/eval-run-checkpoint.md. Fakes sit only at the I/O boundaries:

- ``FakeSystem``: a ``SystemUnderTest`` (the LLM boundary). Deterministic: a case is answered
  right unless its id is in ``wrong``; ``errors`` maps a case id to an error text the arm
  returns; ``crash_at`` raises ``Crash`` (a ``BaseException``, as a process death would end the
  run) and ``halt_at`` raises ``CircuitBreakerOpen``. Both are sticky through a shared
  ``Power``: after them every later answer fails the same way, so what is stored never depends
  on thread timing. Every answer has a fixed latency and cost,
  so metrics do not depend on timing. ``on_answer`` lets a test look at the store at the moment
  a case is answered;
- ``MemoryStore``: an in-memory ``RunStore`` (the database boundary). It stores each case as
  ``json.loads(json.dumps(checkpoint_payload(result)))``, as a JSONB column would;
- ``fake_pg``: a fake ``psycopg2`` module whose connections record committed transactions;
- ``JudgeAdapter``: a provider adapter behind a real ``LLMGateway`` for the claim judge (the
  LLM provider). It answers from the judge's payload, or with an unparseable reply for a
  question listed in ``malformed``, which the judge turns into a ``judge_error``. No request
  leaves the process.

The runner, graders, judge, reports, metrics, the checkpoint logic and ``PostgresEvalStore`` are
real. The process-wide audit sink is off for the session (the database boundary). Everything is
synthetic.
"""

from __future__ import annotations

import contextlib
import copy
import json
import re
import sys
import threading
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.base import RunStore
from src.eval.core.checkpoint.run import checkpoint_payload, resume_identity
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.judge import ClaimJudge
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.system import SystemAnswer
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import CircuitBreakerOpen, RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
RUN_SUITE = REPO_ROOT / "scripts" / "eval" / "run_suite.py"

LATENCY_MS = 10.0
GIT_SHA = "abc1234"

# The judge is Anthropic and the generator profile OpenAI: a cross-family pair.
JUDGE_MODEL = "claude-opus-5-5"
GENERATOR_MODEL = "gpt-5.6-sol"
GENERATOR_PROFILE = "openai"
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
PROMPT_TOKENS = 1000  # the fake provider's usage per judge call
COMPLETION_TOKENS = 200


class Crash(BaseException):
    """Stands in for a process death: the runner does not catch a BaseException."""


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the database boundary)."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


# ---------------------------------------------------------------------------- cases


def make_case(case_id: str, ticker: str | None = None, fy: int | None = None) -> EvalCase:
    """A scalar lookup expecting 1.0, grounded on fact 1."""
    return EvalCase(id=case_id, question=f"question {case_id}", kind="lookup",
                    expected_value=1.0, expected_fact_ids=[1], ticker=ticker, fy=fy)


def make_text_case(case_id: str) -> EvalCase:
    """A text-graded case, which the claim judge scores."""
    return EvalCase(id=case_id, question=f"question {case_id}", kind="narrative",
                    expected_text="Revenue grew.", key_points=["revenue grew"])


# ---------------------------------------------------------------------------- judge


def judge_call_cost() -> float:
    """One fake judge call's USD cost, from configs/llm/pricing.yaml (USD per 1,000 tokens)."""
    with open(REPO_ROOT / "configs" / "llm" / "pricing.yaml", encoding="utf-8") as f:
        rates = yaml.safe_load(f)[JUDGE_MODEL]
    return PROMPT_TOKENS / 1000 * rates["input"] + COMPLETION_TOKENS / 1000 * rates["output"]


def _payload_of(params: dict) -> dict:
    parts = []
    for m in params["messages"]:
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(p.get("text", "")) if isinstance(p, dict) else str(p)
                                for p in content)
        parts.append(str(content))
    text = "\n".join(parts)
    obj, _ = json.JSONDecoder().raw_decode(text, text.index("{"))
    return obj


class JudgeAdapter(ProviderAdapter):
    """Every claim correct and supported, every key point covered; or, for a question in
    *malformed*, a reply with no JSON object (a judge_error)."""

    def __init__(self, malformed: set[str] | None = None) -> None:
        self.provider = "fake"
        self.malformed = set(malformed or ())
        self.calls = 0
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        payload = _payload_of(params)
        with self._lock:
            self.calls += 1
        if any(q in json.dumps(payload) for q in self.malformed):
            return "no verdict here"
        return json.dumps({
            "claims": [{"id": c["id"], "correct": True, "supported": True}
                       for c in payload["claims"]],
            "key_points": [{"index": p["index"], "covered": True}
                           for p in payload["key_points"]]})

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=raw, finish_reason="stop", prompt_tokens=PROMPT_TOKENS,
                              completion_tokens=COMPLETION_TOKENS, reasoning_tokens=0,
                              cached_prompt_tokens=0)


def make_judge(malformed: set[str] | None = None) -> tuple[ClaimJudge, JudgeAdapter]:
    """A real ClaimJudge on a real gateway whose only provider is the fake adapter."""
    adapter = JudgeAdapter(malformed)
    gateway = LLMGateway(stage="eval_judge", cost_tracker=CostTracker(), rate_limiter=None,
                         run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                         adapters={p: adapter for p in PROVIDERS})
    return ClaimJudge(model=JUDGE_MODEL, generator_model=GENERATOR_MODEL,
                      gateway=gateway), adapter


def answer_for(correct: bool, cost: float = 0.0) -> SystemAnswer:
    return SystemAnswer(text="synthetic", numeric_values=[1.0 if correct else 5.0],
                        fact_ids=[1], latency_ms=LATENCY_MS, cost_usd=cost)


def graded(case: EvalCase, answer: SystemAnswer) -> CaseResult:
    """The real grader's result for *answer*."""
    return CaseResult.build(case, answer, grade_case(case, answer))


def identity_for(cases: list[EvalCase], system: str, **overrides: Any) -> dict[str, Any]:
    """The identity a run of *system* over *cases* is checkpointed under."""
    kwargs: dict[str, Any] = {"system": system, "model": "model-1", "llm_profile": "deepseek",
                              "git_sha": GIT_SHA, "settings": {"cache_groups": None}}
    kwargs.update(overrides)
    return resume_identity(cases, **kwargs)


# ---------------------------------------------------------------------------- system


class Power:
    """Shared by the systems of one process. Once cut (a crash) or tripped (a halt), every
    later answer of every system sharing it fails the same way, as after a real power cut or
    with an open breaker, so what is stored does not depend on thread timing."""

    def __init__(self, cut_at_call: int | None = None) -> None:
        self.cut_at_call = cut_at_call
        self.calls = 0
        self.off = False
        self.tripped = False
        self.lock = threading.Lock()


class FakeSystem:
    """A deterministic arm; records the case ids it was asked, in order."""

    def __init__(self, name: str = "fake_system", *, wrong: set[str] | None = None,
                 errors: dict[str, str] | None = None, crash_at: str | None = None,
                 halt_at: str | None = None, cost: float = 0.0,
                 served: dict[str, list[str]] | None = None,
                 on_answer: Callable[[str, EvalCase], None] | None = None,
                 power: Power | None = None) -> None:
        self.name = name
        self.wrong = set(wrong or ())
        self.errors = dict(errors or {})
        self.crash_at = crash_at
        self.halt_at = halt_at
        self.cost = cost
        self.served = served
        self.on_answer = on_answer
        self.power = power if power is not None else Power()
        self.answered: list[str] = []
        self._lock = threading.Lock()

    def answer(self, case: EvalCase) -> SystemAnswer:
        with self._lock:
            self.answered.append(case.id)
        if self.on_answer is not None:
            self.on_answer(self.name, case)
        with self.power.lock:
            self.power.calls += 1
            if case.id == self.crash_at or self.power.calls == self.power.cut_at_call:
                self.power.off = True
            if case.id == self.halt_at:
                self.power.tripped = True
            off, tripped = self.power.off, self.power.tripped
        if off:
            raise Crash(case.id)
        if tripped:
            raise CircuitBreakerOpen("3 consecutive LLM failures >= limit 3")
        if case.id in self.errors:
            return SystemAnswer(error=self.errors[case.id], latency_ms=LATENCY_MS,
                                cost_usd=self.cost)
        ans = answer_for(case.id not in self.wrong, self.cost)
        if self.served is not None:
            ans.served_models = copy.deepcopy(self.served)
        return ans


# ---------------------------------------------------------------------------- store


class MemoryStore(RunStore):
    """An in-memory RunStore. ``fail_writes`` names case ids whose write fails."""

    def __init__(self, fail_writes: set[str] | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.cases: dict[str, dict[str, dict[str, Any]]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_writes = set(fail_writes or ())
        self.fail_start = False
        self._lock = threading.Lock()

    def start_run(self, report: RunReport) -> None:
        with self._lock:
            self.calls.append(("start_run", report.run_id))
            if self.fail_start:
                raise ConnectionError("store unavailable")
            self.rows[report.run_id] = {
                "run_id": report.run_id, "system": report.system, "status": report.status,
                "started_at": report.started_at, "config": copy.deepcopy(report.config)}
            self.cases.setdefault(report.run_id, {})

    def write_case(self, run_id: str, result: CaseResult) -> bool:
        with self._lock:
            self.calls.append(("write_case", run_id))
            if result.case.id in self.fail_writes:
                return False
            payload = json.loads(json.dumps(checkpoint_payload(result)))
            self.cases.setdefault(run_id, {})[result.case.id] = payload
            return True

    def finish_run(self, report: RunReport) -> bool:
        with self._lock:
            self.calls.append(("finish_run", report.run_id))
            self.rows[report.run_id] = {
                "run_id": report.run_id, "system": report.system, "status": report.status,
                "started_at": report.started_at, "config": copy.deepcopy(report.config)}
            stored = self.cases.setdefault(report.run_id, {})
            for result in report.results:
                stored[result.case.id] = json.loads(json.dumps(checkpoint_payload(result)))
            return True

    def load_run_row(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.rows.get(run_id)
            return copy.deepcopy(row) if row is not None else None

    def load_case_payloads(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(p) for p in self.cases.get(run_id, {}).values()]

    # -- test helpers
    def stored_ids(self, run_id: str) -> set[str]:
        with self._lock:
            return set(self.cases.get(run_id, {}))

    def count(self, method: str) -> int:
        with self._lock:
            return sum(1 for m, _ in self.calls if m == method)


# ---------------------------------------------------------------------------- psycopg2

_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


class PgRecorder:
    """Committed transactions, each a list of ``(sql, params)``."""

    def __init__(self) -> None:
        self.transactions: list[list[tuple[str, dict[str, Any]]]] = []
        self.connects = 0
        self.fail_connect = False

    @staticmethod
    def inserts(txn: list[tuple[str, dict[str, Any]]], table: str) -> list[dict[str, Any]]:
        pat = re.compile(rf"insert\s+into\s+eval\.{table}\s*\(", re.IGNORECASE)
        return [p for s, p in txn if pat.search(s)]


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if isinstance(params, dict):
            for name in _PLACEHOLDER.findall(sql):
                if name not in params:
                    raise KeyError(name)  # what psycopg2 does for a missing placeholder
        self.conn.pending.append((sql, dict(params) if isinstance(params, dict) else {}))

    def executemany(self, sql: str, rows: Any) -> None:
        for row in rows:
            self.execute(sql, row)


class _Conn:
    def __init__(self, rec: PgRecorder) -> None:
        self.rec = rec
        self.pending: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, exc_type: Any, *exc: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    def commit(self) -> None:
        if self.pending:
            self.rec.transactions.append(self.pending)
        self.pending = []

    def rollback(self) -> None:
        self.pending = []

    def cursor(self, *args: Any, **kwargs: Any) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        self.pending = []  # uncommitted work is lost, as on a real close


@contextlib.contextmanager
def _fake_pg() -> Iterator[PgRecorder]:
    rec = PgRecorder()

    def connect(*_args: Any, **_kwargs: Any) -> _Conn:
        rec.connects += 1
        if rec.fail_connect:
            raise ConnectionError("could not connect")
        return _Conn(rec)

    module = types.ModuleType("psycopg2")
    module.connect = connect  # type: ignore[attr-defined]  # the driver's entry point
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "psycopg2", module)
        yield rec


@pytest.fixture(scope="session")
def fake_pg():
    """Context manager factory: ``with fake_pg() as rec:`` installs a fake psycopg2."""
    return _fake_pg


@pytest.fixture(scope="session")
def kit() -> types.SimpleNamespace:
    """The builders and fakes above, as one namespace."""
    return types.SimpleNamespace(
        Crash=Crash, Power=Power, FakeSystem=FakeSystem, MemoryStore=MemoryStore,
        make_case=make_case, make_text_case=make_text_case, make_judge=make_judge,
        judge_call_cost=judge_call_cost, GENERATOR_PROFILE=GENERATOR_PROFILE,
        answer_for=answer_for, graded=graded, identity_for=identity_for,
        LATENCY_MS=LATENCY_MS, GIT_SHA=GIT_SHA, RUN_SUITE=RUN_SUITE)


@pytest.fixture(scope="session")
def eval_runs_sql() -> str:
    """The committed scripts/ddl/eval/eval_runs.sql, as text."""
    return (REPO_ROOT / "scripts" / "ddl" / "eval" / "eval_runs.sql").read_text(
        encoding="utf-8")
