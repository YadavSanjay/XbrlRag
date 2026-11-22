"""Per-query trace context and structured event emission for the XBRL flow.

docs/design/XbrlRag_Observability_Tracing.md.

A single :class:`TraceContext` is created per user query (:func:`trace_query`)
and propagated implicitly via ``contextvars``, so any code deep in the call
stack can:

* stamp its log lines with the query's correlation id (via ``LogContextFilter``
  in :mod:`src.logging_config`),
* time a pipeline stage (:func:`traced_stage`),
* emit a structured, machine-parseable event (:func:`log_event`),

without threading a context object through every function signature.

Everything degrades to a no-op when no trace is active (e.g. unit tests that
exercise a single stage in isolation), so adding instrumentation never makes a
callable *require* a trace.
"""
from __future__ import annotations

import datetime
import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.logging_config import get_logger
from src.observability.service_tiers import TierCounts, count_call

logger = get_logger(__name__)

# Dedicated event stream. Named outside the ``src.*`` tree so it propagates to
# the root logger's handlers (see configs/logging.yaml) and can later be routed
# to its own sink without touching module loggers.
_events_logger = get_logger("xbrlrag.events")

# Sentinel for "no value" in log fields / context vars.
NONE_SENTINEL = "-"

_trace_var: ContextVar[TraceContext | None] = ContextVar("xbrlrag_trace", default=None)
_query_id_var: ContextVar[str] = ContextVar("xbrlrag_query_id", default=NONE_SENTINEL)
_stage_var: ContextVar[str] = ContextVar("arag_stage", default=NONE_SENTINEL)

# Process-wide audit sink (Phase 3). Default None => events live only in the
# `xbrlrag.events` log stream. A sink (e.g. PostgresAuditStore) is registered via
# `set_audit_sink()` — the XBRL pipeline does this from env config — and then
# every completed query is persisted on `trace_query` exit.
_audit_sink: AuditSink | None = None


@dataclass
class TraceContext:
    """Mutable accumulator for one query's telemetry.

    Stages and the LLM gateway record into this as they run; the running totals
    are emitted as a ``query_summary`` event when :func:`trace_query` exits.
    """

    query_id: str
    query: str = ""
    user_id: str | None = None
    started_at: float = field(default_factory=time.perf_counter)
    started_wall: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat()
    )

    llm_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cached_prompt_tokens: int = 0   # B2: prompt tokens served from provider cache
    total_cost_usd: float = 0.0
    total_retries: int = 0
    # WS-4 / H1: consecutive permanent LLM failures across this run, for the
    # gateway's circuit breaker. Reset to 0 on any successful call; incremented on
    # a permanent (retry-exhausted) failure.
    consecutive_failures: int = 0
    # Seconds this trace has waited on Flex 429s, and whether it hit the per-trace cap; a
    # capped trace halts every later LLM call (docs/specs/flex-retry-halt.md).
    flex_wait_s: float = 0.0
    flex_wait_capped: bool = False
    stage_latency_ms: dict[str, float] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)

    # Populated as the query runs / on completion (used by the audit sink).
    events: list[dict[str, Any]] = field(default_factory=list)
    total_latency_ms: float | None = None
    error: str | None = None
    # The full final answer (XbrlRagResponse.model_dump()) for this query, so the
    # Trace Explorer can render the same Answer / statements / not-answered /
    # "how it was answered" view the Ask page shows. Set by the pipeline before
    # the trace closes; persisted to audit.query_audit.answer.
    answer: dict[str, Any] | None = None
    # Requested model id -> the sorted distinct model ids the provider reported serving
    # in this trace (docs/specs/served-model-id.md).
    served_models: dict[str, list[str]] = field(default_factory=dict)
    # "<provider>/<stage>" -> requested tier -> served tier -> calls
    # (src/observability/service_tiers.py).
    service_tiers: TierCounts = field(default_factory=dict)

    def record_llm_call(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        retries: int = 0,
        cached_prompt_tokens: int = 0,
        model: str | None = None,
        served_model: str | None = None,
        provider: str | None = None,
        stage: str | None = None,
        requested_tier: str | None = None,
        served_tier: str | None = None,
    ) -> None:
        """Adds one provider call's usage to the trace.

        The call is counted in ``service_tiers`` only when ``provider`` and ``stage``
        are given (the gateway always gives them).

        Args:
            prompt_tokens: Prompt tokens billed.
            completion_tokens: Completion tokens billed.
            cost_usd: The call's cost.
            retries: Retries before the call returned.
            cached_prompt_tokens: Prompt tokens served from the provider cache.
            model: The requested model id.
            served_model: The model id the provider reported serving.
            provider: The provider called.
            stage: The calling stage.
            requested_tier: The ``service_tier`` sent, or None.
            served_tier: The tier the reply reported, or None.
        """
        if provider and stage:
            count_call(self.service_tiers, provider=provider, stage=stage,
                       requested=requested_tier, served=served_tier)
        self.llm_calls += 1
        self.total_prompt_tokens += prompt_tokens or 0
        self.total_completion_tokens += completion_tokens or 0
        self.total_cached_prompt_tokens += cached_prompt_tokens or 0
        self.total_cost_usd += cost_usd or 0.0
        self.total_retries += retries or 0
        if model and served_model:
            seen = self.served_models.get(model, [])
            if served_model not in seen:
                self.served_models[model] = sorted([*seen, served_model])

    def record_stage(self, stage: str, latency_ms: float) -> None:
        # Accumulate in case a stage runs more than once within a query.
        self.stage_latency_ms[stage] = self.stage_latency_ms.get(stage, 0.0) + latency_ms

    def annotate(self, **kwargs: Any) -> None:
        self.annotations.update(kwargs)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000.0


# ----------------------------------------------------------------------
# Accessors (used by the logging filter and the LLM gateway)
# ----------------------------------------------------------------------

def current_trace() -> TraceContext | None:
    return _trace_var.get()


def current_query_id() -> str:
    return _query_id_var.get()


def current_stage() -> str:
    return _stage_var.get()


def log_event(kind: str, **fields: Any) -> dict[str, Any]:
    """Emit one structured event as a JSON line on the ``xbrlrag.events`` stream.

    Always includes ``kind``, the active ``query_id`` and ``stage``. Telemetry
    failures are swallowed — emitting an event must never break the request path.
    """
    tr = current_trace()
    event: dict[str, Any] = {
        "kind": kind,
        "query_id": tr.query_id if tr is not None else current_query_id(),
        "stage": current_stage(),
    }
    event.update(fields)
    if tr is not None:
        tr.events.append(event)
    try:
        _events_logger.info(json.dumps(event, default=str))
    except Exception:  # pragma: no cover - defensive
        _events_logger.exception("failed to emit event kind=%s", kind)
    return event


# ----------------------------------------------------------------------
# Audit sink registration (Phase 3 persistence)
# ----------------------------------------------------------------------

class AuditSink:
    """Interface for a durable sink that persists a completed query's trace.

    Implementations must never raise into the request path — `trace_query`
    additionally guards the call, but a sink should still fail soft.
    """

    def persist_query(self, trace: TraceContext) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def set_audit_sink(sink: AuditSink | None) -> None:
    """Register (or clear, with None) the process-wide audit sink."""
    global _audit_sink
    _audit_sink = sink


def get_audit_sink() -> AuditSink | None:
    return _audit_sink


def _persist_trace(trace: TraceContext) -> None:
    sink = _audit_sink
    if sink is None:
        return
    try:
        sink.persist_query(trace)
    except Exception:  # never let persistence break the request path
        logger.exception("audit persist failed for query_id=%s", trace.query_id)


# ----------------------------------------------------------------------
# Context managers
# ----------------------------------------------------------------------

@contextmanager
def trace_query(
    query: str,
    user_id: str | None = None,
    query_id: str | None = None,
) -> Iterator[TraceContext]:
    """Open a trace for one query; emit ``query_start`` / ``query_summary``.

    Restores any previously-active trace on exit, so nesting is safe.
    """
    tc = TraceContext(query_id=query_id or uuid.uuid4().hex[:16], query=query, user_id=user_id)
    trace_token: Token = _trace_var.set(tc)
    qid_token: Token = _query_id_var.set(tc.query_id)
    log_event("query_start", query=query[:500], user_id=user_id)

    try:
        yield tc
    except Exception as exc:  # record then re-raise — the caller still sees it
        tc.error = repr(exc)
        raise
    finally:
        tc.total_latency_ms = round(tc.elapsed_ms(), 1)
        log_event(
            "query_summary",
            total_latency_ms=tc.total_latency_ms,
            stage_latency_ms={k: round(v, 1) for k, v in tc.stage_latency_ms.items()},
            llm_calls=tc.llm_calls,
            total_prompt_tokens=tc.total_prompt_tokens,
            total_completion_tokens=tc.total_completion_tokens,
            total_cached_prompt_tokens=tc.total_cached_prompt_tokens,
            total_cost_usd=round(tc.total_cost_usd, 6),
            total_retries=tc.total_retries,
            error=tc.error,
            **tc.annotations,
        )
        _persist_trace(tc)
        _query_id_var.reset(qid_token)
        _trace_var.reset(trace_token)


@contextmanager
def traced_stage(stage: str) -> Iterator[None]:
    """Time a pipeline stage and record its latency on the active trace.

    Also sets the ``stage`` context var so log lines emitted inside the stage
    are tagged with it. No-op recording when no trace is active.
    """
    stage_token: Token = _stage_var.set(stage)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        tr = current_trace()
        if tr is not None:
            tr.record_stage(stage, elapsed_ms)
        _stage_var.reset(stage_token)
