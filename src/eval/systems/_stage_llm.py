"""Shared plumbing for the eval baseline arms (closed-book, naive RAG, long-context).

Each arm makes its LLM call the way an XbrlRag stage does (see
``src/retriever/xbrl/llm_xbrl_synthesizer.py``): the model is the active LLM profile's
model for the stage's tier, read when the arm is built (or is given explicitly, as the
long-context arm does), the prompt pair from the XBRL prompt registry, and
the call goes through an :class:`~src.llm.gateway.LLMGateway` built by
``build_xbrl_gateway(stage)``. Every answer runs inside its own trace, so it gets a
``query_id`` and its LLM cost and latency.

Spec: [docs/specs/baseline-arms.md](../../../docs/specs/baseline-arms.md).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from prompts.prompt_registry import ResolvedPrompt, get_xbrl_registry
from src.eval.core.system import (
    SystemAnswer,
    exception_text,
    served_models_of,
    service_tiers_of,
)
from src.llm.gateway import LLMGateway, LLMRefusalError, call_params, resolve_model_ref
from src.llm.run_budget import RunHalt
from src.llm.stage_models import resolve_stage
from src.llm.xbrl_gateway import build_xbrl_gateway
from src.logging_config import get_logger
from src.observability.trace_context import trace_query

logger = get_logger(__name__)

# Reasoning shares this budget; call_params still caps it at the model's own ceiling.
_DEFAULT_MAX_COMPLETION_TOKENS = 8000
_DEFAULT_TEMPERATURE = 0.1


@dataclass(frozen=True)
class UserPart:
    """One user message of a multi-message call.

    Attributes:
        text: The message's formatted text.
        cache_point: Whether the prompt up to the end of this message is worth caching.
    """

    text: str
    cache_point: bool = False


class StageLLM:
    """One XBRL-registry stage's model, prompt pair and (lazily built) gateway.

    Thread-safe: the eval runner calls ``answer`` concurrently, and the default
    gateway is built once under a lock on first use.
    """

    def __init__(
        self, stage: str, gateway: LLMGateway | None = None, *, model: str | None = None
    ) -> None:
        """Resolves the stage's model and prompts; builds no client.

        Args:
            stage: The stage name in the prompt registry (and, unless *model* is
                given, in ``XBRL_STAGE_TIERS``).
            gateway: The gateway to call; built with ``build_xbrl_gateway(stage)`` on
                first use when omitted.
            model: An explicit model ID, used as given. When omitted, the model is the
                active LLM profile's model for the stage's tier, read now.
        """
        self.stage = stage
        model_id = model if model is not None else resolve_stage(stage).model
        self.model = model_id
        self._model_ref = resolve_model_ref(model_id)
        # The model's provider in configs/llm/llm.yaml (e.g. "anthropic", "openai").
        self.provider: str = self._model_ref.provider
        self._model_config: dict[str, Any] = self._model_ref.config
        self.prompt: ResolvedPrompt = get_xbrl_registry().resolve(
            stage, provider=self._model_ref.provider, series=self._model_ref.series
        )
        self._prompt_sig = self.prompt.signature()
        self._gateway = gateway
        self._lock = threading.Lock()

    def gateway(self) -> LLMGateway:
        """Returns the gateway, building the default one on first use."""
        if self._gateway is None:
            with self._lock:
                if self._gateway is None:
                    self._gateway = build_xbrl_gateway(self.stage)
        return self._gateway

    def complete(self, user_text: str | Sequence[UserPart]) -> str:
        """Sends the stage's system prompt and the user message(s); returns the reply.

        Args:
            user_text: The fully formatted user message, or a sequence of user parts,
                each sent as its own user message in order. A part's ``cache_point``
                marks the prompt up to the end of that message as worth caching
                (docs/specs/llm-anthropic-and-caching.md).

        Returns:
            The model's reply content.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.prompt.system.format()},
        ]
        if isinstance(user_text, str):
            messages.append({"role": "user", "content": user_text})
        else:
            for part in user_text:
                message: dict[str, Any] = {"role": "user", "content": part.text}
                if part.cache_point:
                    message["cache_point"] = True
                messages.append(message)
        params = call_params(
            self._model_config,
            max_completion_tokens=_DEFAULT_MAX_COMPLETION_TOKENS,
            temperature=_DEFAULT_TEMPERATURE,
        )
        temperature = params["temperature"]
        result = self.gateway().complete(
            messages=messages,
            model_ref=self._model_ref,
            stage=self.stage,
            max_completion_tokens=params["max_completion_tokens"],
            temperature=None if temperature is None else float(temperature),
            response_format="json",
            prompt_templates=self._prompt_sig["templates"],
            prompt_version=self._prompt_sig["version"],
        )
        return result.content


def trace_answer(answer: SystemAnswer) -> dict[str, Any]:
    """A baseline answer in the shape the Trace Explorer renders as the final answer.

    The UI's answer view reads an ``XbrlRagResponse`` dump (``answer_summary``,
    ``answer_details``, ``unanswered_parts``, ``metadata.fully_answered``); a baseline
    arm has no per-statement citations, so it fills the summary and the confidence flag
    and keeps its full graded payload under ``baseline``.

    Args:
        answer: The arm's answer for one case.

    Returns:
        The dict stored on ``TraceContext.answer`` (``audit.query_audit.answer``), made
        JSON-safe so one odd value in the payload cannot cost the case its trace.
    """
    payload = {
        "answer_summary": answer.text,
        "answer_details": [],
        "unanswered_parts": [],
        "metadata": {
            "fully_answered": not (answer.refused or answer.hedged or answer.error),
            "refused": answer.refused,
            "hedged": answer.hedged,
        },
        "baseline": answer.to_dict(),
    }
    result: dict[str, Any] = json.loads(json.dumps(payload, default=str))
    return result


def answer_in_trace(question: str, produce: Callable[[], SystemAnswer]) -> SystemAnswer:
    """Runs *produce* inside a fresh trace and stamps the trace's totals on its answer.

    The answer is also recorded on the trace (:func:`trace_answer`) before it closes, so
    when an audit sink is registered (``--persist-audit``) the case's trace lands in
    ``audit.*`` with its LLM calls, cost and final answer, as XbrlRag's own does.

    Any exception except a run halt becomes ``SystemAnswer(error=...)``, so one bad case
    cannot abort a run; a :class:`RunHalt` propagates so the run stops. A provider
    refusal (:class:`LLMRefusalError`) becomes a refused answer, not an error.

    Args:
        question: The case's question, recorded on the trace.
        produce: Builds the answer; makes the LLM call(s) under the active trace.

    Returns:
        The answer, with ``query_id``, ``cost_usd`` and ``latency_ms`` from the trace.
    """
    with trace_query(question) as trace:
        try:
            answer = produce()
        except RunHalt:
            raise
        except LLMRefusalError as refusal:
            # The model declined: graded as a refusal, as XbrlRag's own declines are.
            logger.warning("baseline arm: model refused question %r: %s",
                           question[:200], refusal)
            answer = SystemAnswer(text=str(refusal), refused=True)
        except Exception as exc:  # isolation point: a failed case is reported, not raised
            logger.warning("baseline arm failed on question %r: %r", question[:200], exc)
            trace.error = exception_text(exc)
            answer = SystemAnswer(error=exception_text(exc))
        # Set before the trace closes, so an audit sink persists it with the trace.
        trace.answer = trace_answer(answer)
    answer.query_id = trace.query_id
    answer.cost_usd = float(trace.total_cost_usd)
    answer.latency_ms = float(trace.total_latency_ms or 0.0)
    answer.served_models = served_models_of(trace)
    answer.service_tiers = service_tiers_of(trace)
    return answer
