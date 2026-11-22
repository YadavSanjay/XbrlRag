"""Fakes and builders for the xbrlrag-run-halt spec tests (docs/specs/xbrlrag-run-halt.md).

The pipeline is the real ``XbrlRetrievalPipeline`` with its real LLM stages (decomposer,
intent router, plan generator, synthesizer, faithfulness verifier), dispatcher and plan
executor. The fakes sit only at I/O boundaries:

- ``ScriptAdapter``: a fake provider adapter behind a real ``LLMGateway`` (real
  ``CostTracker``, real trace, real ``RunBudget``), one per stage, installed as the stage's
  ``_llm_client``. Each attempt plays one script item: an exception is raised, anything else
  is the reply; when the script runs out every attempt answers ``final``;
- a look-alike of openai 2.14.0's Flex "resource unavailable" ``RateLimitError`` (copied from
  the flex-retry-halt spec conftest), and ``Sleeper``, the gateway's recording ``sleep_fn``
  (no test sleeps);
- ``FakeResolver``: the SQL boundary of ``EntityExtractor`` (``resolve_companies`` /
  ``resolve_periods``), returning the real dataclasses; ``FakeLookup``: the fact store,
  returning a real ``FactLookupResult``; ``Untouchable`` for the screen, breakdown and
  narrative backends (touching one fails the test); ``NoCache``: a disabled query cache;
- ``MemoryStore``: an in-memory ``RunStore``; ``RecordingSink``: an audit sink that keeps
  every persisted trace.

Everything is synthetic. The question is a one-company, one-year revenue lookup, so the
router's fast path plans one ``lookup`` step and the query makes four LLM calls, in order:
decomposer, router, synthesizer, verifier.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.base import RunStore
from src.eval.core.checkpoint.run import checkpoint_payload, resume_identity
from src.eval.core.report import CaseResult, RunReport
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import AuditSink, get_audit_sink, set_audit_sink
from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import IntentRouter
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.query_decomposer import QueryDecomposer
from src.retriever.xbrl.xbrl_factstore_lookup import Citation, FactLookupResult, LookupStatus

API_KEY = "sk-spec-offline"
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
CAP_ENV = "XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S"
_ENV_VARS = ("XBRLRAG_LLM_PROFILE", "XBRLRAG_OPENAI_SERVICE_TIER",
             "XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS", "XBRLRAG_FLEX_RETRY_BASE_S",
             "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S", CAP_ENV, "XBRLRAG_QUERY_CACHE",
             "XBRLRAG_RUN_MAX_COST_USD", "XBRLRAG_RUN_MAX_LLM_CALLS", "XBRLRAG_RUN_MAX_TOKENS",
             "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES")

QUESTION = "What was Apple's revenue in fiscal 2023?"
FACT_ID = 101
FACT_VALUE = 383_285_000_000.0
SUMMARY = "Apple's fiscal 2023 revenue was $383,285 million."

# The four LLM stages of one fast-path lookup, in call order.
STAGES = ("decomposer", "router", "synthesizer", "verifier")


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the database boundary)."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture(scope="session", autouse=True)
def _clean_env():
    """Clears the profile, Flex policy, cap, cache and run-budget variables for the session."""
    saved = {k: os.environ.pop(k) for k in _ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        for k in _ENV_VARS:
            os.environ.pop(k, None)
        os.environ.update(saved)


@contextlib.contextmanager
def env_vars(**values: str | None) -> Iterator[None]:
    """Sets (a str) or unsets (None) environment variables for the block."""
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def flex_cap(seconds: float) -> dict[str, str]:
    """A Flex trace cap of *seconds*; the default policy's first wait is 5 s."""
    return {CAP_ENV: repr(float(seconds))}


# ---------------------------------------------------------------------------- SDK errors


class _Headers(dict):
    """A case-insensitive header mapping, as ``httpx.Headers`` is."""

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(key.lower(), default)


class _Response:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.headers = _Headers()


class RateLimitError(Exception):
    """openai 2.14.0's ``RateLimitError`` shape: ``status_code`` 429, ``body`` the error."""

    def __init__(self, message: str, *, body: dict) -> None:
        super().__init__(message)
        self.message = message
        self.response = _Response(429)
        self.status_code = 429
        self.body = body
        self.code = body.get("code")
        self.type = body.get("type")


def flex() -> RateLimitError:
    """The Flex 429: code ``resource_unavailable``."""
    error = {"message": "Resource unavailable.", "type": "server_error", "param": None,
             "code": "resource_unavailable"}
    return RateLimitError(f"Error code: 429 - {json.dumps({'error': error})}", body=error)


class PermanentError(Exception):
    """A provider error the gateway does not retry (it advances the circuit breaker)."""


# ---------------------------------------------------------------------------- provider fakes


class ScriptAdapter(ProviderAdapter):
    """Plays one item per attempt; when the script runs out, every attempt answers *final*."""

    def __init__(self, final: str, script: list[Any] | None = None) -> None:
        self.provider = "fake"
        self.final = final
        self.script = list(script or [])
        self.invoked = 0
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {k: v for k, v in kwargs.items() if k != "extra"}

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.invoked += 1
            outcome = self.script.pop(0) if self.script else self.final
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)


class Sleeper:
    """The gateway's ``sleep_fn``: records each sleep, never sleeps."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


# ---------------------------------------------------------------------------- replies


def decomposer_reply(*queries: str) -> str:
    """The decomposer's JSON: one sub-query per *queries* item, none dependent."""
    return json.dumps({"subqueries": [{"id": f"sq{i}", "query": q, "depends_on": []}
                                      for i, q in enumerate(queries or (QUESTION,), start=1)]})


ROUTER_REPLY = json.dumps({
    "intent": "NUMERIC_LOOKUP", "company_mentions": ["Apple"],
    "period_mentions": ["fiscal 2023"], "concept_phrases": ["revenue"],
    "dimension_hint_present": False})
SYNTHESIZER_REPLY = json.dumps({
    "answer_summary": SUMMARY,
    "answer_details": [{"statement": SUMMARY, "fact_ids": [str(FACT_ID)], "chunk_ids": []}],
    "unanswered_parts": [], "metadata": {"fully_answered": True}})
VERIFIER_REPLY = json.dumps({"verdicts": [{"index": 0, "verdict": "SUPPORTED",
                                           "reason": "matches the cited fact"}]})


# ---------------------------------------------------------------------------- I/O fakes


class FakeResolver:
    """The SQL boundary of ``EntityExtractor``: Apple, fiscal 2023."""

    def resolve_companies(self, mentions: list[str]) -> list[ResolvedCompany]:
        return [ResolvedCompany(mention=m, cik="0000320193", ticker="AAPL",
                                company_name="Apple Inc.", resolution="exact_ticker",
                                similarity=1.0) for m in mentions]

    def resolve_periods(self, mentions: list[str],
                        companies: list[ResolvedCompany] | None = None) -> list[ResolvedPeriod]:
        return [ResolvedPeriod(mention=m, fiscal_year=2023) for m in mentions]


class FakeLookup:
    """The fact store: every lookup step resolves to fact 101."""

    def __init__(self) -> None:
        self.steps: list[dict] = []

    def lookup(self, step: dict) -> FactLookupResult:
        self.steps.append(step)
        return FactLookupResult(step_id=step.get("id"), status=LookupStatus.OK,
                                value=FACT_VALUE, unit="USD",
                                citation=Citation(fact_id=FACT_ID,
                                                  accession_no="0000320193-23-000106"),
                                confidence=1.0)


class Untouchable:
    """A backend the lookup question must never reach."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        raise AssertionError(f"the {self._name} backend was touched ({attr})")


class NoCache:
    """The query cache, disabled."""

    enabled = False


class RecordingSink(AuditSink):
    """Keeps every trace the pipeline persists."""

    def __init__(self) -> None:
        self.traces: list[Any] = []

    def persist_query(self, trace: Any) -> None:
        self.traces.append(trace)


# ---------------------------------------------------------------------------- the pipeline


class Kit:
    """One real pipeline with a scripted adapter and a real gateway per LLM stage."""

    def __init__(self, *, budget: RunBudget | None = None, decomposer: str | None = None,
                 scripts: dict[str, list[Any]] | None = None) -> None:
        scripts = scripts or {}
        self.budget = budget if budget is not None else RunBudget()
        self.sleeper = Sleeper()
        finals = {"decomposer": decomposer or decomposer_reply(), "router": ROUTER_REPLY,
                  "plan_generator": "{}", "synthesizer": SYNTHESIZER_REPLY,
                  "verifier": VERIFIER_REPLY}
        self.adapters = {s: ScriptAdapter(finals[s], scripts.get(s)) for s in finals}
        resolver = FakeResolver()
        self.lookup = FakeLookup()
        stages = {
            "decomposer": QueryDecomposer(api_key=API_KEY),
            "router": IntentRouter(api_key=API_KEY, entity_extractor=resolver),
            "plan_generator": PlanGenerator(api_key=API_KEY),
            "synthesizer": XbrlRagSynthesizer(api_key=API_KEY),
            "verifier": XbrlFaithfulnessVerifier(api_key=API_KEY),
        }
        for name, stage in stages.items():
            stage._llm_client = LLMGateway(
                stage=name, cost_tracker=CostTracker(), rate_limiter=None,
                run_budget=self.budget, max_attempts=3, sleep_fn=self.sleeper,
                adapters={p: self.adapters[name] for p in PROVIDERS})
        self.pipeline = XbrlRetrievalPipeline(
            entity_extractor=resolver,
            intent_router=stages["router"],
            query_decomposer=stages["decomposer"],
            plan_generator=stages["plan_generator"],
            factstore_lookup=self.lookup,
            factstore_screen=Untouchable("screen"),
            factstore_breakdown=Untouchable("breakdown"),
            narrative_retriever=Untouchable("narrative"),
            synthesizer=stages["synthesizer"],
            faithfulness_verifier=stages["verifier"],
            persist_audit=False,
            query_cache=NoCache(),
        )

    def calls(self) -> dict[str, int]:
        """Adapter calls per stage."""
        return {s: a.invoked for s, a in self.adapters.items()}


def calls_through(stage_index: int, *, at_stage: int, order: tuple[str, ...] = STAGES
                  ) -> dict[str, int]:
    """Expected adapter calls when the halt is at ``order[stage_index]``: one for each earlier
    stage, *at_stage* (0 or 1) for the halting stage, none after; the planner is never called."""
    out = {s: 0 for s in (*order, "plan_generator")}
    for i, s in enumerate(order):
        if i < stage_index:
            out[s] += 1
        elif i == stage_index:
            out[s] += at_stage
    return out


# ---------------------------------------------------------------------------- eval store


class MemoryStore(RunStore):
    """An in-memory RunStore; each case is stored as its JSON round-tripped payload."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.cases: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def start_run(self, report: RunReport) -> None:
        with self._lock:
            self.rows[report.run_id] = {
                "run_id": report.run_id, "system": report.system, "status": report.status,
                "started_at": report.started_at, "config": copy.deepcopy(report.config)}
            self.cases.setdefault(report.run_id, {})

    def write_case(self, run_id: str, result: CaseResult) -> bool:
        with self._lock:
            self.cases.setdefault(run_id, {})[result.case.id] = json.loads(
                json.dumps(checkpoint_payload(result)))
            return True

    def finish_run(self, report: RunReport) -> bool:
        with self._lock:
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

    def stored_ids(self, run_id: str) -> set[str]:
        with self._lock:
            return set(self.cases.get(run_id, {}))


def make_case(case_id: str) -> EvalCase:
    """A lookup case on the kit's question, expecting fact 101's value.

    Every case is on the same filing (AAPL, fiscal 2023), so with ``cache_groups="filing"``
    the run is one task answered in suite order, and a halt stops it at the halting case.
    """
    return EvalCase(id=case_id, question=QUESTION, kind="lookup", expected_value=FACT_VALUE,
                    expected_fact_ids=[FACT_ID], ticker="AAPL", fy=2023)


def identity_for(cases: list[EvalCase], system: str) -> dict[str, Any]:
    return resume_identity(cases, system=system, model="model-1", llm_profile="openai",
                           git_sha="abc1234", settings={"cache_groups": "filing"})


@pytest.fixture(scope="session")
def h():
    """The builders and constants above, as attributes."""
    class _H:
        pass
    for name, value in {
        "Kit": Kit, "flex": flex, "PermanentError": PermanentError, "env_vars": env_vars,
        "flex_cap": flex_cap, "calls_through": calls_through, "decomposer_reply": decomposer_reply,
        "RecordingSink": RecordingSink, "MemoryStore": MemoryStore, "make_case": make_case,
        "identity_for": identity_for, "QUESTION": QUESTION, "SUMMARY": SUMMARY,
        "STAGES": STAGES, "SYNTHESIZER_REPLY": SYNTHESIZER_REPLY,
        "VERIFIER_REPLY": VERIFIER_REPLY,
    }.items():
        setattr(_H, name, staticmethod(value) if callable(value) and not isinstance(value, type)
                else value)
    return _H


@pytest.fixture
def sink():
    """A recording audit sink for one test; the previous sink is restored after it."""
    previous = get_audit_sink()
    recording = RecordingSink()
    set_audit_sink(recording)
    try:
        yield recording
    finally:
        set_audit_sink(previous)
