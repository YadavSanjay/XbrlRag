"""XBRL synthesizer — design doc §2.2 "synthesizer" step.

    synthesizer  (mid-tier or frontier LLM)
        input: original query + plan + fact results (with citations) + narrative chunks
        output: answer_summary + answer_details[].{statement, fact_ids, chunk_ids}

This is the XBRL-canonical analogue of
:class:`~src.retriever.llm_rag_query.RAGResultGenerator`. The key differences:

* **Two evidence streams** instead of one: ``FACT_RESULTS`` (deterministic
  numeric values from :class:`~src.retriever.xbrl.plan_executor.ExecutionResult`'s
  ``lookup``/``compute`` steps, each with a ``citation`` pointing at a tagged
  iXBRL fact) and ``CONTEXT_CHUNKS`` (narrative/table chunks from
  :class:`~src.retriever.xbrl.xbrl_narrative_retriever.XbrlNarrativeRetriever`,
  via the plan's ``narrative`` step(s)).
* **Citation shape**: ``answer_details[].chunk_ids`` (prose/table chunk ids,
  as in the PDF flow) *plus* ``answer_details[].fact_ids`` (``fact_id``
  integers from ``lookup`` citations, or ``step_id`` strings for ``compute``
  results) — giving every statement a numeric *and* narrative provenance
  trail.
* The LLM never performs arithmetic itself — all numbers come verbatim from
  FACT_RESULTS (the ``compute`` steps already ran via
  :class:`~src.retriever.xbrl.xbrl_calculator_tool.XbrlCalculatorTool`).
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, List, Literal, Optional

from pydantic import BaseModel

from prompts.prompt_registry import PromptRegistry, get_xbrl_registry
from src.llm.escalation import emit_escalation, escalation_ladder, is_escalation_trigger
from src.llm.gateway import LLMRefusalError, extract_json, resolve_model_ref, stage_call_params
from src.llm.gateway.model_ref import ModelRef
from src.llm.stage_models import require_api_key, resolve_stage
from src.llm.xbrl_gateway import build_xbrl_gateway
from src.logging_config import get_logger

if TYPE_CHECKING:
    from src.retriever.xbrl.plan_executor import ExecutionResult

logger = get_logger(__name__)


XBRL_SYNTHESIZER_RESPONSE_FORMAT = {
    "answer_summary": "string - a concise, human-readable overall answer in 3 to 5 sentences, "
                       "limited ONLY to what is supported by FACT_RESULTS and CONTEXT_CHUNKS. "
                       "If the question is not fully answerable, clearly say which parts are unknown.",
    "answer_details": [
        {
            "statement": "string - one specific factual statement or conclusion you are making.",
            "fact_ids": "array of strings - fact_id (from a lookup citation) or step_id (from a "
                        "compute step) values from FACT_RESULTS that support this statement. "
                        "Empty array if purely narrative.",
            "chunk_ids": "array of strings - chunk_id values from CONTEXT_CHUNKS that support "
                         "this statement. Empty array if purely numeric.",
        }
    ],
    "unanswered_parts": [
        {
            "question_fragment": "string - the part of the user query you could NOT answer.",
            "reason": "string - short explanation, e.g. 'fact lookup status was AMBIGUOUS' or "
                      "'no narrative chunks discuss this driver'.",
        }
    ],
    "metadata": {
        "fully_answered": "true or false",
        "notes": "string - caveats about data quality, ambiguity, non-OK FACT_RESULTS entries, "
                 "or discrepancies between FACT_RESULTS and CONTEXT_CHUNKS.",
    },
}


class XbrlRagResponse(BaseModel):
    query: str
    answer_summary: str
    answer_details: list[dict]
    unanswered_parts: list[dict] = []
    metadata: dict = {}
    plan: dict
    execution_result: dict
    narrative_chunks: list[dict] = []


class XbrlRagSynthesizer:
    """Synthesizes a final answer from a plan's ``ExecutionResult``.

    Example:
        >>> synthesizer = XbrlRagSynthesizer()
        >>> response = synthesizer.generate_answer(query, plan, execution_result)
        >>> response.answer_summary
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        prompt_provider: str | None = None,
        prompt_series: str | None = None,
        prompt_registry: PromptRegistry | None = None,
    ):
        # The model comes from the LLM profile active now (an explicit model wins), and
        # the key from that model's provider (docs/specs/llm-provider-profiles.md).
        stage_model = resolve_stage("synthesizer", model)
        self.api_key = require_api_key(stage_model.model, stage_model.settings, api_key)
        self.model = stage_model.model
        self._llm_settings = stage_model.settings

        self._model_ref = resolve_model_ref(self.model)
        self.model_config = self._model_ref.config
        self._registry = prompt_registry or get_xbrl_registry()
        self._prompt = self._registry.resolve(
            "synthesizer",
            provider=prompt_provider or self._model_ref.provider,
            series=prompt_series or self._model_ref.series,
            version=prompt_version,
        )
        self._prompt_sig = self._prompt.signature()
        self._llm_client = build_xbrl_gateway("synthesizer", api_key=api_key)

        logger.info(f"Initialized XBRL RAG Synthesizer with model={model}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_answer(self, query: str, plan: dict, execution_result: ExecutionResult) -> XbrlRagResponse:
        fact_results = self._fact_results(execution_result)
        narrative_chunks = self._narrative_chunks(execution_result)

        messages = self._build_messages(query, plan, fact_results, narrative_chunks)
        logger.debug(f"\n\n XBRL synthesizer messages prepared for LLM call: {messages}\n\n")

        # Escalation ladder: on a parse-error / empty-content deterioration signal,
        # retry one rung up the model tier before degrading (design §3.3). Only if
        # the top rung still fails do we return a structured, not-fully-answered
        # response (§3.4) rather than aborting a query whose facts already computed.
        refs = escalation_ladder("synthesizer", self._model_ref, settings=self._llm_settings)
        last_error: Exception = RuntimeError("synthesis failed")
        for i, ref in enumerate(refs):
            try:
                llm_response = self._do_llm_call(messages, model_ref=ref)
                logger.info(f"XBRL synthesizer LLM response: {llm_response}")
                parsed = extract_json(llm_response)
                if not isinstance(parsed, dict):
                    raise json.JSONDecodeError("synthesizer returned non-object JSON", str(llm_response), 0)
                return XbrlRagResponse(
                    query=query,
                    answer_summary=parsed.get("answer_summary", ""),
                    answer_details=parsed.get("answer_details", []),
                    unanswered_parts=parsed.get("unanswered_parts", []),
                    metadata=parsed.get("metadata", {}),
                    plan=plan,
                    execution_result=execution_result.to_dict(),
                    narrative_chunks=narrative_chunks,
                )
            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                reason = self._escalation_reason(e)
                if i + 1 < len(refs) and is_escalation_trigger("synthesizer", reason):
                    emit_escalation("synthesizer", ref, refs[i + 1], reason, attempt=i + 1)
                    continue
                break

        logger.error(f"XBRL synthesizer failed after {len(refs)} attempt(s): {last_error}")
        return self._degraded_response(query, plan, execution_result, narrative_chunks, last_error)

    @staticmethod
    def _escalation_reason(error: Exception) -> str:
        """Classify a synthesis failure into an escalation reason (see ESCALATION_POLICY)."""
        if isinstance(error, LLMRefusalError):
            return "refusal"  # the provider declined: final, never escalated
        if isinstance(error, json.JSONDecodeError):
            return "parse_error"
        if "empty" in str(error).lower():
            return "empty_content"
        return "other"  # e.g. content filter — not an escalation trigger

    def _degraded_response(
        self, query: str, plan: dict, execution_result: ExecutionResult,
        narrative_chunks: list[dict], error: Exception,
    ) -> XbrlRagResponse:
        """Structured fallback when the synthesizer output can't be parsed as JSON,
        or the model declined to answer (a provider refusal)."""
        if isinstance(error, LLMRefusalError):
            return XbrlRagResponse(
                query=query,
                answer_summary=(
                    "This answer could not be completed: the model declined to answer "
                    "the synthesis step. The retrieved facts are preserved in "
                    "execution_result."
                ),
                answer_details=[],
                unanswered_parts=[{"part": query, "reason": "synthesizer_refusal"}],
                metadata={
                    "fully_answered": False,
                    "synthesis_error": {"type": "refusal", "detail": str(error)},
                },
                plan=plan,
                execution_result=execution_result.to_dict(),
                narrative_chunks=narrative_chunks,
            )
        return XbrlRagResponse(
            query=query,
            answer_summary=(
                "This answer could not be completed: the synthesis step returned a "
                "response that was not valid JSON. The retrieved facts are preserved "
                "in execution_result; please retry the query."
            ),
            answer_details=[],
            unanswered_parts=[{"part": query, "reason": "synthesizer_parse_error"}],
            metadata={
                "fully_answered": False,
                "synthesis_error": {"type": self._escalation_reason(error), "detail": str(error)},
            },
            plan=plan,
            execution_result=execution_result.to_dict(),
            narrative_chunks=narrative_chunks,
        )

    # ------------------------------------------------------------------
    # Evidence formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _fact_results(execution_result: ExecutionResult) -> list[dict]:
        """``lookup``/``compute`` step results — excludes ``narrative``
        steps (those become ``CONTEXT_CHUNKS`` instead)."""
        return [step.to_dict() for step in execution_result.steps if step.op != "narrative"]

    @staticmethod
    def _narrative_chunks(execution_result: ExecutionResult) -> list[dict]:
        """Deduplicated chunk dicts from every ``narrative`` step's
        :class:`~src.retriever.xbrl.xbrl_narrative_retriever.NarrativeRetrievalResult`
        (carried in ``StepResult.detail``)."""
        chunks: list[dict] = []
        seen_ids: set[str] = set()
        for step in execution_result.steps:
            if step.op != "narrative" or step.detail is None:
                continue
            for c in getattr(step.detail, "chunks", []):
                if c.chunk_id in seen_ids:
                    continue
                seen_ids.add(c.chunk_id)
                chunks.append(c.to_dict())
        return chunks

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _build_messages(self, query: str, plan: dict, fact_results: list[dict], narrative_chunks: list[dict]) -> list[dict]:
        system_prompt_text = self._prompt.system.format(
            llm_answer_format=XBRL_SYNTHESIZER_RESPONSE_FORMAT,
        )
        user_prompt_text = self._prompt.user.format(
            user_query=query,
            plan_json=json.dumps(plan, indent=2, default=str),
            fact_results=json.dumps(fact_results, indent=2, default=str),
            context_chunks=json.dumps(narrative_chunks, indent=2, default=str),
        )
        return [
            {"role": "system", "content": [{"type": "text", "text": system_prompt_text}]},
            {"role": "user", "content": [{"type": "text", "text": user_prompt_text}]},
        ]

    def _do_llm_call(self, messages: list[dict], model_ref: ModelRef | None = None, **kwargs) -> str:
        """Run the synthesizer LLM call through the instrumented gateway.

        Latency, token usage / cost, transient-error retry and the structured
        ``llm_call`` event are handled by :class:`~src.llm.gateway.LLMGateway`.
        ``model_ref`` overrides the model (used by escalation); its own per-call
        config (token/temperature) is used so an escalated model runs correctly.
        """
        ref = model_ref or self._model_ref
        cfg = ref.config or self.model_config
        result = self._llm_client.complete(
            messages=messages,
            model_ref=ref,
            stage="synthesizer",
            **stage_call_params(cfg, temperature=0.1),
            response_format="json",
            prompt_templates=self._prompt_sig["templates"],
            prompt_version=self._prompt_sig["version"],
            **kwargs,
        )
        return result.content
