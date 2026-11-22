"""WS-9 / E1 — the full plan-based XBRL pipeline as a :class:`SystemUnderTest`.

Normalises an ``XbrlRagResponse`` into a :class:`SystemAnswer`. Like every other arm,
XbrlRag is graded on the numbers its final answer *states* (its summary and the
statements the faithfulness verifier kept), so the same text gets the same verdict
whichever arm wrote it (docs/specs/grading-precision.md requirement 5). The citations
(``fact_ids``) and the refusal signals still come from the structured
``execution_result``: the prose is never grounding evidence.

The interesting part is ``query_id`` capture. ``XbrlRetrievalPipeline.generate``
opens its own trace internally and never returns the id, so this module installs a
capturing audit sink exactly as [src/api/pipelines.py](../../api/pipelines.py) does
for the UI — a thread-local that records the completed ``TraceContext``. That gives
each eval case not just its correlation id but the trace's own cost and latency
totals, and lets the Phase-2 UI link a failing case straight to
``/tech/traces/<query_id>``.

The comparison arms live beside this module: closed-book and naive RAG
(``closed_book.py``, ``naive_rag.py``) and the long-context arm (``long_context.py``).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from src.eval.core.answer_parsing import extract_stated_numbers
from src.eval.core.case import EvalCase
from src.eval.core.graders.numeric import (
    _as_fact_id,
    all_fact_ids,
    all_numeric_values,
    component_fact_ids,
    narrative_chunk_ids,
    screen_rows,
)
from src.eval.core.graders.screen import row_entity, row_value
from src.eval.core.system import (
    SystemAnswer,
    exception_text,
    served_models_of,
    service_tiers_of,
)
from src.llm.run_budget import RunHalt
from src.logging_config import get_logger
from src.observability.trace_context import AuditSink

logger = get_logger(__name__)

_thread_local = threading.local()
_sink_lock = threading.Lock()
_sink_installed = False


class _CapturingSink(AuditSink):
    """Records the completed trace per thread, then delegates to the real sink.

    Mirrors ``src.api.pipelines._CapturingSink``. The runner calls ``answer``
    concurrently and the pipeline runs each query on the calling thread, so a
    thread-local is correctly case-scoped.
    """

    def __init__(self, delegate: Any = None):
        self._delegate = delegate

    def persist_query(self, trace: Any) -> None:
        _thread_local.last_trace = trace
        if self._delegate is not None:
            try:
                self._delegate.persist_query(trace)
            except Exception:
                logger.exception("delegate audit sink failed")


def _install_sink(persist_audit: bool) -> None:
    """Install the capturing sink once, chaining any sink already registered."""
    global _sink_installed
    with _sink_lock:
        if _sink_installed:
            return
        try:
            from src.observability.trace_context import get_audit_sink, set_audit_sink

            delegate: Any = get_audit_sink() if persist_audit else None
            set_audit_sink(_CapturingSink(delegate))
            _sink_installed = True
        except Exception:
            logger.exception(
                "could not install capturing audit sink; query_id will be absent"
            )


def _last_trace() -> Any | None:
    trace = getattr(_thread_local, "last_trace", None)
    _thread_local.last_trace = None
    return trace


# Substrings identifying a transient BACKEND failure (RagInference, Qdrant, or a
# dropped HTTP connection) as opposed to a genuine pipeline error. Matched on the
# repr because the exception types come from httpx/requests/socket depending on path.
_TRANSIENT_MARKERS = (
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "ReadError",
    "RemoteProtocolError",
    "ConnectionResetError",
    "WinError 10061",   # connection actively refused
    "WinError 10054",   # connection forcibly closed
)


def _is_transient(exc: BaseException) -> bool:
    """True for a backend blip worth one retry, not a real pipeline failure.

    Deliberately excludes rate limits and credit exhaustion: retrying a 429 for "no
    credits remaining" just burns the run's wall-clock against a wall.
    """
    text = f"{type(exc).__name__}: {exc}"
    if "insufficient_quota" in text or "no credits remaining" in text:
        return False
    return any(m in text for m in _TRANSIENT_MARKERS)


def _refusal_signals(resp: Any) -> tuple[bool, bool]:
    """``(refused, hedged)`` read from the response's own metadata.

    XbrlRag is *designed* to decline rather than invent, so a decline must be graded as
    a refusal, not a wrong answer. ``hedged`` (incomplete but partially answered)
    additionally exempts a wrong answer from the silent-wrong count — flagging
    uncertainty is the opposite of being silently confident.
    """
    metadata = getattr(resp, "metadata", None) or {}
    unanswered = getattr(resp, "unanswered_parts", None) or []
    fully = metadata.get("fully_answered")
    hedged = bool(unanswered) or fully is False
    refused = hedged and not all_numeric_values(resp) and not screen_rows(resp)
    return refused, hedged


class XbrlRagFullSystem:
    """The production XBRL pipeline, wrapped for evaluation."""

    name = "xbrlrag_full"

    def __init__(
        self,
        pipeline: Any = None,
        *,
        persist_audit: bool = False,
        name: str | None = None,
        transient_retries: int = 1,
        retry_backoff_s: float = 2.0,
    ):
        """*pipeline* is injectable so tests can stub it and so a Phase-3 arm can pass
        a pipeline built with per-stage prompt/model overrides.

        ``persist_audit`` defaults to **False**: an eval run would otherwise write
        hundreds of rows into ``audit.query_audit`` and skew the production
        cost/latency dashboards. The trace is still captured in-memory, so
        ``query_id`` is populated either way.
        """
        self._pipeline = pipeline
        self._persist_audit = persist_audit
        self._transient_retries = max(0, transient_retries)
        self._retry_backoff_s = retry_backoff_s
        self._lock = threading.Lock()
        if name:
            self.name = name

    def _get_pipeline(self) -> Any:
        with self._lock:
            if self._pipeline is None:
                from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline

                self._pipeline = XbrlRetrievalPipeline(
                    persist_audit=self._persist_audit
                )
            _install_sink(self._persist_audit)
            return self._pipeline

    # ------------------------------------------------------------------
    def answer(self, case: EvalCase) -> SystemAnswer:
        try:
            pipeline = self._get_pipeline()
        except Exception as exc:
            return SystemAnswer(error=f"pipeline construction failed: {exception_text(exc)}")

        started = time.perf_counter()
        resp = None
        last_exc: BaseException | None = None
        for attempt in range(1 + self._transient_retries):
            try:
                resp = pipeline.generate(case.question)
                break
            except RunHalt:
                raise  # the run's budget or breaker stops the run, not just this case
            except Exception as exc:
                last_exc = exc
                if attempt >= self._transient_retries or not _is_transient(exc):
                    return SystemAnswer(
                        error=exception_text(exc),
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        # The failed query's trace: the calls it made before failing.
                        served_models=served_models_of(_last_trace()),
                        service_tiers=service_tiers_of(_last_trace()),
                    )
                # A backend blip must not destroy the case. RagInference (the shared
                # embedder/reranker) has dropped out twice under eval load, taking 31
                # breakdown cases of a $2 run with it — those queries route through
                # the narrative path to reach it.
                logger.warning(
                    "transient backend error on case %s (attempt %d), retrying: %s",
                    case.id,
                    attempt + 1,
                    exc,
                )
                time.sleep(self._retry_backoff_s * (attempt + 1))
        if resp is None:  # pragma: no cover - defensive; loop returns on failure
            return SystemAnswer(
                error=exception_text(last_exc) if last_exc is not None else None,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        trace = _last_trace()
        refused, hedged = _refusal_signals(resp)

        text = final_answer_text(resp)
        return SystemAnswer(
            text=text,
            numeric_values=[n.value for n in extract_stated_numbers(text)],
            rows=answer_rows(resp),
            fact_ids=all_fact_ids(resp),
            chunk_ids=narrative_chunk_ids(resp),
            refused=refused,
            hedged=hedged,
            query_id=getattr(trace, "query_id", None),
            cost_usd=float(getattr(trace, "total_cost_usd", 0.0) or 0.0),
            latency_ms=float(getattr(trace, "total_latency_ms", None) or elapsed_ms),
            raw=resp,
            served_models=served_models_of(trace),
            service_tiers=service_tiers_of(trace),
        )


def _field(resp: Any, name: str) -> Any:
    """A response field, from an ``XbrlRagResponse`` or its ``model_dump()`` dict."""
    if isinstance(resp, dict):
        return resp.get(name)
    return getattr(resp, name, None)


def _statements(resp: Any) -> list[dict]:
    details = _field(resp, "answer_details")
    return [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []


def final_answer_text(resp: Any) -> str:
    """XbrlRag's final answer: the summary, then each kept statement, one per line.

    Spec: docs/specs/grading-precision.md (requirement 5).

    Args:
        resp: An ``XbrlRagResponse`` or its ``model_dump()`` dict.

    Returns:
        The text the answer is graded on.
    """
    lines = [str(_field(resp, "answer_summary") or "")]
    lines += [str(d.get("statement") or "") for d in _statements(resp)]
    return "\n".join(line for line in lines if line)


def _row_fact_ids(row: dict) -> set[int]:
    """Every fact id a screen row rests on: its own, its citation's, its components'."""
    ids = [row.get("fact_id"), (row.get("citation") or {}).get("fact_id")]
    ids += component_fact_ids(row.get("component_fact_ids"))
    return {fid for fid in (_as_fact_id(i) for i in ids) if fid is not None}


def _nearest(values: list[float], target: float) -> float:
    """The value nearest a target (the first on a tie)."""
    return min(values, key=lambda v: abs(v - target))


def answer_rows(resp: Any) -> list[dict]:
    """The screen rows XbrlRag's statements state, each with its stated value.

    Spec: docs/specs/grading-precision.md (requirement 5). Walking the statements in
    order, one that cites an OK screen row's fact id states that row, with the number it
    states nearest the row's execution value; a statement stating no number states no
    row, and each ticker is stated once (the first statement wins).

    Args:
        resp: An ``XbrlRagResponse`` or its ``model_dump()`` dict.

    Returns:
        ``{"ticker", "value"}`` per stated row, in statement order.
    """
    rows = [(row, _row_fact_ids(row)) for row in screen_rows(resp)]
    out: list[dict] = []
    seen: set[str] = set()
    for detail in _statements(resp):
        raw = detail.get("fact_ids")
        cited = {f for f in (_as_fact_id(i) for i in raw or []) if f is not None} if (
            isinstance(raw, list)) else set()
        if not cited:
            continue
        values = [n.value for n in extract_stated_numbers(str(detail.get("statement") or ""))]
        if not values:
            continue
        for row, ids in rows:
            ticker = row.get("ticker") or row_entity(row)
            if ticker is None or str(ticker) in seen or not (ids & cited):
                continue
            target = row_value(row)
            value = values[0] if target is None else _nearest(values, target)
            out.append({"ticker": str(ticker), "value": value})
            seen.add(str(ticker))
    return out


def from_env(**kwargs: Any) -> XbrlRagFullSystem:
    """Match the ``from_env`` construction convention used across the XBRL stages."""
    return XbrlRagFullSystem(**kwargs)
