"""WS-9 / E1 — the *system under test* seam.

WS-2's regression matrix can only vary stages *inside* XbrlRag
(``regression_runner._STAGE_PARAM`` maps arm stages 1:1 onto
``XbrlRetrievalPipeline`` kwargs), so it cannot express "run the same questions
against a naive top-k RAG baseline" or "against a long-context model." This
protocol is the missing layer above :class:`~src.eval.regression.prompt_matrix.Arm`:
an ``XbrlRagFullSystem`` may itself be parameterised by an ``Arm``, while an
``OpenAILongContextSystem`` has no arm at all.

Design: [docs/design/XbrlRag_Evaluation.md](../../../docs/design/XbrlRag_Evaluation.md).
Only ``xbrlrag_full`` ships in Phase 0; the comparison arms are Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from src.eval.core.case import EvalCase
from src.observability.service_tiers import TierCounts, merge_counts


@dataclass
class SystemAnswer:
    """One system's response to one case, normalised for grading.

    ``query_id`` is the load-bearing field: for XbrlRag it is the correlation key
    already written to ``audit.query_audit``, so an eval case row links straight
    to its full existing trace (plan, SQL, every LLM prompt, per-call cost). That
    is how the UI reaches per-case fidelity without new instrumentation.
    """

    text: str = ""
    numeric_values: list[float] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    fact_ids: list[int] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)

    # Did the system explicitly decline / flag incompleteness? A refusal is not a
    # wrong answer — XbrlRag's synthesizer is *meant* to decline rather than invent —
    # so the graders keep the two apart (see graders.base.Grade).
    refused: bool = False
    hedged: bool = False

    query_id: str | None = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None
    raw: Any = None  # untouched XbrlRagResponse, for drill-down
    # Requested model id -> sorted distinct model ids the providers reported serving
    # for this answer (docs/specs/served-model-id.md).
    served_models: dict[str, list[str]] = field(default_factory=dict)
    # The answer's LLM calls by requested and served service tier, per provider and stage
    # (src/observability/service_tiers.py).
    service_tiers: TierCounts = field(default_factory=dict)
    # What a resolving long-context arm chose to read, and why; empty for every other
    # system (docs/specs/long-context-arms.md).
    selection: dict[str, Any] = field(default_factory=dict)
    # The tool-calling arm's record of its loop (steps, tool calls, sources, the budget that
    # stopped it); empty for every other system (docs/specs/frontier-sec-tools.md).
    agent: dict[str, Any] = field(default_factory=dict)

    @property
    def produced_anything(self) -> bool:
        return bool(self.numeric_values or self.rows or self.fact_ids)

    def to_dict(self) -> dict[str, Any]:
        """The `produced` JSONB payload persisted per case result (no `raw`)."""
        payload: dict[str, Any] = {
            "text": self.text,
            "numeric_values": self.numeric_values,
            "rows": self.rows,
            "fact_ids": self.fact_ids,
            "chunk_ids": self.chunk_ids,
            "refused": self.refused,
            "hedged": self.hedged,
            "error": self.error,
        }
        if self.selection:
            payload["selection"] = self.selection
        if self.agent:
            payload["agent"] = self.agent
        return payload


def served_models_of(trace: Any) -> dict[str, list[str]]:
    """An independent copy of a trace's ``served_models``.

    Args:
        trace: A completed ``TraceContext``, or None.

    Returns:
        Requested model id -> sorted distinct served ids; empty when there is no trace.
    """
    served = getattr(trace, "served_models", None) or {}
    return {model: sorted(set(ids)) for model, ids in served.items()}


def service_tiers_of(trace: Any) -> TierCounts:
    """An independent copy of a trace's ``service_tiers`` call counts.

    Args:
        trace: A completed ``TraceContext``, or None.

    Returns:
        ``"<provider>/<stage>"`` -> requested tier -> served tier -> calls
        (src/observability/service_tiers.py); empty when there is no trace.
    """
    return merge_counts([getattr(trace, "service_tiers", None) or {}])


def exception_text(exc: BaseException) -> str:
    """The error text a failed answer stores (docs/specs/eval-run-checkpoint.md, req. 13).

    ``repr(exc)``, unless that drops the message: an exception built with no ``args`` but
    a non-empty ``str`` (Qdrant's ``UnexpectedResponse`` reprs as ``UnexpectedResponse()``)
    is written ``Name('<str(exc)>')``, so its status stays readable by ``error_origin``.
    Re-exported as ``src.eval.core.checkpoint.run.error_text``; it lives here so the runner
    and the arms can use it without importing the checkpoint module.

    Args:
        exc: The exception a system raised.

    Returns:
        The text.
    """
    if not exc.args:
        message = str(exc)
        if message:
            return f"{type(exc).__name__}({message!r})"
    return repr(exc)


@runtime_checkable
class SystemUnderTest(Protocol):
    """Anything that can answer an :class:`EvalCase`.

    Implementations must be **thread-safe**: :class:`~src.eval.core.runner.EvalRunner`
    calls ``answer`` concurrently. They must also not raise — a failure is reported
    as ``SystemAnswer(error=...)`` so one bad case cannot abort a run.
    """

    name: str

    def answer(self, case: EvalCase) -> SystemAnswer: ...


class CallableSystem:
    """Adapts a bare ``fn(question) -> SystemAnswer`` into a `SystemUnderTest`.

    Keeps tests (and one-off scripts) free of a class definition, and mirrors how
    ``NumericGrader.grade`` already accepts a ``run_fn``.
    """

    def __init__(self, name: str, fn: Any):
        self.name = name
        self._fn = fn

    def answer(self, case: EvalCase) -> SystemAnswer:
        try:
            result = self._fn(case.question)
        except Exception as exc:  # a broken system fails the case, not the run
            return SystemAnswer(error=exception_text(exc))
        if isinstance(result, SystemAnswer):
            return result
        raise TypeError(
            f"{self.name}: fn must return SystemAnswer, got {type(result).__name__}"
        )
