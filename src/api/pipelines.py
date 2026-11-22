"""Lazy, cached access to the two retrieval pipelines + query_id capture.

Pipelines are expensive to construct (models, DB, LLM clients), so we build them
once on first use and reuse. Construction failures are caught and surfaced as a
clear error to the UI rather than crashing the app at boot.

The XBRL pipeline opens its own per-query trace internally (``trace_query``) and
never returns the generated ``query_id``. To give the UI the correlation id that
links an answer to its trace, we register a *capturing* audit sink that records
the last persisted ``query_id`` per request thread (FastAPI runs sync endpoints
in a threadpool, so a thread-local is request-scoped).
"""

from __future__ import annotations

import importlib
import threading
from typing import Any, Dict, Optional, Tuple

from src.llm.run_budget import RunHalt
from src.logging_config import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_xbrl_pipeline = None
_sec10k_pipeline = None
_sink_installed = False
_thread_local = threading.local()


def _to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return obj
    return {"value": str(obj)}


class _CapturingSink:
    """AuditSink that records the last query_id then delegates to the real sink."""

    def __init__(self, delegate=None):
        self._delegate = delegate

    def persist_query(self, trace) -> None:  # noqa: D401 - interface method
        _thread_local.last_query_id = getattr(trace, "query_id", None)
        if self._delegate is not None:
            try:
                self._delegate.persist_query(trace)
            except Exception:
                logger.exception("delegate audit sink failed")


def _install_sink() -> None:
    global _sink_installed
    if _sink_installed:
        return
    try:
        from src.observability.trace_context import get_audit_sink, set_audit_sink

        delegate = get_audit_sink()
        if delegate is None:
            try:
                from src.observability.audit_store import PostgresAuditStore

                delegate = PostgresAuditStore.from_env()
            except Exception:
                logger.info(
                    "Postgres audit sink unavailable; query_id still captured in-memory"
                )
        set_audit_sink(_CapturingSink(delegate))
        _sink_installed = True
    except Exception:
        logger.exception("could not install capturing audit sink")


def _last_query_id() -> str | None:
    return getattr(_thread_local, "last_query_id", None)


def get_xbrl_pipeline():
    global _xbrl_pipeline
    with _lock:
        _install_sink()
        if _xbrl_pipeline is None:
            from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline

            _xbrl_pipeline = XbrlRetrievalPipeline(persist_audit=True)
        return _xbrl_pipeline


def get_sec10k_pipeline():
    global _sec10k_pipeline
    with _lock:
        if _sec10k_pipeline is None:
            from src.pipelines.sec10k.retrieval_pipeline import RetrievalPipeline

            _sec10k_pipeline = RetrievalPipeline()
        return _sec10k_pipeline


def run_xbrl(
    query: str, user_id: str | None = None
) -> tuple[dict[str, Any], str | None]:
    _thread_local.last_query_id = None
    pipeline = get_xbrl_pipeline()
    answer = xbrl_answer(pipeline, query, user_id=user_id)
    return answer, _last_query_id()


def xbrl_answer(pipeline: Any, query: str, user_id: str | None = None) -> dict[str, Any]:
    """Answers *query* for a serving caller (docs/specs/xbrlrag-run-halt.md).

    The pipeline re-raises a run halt (budget, breaker, Flex cap, stop) so an eval run
    stops and stays resumable. A serving caller asked a question, so here, at the API
    boundary, the halt becomes today's "not answered: run halted" body rather than an
    error. Every serving surface (the BFF, its stream, ``/api/v1`` and the MCP tools)
    reaches the pipeline through :func:`run_xbrl`, which calls this.

    Args:
        pipeline: The XBRL retrieval pipeline.
        query: The user's question.
        user_id: The caller, passed to ``generate``.

    Returns:
        The answer dict, or the halted answer when the pipeline raised a ``RunHalt``.
        Any other exception propagates to the route's own handler.
    """
    try:
        response = pipeline.generate(query, user_id=user_id)
    except RunHalt as halt:
        return _halted_answer(query, halt)
    return _to_dict(response)


def _halted_answer(query: str, halt: RunHalt) -> dict[str, Any]:
    """Builds the "not answered" body for a query the run's halt stopped.

    Args:
        query: The user's question.
        halt: The halt the pipeline raised.

    Returns:
        The answer dict, flagged ``halted`` with the halt's class name.
    """
    from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse
    from src.retriever.xbrl.plan_executor import ExecutionResult, ExecutionStatus

    name = type(halt).__name__
    reason = f"run halted ({name}): {halt}"
    logger.warning("xbrl query not answered: %s", reason)
    execution_result = ExecutionResult(status=ExecutionStatus.ERROR, error=reason)
    return XbrlRagResponse(
        query=query,
        answer_summary="",
        answer_details=[],
        unanswered_parts=[{"question_fragment": query, "reason": reason}],
        metadata={
            "fully_answered": False,
            "notes": "run halted by per-run cost ceiling / circuit breaker",
            "halted": True,
            "halt_reason": name,
        },
        plan={},
        execution_result=execution_result.to_dict(),
        narrative_chunks=[],
    ).model_dump()


def run_sec10k(
    query: str, mode: str, top_k: int = 10
) -> tuple[dict[str, Any], str | None]:
    from src.pipelines.sec10k.retrieval_pipeline import RetrievalType

    rtype = {
        "basic": RetrievalType.BASIC,
        "hyde": RetrievalType.HYDE,
        "subquery": RetrievalType.SUBQUERY,
    }.get(mode, RetrievalType.BASIC)
    pipeline = get_sec10k_pipeline()
    response = pipeline.generate(query, rtype, top_k=top_k)
    # sec10k path has no per-query trace, so there is no query_id to correlate.
    return _to_dict(response), None


def _probe_pipeline(name: str, module: str, status: dict[str, bool]) -> None:
    """Import *module*, recording availability and logging at the right level.

    A missing **third-party** dependency is expected, not broken: the serving and
    ingest images are deliberately torch-free, so the PDF-legacy ``sec10k``
    pipeline (FlagEmbedding/torch) cannot import there and its modes are simply
    unavailable. Logging that at WARNING made every container start look like a
    defect. A missing **first-party** module (``src.*``) is a genuine packaging
    error and stays at WARNING, as does any other exception.
    """
    try:
        importlib.import_module(module)
        status[name] = True
        return
    except ModuleNotFoundError as exc:
        status[name] = False
        missing = exc.name or ""
        if not missing.startswith("src."):
            logger.info(
                "%s pipeline unavailable: optional dependency %r is not installed "
                "(expected in the torch-free images; its query modes are disabled, "
                "everything else is unaffected)",
                name,
                missing,
            )
            return
        logger.warning("%s pipeline import failed: %s", name, exc)
    except Exception as exc:
        status[name] = False
        logger.warning("%s pipeline import failed: %s", name, exc)


def pipelines_importable() -> dict[str, bool]:
    """Cheap check that pipeline modules import — for the health endpoint."""
    status: dict[str, bool] = {}
    _probe_pipeline("xbrl", "src.pipelines.xbrl.retrieval_pipeline", status)
    _probe_pipeline("sec10k", "src.pipelines.sec10k.retrieval_pipeline", status)
    return status
