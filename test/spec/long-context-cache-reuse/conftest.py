"""Fakes and builders for the long-context-cache-reuse spec tests.

Spec: docs/specs/long-context-cache-reuse.md. The fakes sit only at the I/O boundaries:

- ``ArmAdapter``: a fake provider adapter behind a real ``LLMGateway`` (the LLM provider).
  It records every request's params and answers with a fixed JSON answer object;
- ``FakeResolver``: a ``FilingResolver`` (the fact-store database) mapping
  ``(ticker, fiscal year)`` to a store key; ``fail`` names tickers it raises on;
- ``FilingMemoryStore``: an in-memory ``FilingStore`` (local FS / S3);
- ``RunMemoryStore``: an in-memory ``RunStore`` (the eval database), for ``run_checkpointed``;
- ``PlanningSystem`` / ``PlainSystem``: ``SystemUnderTest`` fakes (the system boundary of the
  runner) that log every ``plan_cases`` and ``answer`` call into a shared event list.

The arm, ``filing_text``, the gateway, the prompt registry, the runner, the graders and the
checkpoint logic are real. Patterns are copied from the llm-anthropic-and-caching,
long-context-arms and eval-run-checkpoint spec conftests (spec tests cannot import another
spec's conftest). Everything is synthetic; no network, no data/ and no real keys.
"""

from __future__ import annotations

import copy
import json
import threading
import types
from collections.abc import Iterator, Sequence
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.base import RunStore
from src.eval.core.checkpoint.run import checkpoint_payload, resume_identity
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.system import SystemAnswer
from src.eval.systems.base import FilingResolver
from src.eval.systems.long_context import LongContextSystem
from src.ingest.sec_edgar.storage import FilingStore
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")

# Committed configs/llm/llm.yaml models, one per provider the spec names.
ANTHROPIC_MODEL = "claude-opus-5-5"
OPENAI_MODEL = "gpt-5.6-sol"
DEEPSEEK_MODEL = "deepseek-flash"

XHTML_NS = "http://www.w3.org/1999/xhtml"
IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI_NS = "http://www.xbrl.org/2003/instance"

REPLY = ('{"value": 1, "unit": "USD", "rows": [], "refused": false, "hedged": false, '
         '"cited_chunks": [], "text": "One dollar."}')
GIT_SHA = "abc1234"


class Crash(BaseException):
    """Stands in for a process death: the runner does not catch a BaseException."""


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off: no trace these tests open is persisted."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


# ---------------------------------------------------------------------------- the arm


class ArmAdapter(ProviderAdapter):
    """Records each request's params and answers with a fixed reply."""

    def __init__(self, reply: str = REPLY) -> None:
        self.provider = "fake"
        self.reply = reply
        self.params: list[dict] = []
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.params.append(copy.deepcopy(params))
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=self.reply, finish_reason="stop",
                              prompt_tokens=1000, completion_tokens=50)

    @property
    def calls(self) -> int:
        return len(self.params)

    def messages(self, call: int = 0) -> list[dict]:
        return list(self.params[call]["messages"])

    def calls_for(self, question: str) -> list[list[dict]]:
        """The message lists of the calls whose question message holds *question*."""
        return [list(p["messages"]) for p in self.params
                if question in str(p["messages"][-1].get("content"))]

    def marked(self, question: str) -> bool:
        """Whether the one call on *question* marks its filing message as a cache point."""
        calls = self.calls_for(question)
        assert len(calls) == 1, f"expected one call for {question!r}, got {len(calls)}"
        return calls[0][1].get("cache_point") is True


class FakeResolver(FilingResolver):
    """Maps (ticker, fiscal year) to a key; raises for a ticker in ``fail``."""

    def __init__(self, keys: dict[tuple[str, int], str], fail: Sequence[str] = ()) -> None:
        self.keys = dict(keys)
        self.fail = set(fail)
        self.calls: list[tuple[str, int]] = []

    def canonical_key(self, ticker: str, fiscal_year: int) -> str | None:
        self.calls.append((ticker, fiscal_year))
        if ticker in self.fail:
            raise ConnectionError(f"fact store unavailable for {ticker}")
        return self.keys.get((ticker, fiscal_year))


class FilingMemoryStore(FilingStore):
    """An in-memory filing store; a missing key raises FileNotFoundError like the local one."""

    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = {k: v.encode("utf-8") for k, v in objects.items()}

    def write_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = bytes(data)

    def read_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(key).decode(encoding)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        return iter(())

    def localize_dir(self, prefix: str):
        raise RuntimeError("the in-memory store has no local directories")


def xhtml(body: str) -> str:
    """A canonical-style XHTML document around *body*."""
    return (
        f'<html xmlns="{XHTML_NS}" xmlns:ix="{IX_NS}" xmlns:xbrli="{XBRLI_NS}">'
        f"<head></head><body>{body}</body></html>"
    )


def filing_key(ticker: str, fy: int) -> str:
    return f"{ticker}/{fy}/10-K/canonical.htm"


def filing_html(ticker: str, fy: int) -> str:
    return xhtml(f"<p>Zorblax {ticker} annual report {fy}</p><p>Zorblax revenue | 391,035</p>")


def question_of(case_id: str) -> str:
    """A question carrying the case id, so each call can be told apart."""
    return f"What was Qvx revenue in case Kq{case_id}Kq?"


def make_case(case_id: str, ticker: str | None = "AAA", fy: int | None = 2024) -> EvalCase:
    """A scalar lookup case expecting 1.0 (the fake reply's value)."""
    return EvalCase(id=case_id, question=question_of(case_id), kind="lookup",
                    expected_value=1.0, ticker=ticker, fy=fy)


def make_arm(
    model: str,
    filers: Sequence[tuple[str, int]],
    *,
    keys: dict[tuple[str, int], str] | None = None,
    fail: Sequence[str] = (),
) -> tuple[LongContextSystem, ArmAdapter]:
    """The real arm on *model* over the given filers' filings, with a recording adapter.

    Each ``(ticker, fy)`` resolves to :func:`filing_key` unless *keys* maps it elsewhere;
    the store holds a filing for every key.
    """
    adapter = ArmAdapter()
    gateway = LLMGateway(
        stage="long_context",
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=RunBudget(),
        max_attempts=1,
        sleep_fn=lambda _s: None,
        adapters={p: adapter for p in PROVIDERS},
    )
    mapping = {f: filing_key(*f) for f in filers}
    mapping.update(keys or {})
    objects = {key: filing_html(*f) for f, key in mapping.items()}
    arm = LongContextSystem(
        model=model,
        gateway=gateway,
        resolver=FakeResolver(mapping, fail=fail),
        store=FilingMemoryStore(objects),
    )
    return arm, adapter


# ---------------------------------------------------------------------------- runner fakes


class EventLog:
    """A thread-safe list of ``(kind, system, payload)`` events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, Any]] = []
        self._lock = threading.Lock()

    def add(self, kind: str, system: str, payload: Any) -> None:
        with self._lock:
            self.events.append((kind, system, payload))

    def plans(self, system: str) -> list[list[str]]:
        return [p for k, s, p in self.events if k == "plan" and s == system]

    def answered(self, system: str) -> list[str]:
        return [p for k, s, p in self.events if k == "answer" and s == system]

    def first_answer_index(self) -> int:
        kinds = [k for k, _, _ in self.events]
        return kinds.index("answer") if "answer" in kinds else len(kinds)

    def plan_indexes(self) -> list[int]:
        return [i for i, (k, _, _) in enumerate(self.events) if k == "plan"]


def _answer() -> SystemAnswer:
    return SystemAnswer(text="synthetic", numeric_values=[1.0], latency_ms=10.0)


class PlainSystem:
    """A system without ``plan_cases``.

    ``crash_at`` is sticky: from that case on every answer raises ``Crash``, as after a
    process death, so what a crashed run stored does not depend on thread timing.
    """

    def __init__(self, name: str, log: EventLog, *, crash_at: str | None = None) -> None:
        self.name = name
        self.log = log
        self.crash_at = crash_at
        self.crashed = False
        self._crash_lock = threading.Lock()

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.log.add("answer", self.name, case.id)
        with self._crash_lock:
            if case.id == self.crash_at:
                self.crashed = True
            crashed = self.crashed
        if crashed:
            raise Crash(case.id)
        return _answer()


class PlanningSystem(PlainSystem):
    """A system with ``plan_cases``; ``plan_error`` is raised from it after logging."""

    def __init__(self, name: str, log: EventLog, *, plan_error: BaseException | None = None,
                 crash_at: str | None = None) -> None:
        super().__init__(name, log, crash_at=crash_at)
        self.plan_error = plan_error

    def plan_cases(self, cases: Sequence[EvalCase]) -> None:
        self.log.add("plan", self.name, [c.id for c in cases])
        if self.plan_error is not None:
            raise self.plan_error


class RunMemoryStore(RunStore):
    """An in-memory RunStore, storing each case as a JSONB column would."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.cases: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def _row(self, report: RunReport) -> dict[str, Any]:
        return {"run_id": report.run_id, "system": report.system, "status": report.status,
                "started_at": report.started_at, "config": copy.deepcopy(report.config)}

    def start_run(self, report: RunReport) -> None:
        with self._lock:
            self.rows[report.run_id] = self._row(report)
            self.cases.setdefault(report.run_id, {})

    def write_case(self, run_id: str, result: CaseResult) -> bool:
        with self._lock:
            payload = json.loads(json.dumps(checkpoint_payload(result)))
            self.cases.setdefault(run_id, {})[result.case.id] = payload
            return True

    def finish_run(self, report: RunReport) -> bool:
        with self._lock:
            self.rows[report.run_id] = self._row(report)
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

    def stored_ids(self, run_id: str) -> set[str]:
        with self._lock:
            return set(self.cases.get(run_id, {}))


def identity_for(cases: Sequence[EvalCase], system: str) -> dict[str, Any]:
    """The identity a run of *system* over *cases* is checkpointed under."""
    return resume_identity(list(cases), system=system, model="model-1", llm_profile="deepseek",
                           git_sha=GIT_SHA, settings={"cache_groups": None})


@pytest.fixture(scope="session")
def kit() -> types.SimpleNamespace:
    """The builders and fakes above, as one namespace (session-scoped for hypothesis)."""
    return types.SimpleNamespace(
        ANTHROPIC_MODEL=ANTHROPIC_MODEL, OPENAI_MODEL=OPENAI_MODEL,
        DEEPSEEK_MODEL=DEEPSEEK_MODEL, Crash=Crash, EventLog=EventLog,
        PlainSystem=PlainSystem, PlanningSystem=PlanningSystem, RunMemoryStore=RunMemoryStore,
        identity_for=identity_for, make_case=make_case, make_arm=make_arm,
        question_of=question_of, filing_key=filing_key,
    )
