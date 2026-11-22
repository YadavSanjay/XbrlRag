"""plan_generator (design doc §2.2.3).

One LLM call (gpt-5-mini) producing a typed `{"steps": [...]}` execution plan
for `NUMERIC_COMPUTE`, `HYBRID`, and dimensional `NUMERIC_LOOKUP` queries that
`intent_router`'s fast path (§2.2.2) could not resolve to a 1-step plan.

The system prompt
(prompts/templates/xbrl/xbrl_query_plan_generator.txt) is static and already
carries the full CONCEPT_FAMILIES taxonomy, dimension hints, fiscal-year rules,
few-shots, and an explicit `{"error": ...}` contract — it is loaded verbatim
(never `.format()`-ed, since it contains literal JSON braces).

This module supplies the surrounding harness (§2.2.3 items 1-3):

1. A user-prompt template (`xbrl_query_plan_generator_user.txt`) that injects
   the `IntentRouterResult`'s resolved companies/periods/concepts as a
   RESOLVED_ENTITIES block, so the LLM never has to recall a CIK from memory.
2. The LLM call itself (gpt-5-mini, low temperature, JSON response).
3. Validation via `PlanValidator`, with one retry that appends the validator's
   error message to the user prompt before falling back to the prompt's own
   `{"error": ...}` contract.
"""
from __future__ import annotations

import json
import os
from typing import Any, List, Literal, Optional

from prompts.prompt_registry import PromptRegistry, get_xbrl_registry
from src.llm.escalation import emit_escalation, escalation_ladder, is_escalation_trigger
from src.llm.gateway import extract_json, resolve_model_ref, stage_call_params
from src.llm.stage_models import require_api_key, resolve_stage
from src.llm.xbrl_gateway import build_xbrl_gateway
from src.logging_config import get_logger
from src.observability.trace_context import log_event
from src.retriever.xbrl.intent_router import IntentRouterResult
from src.retriever.xbrl.named_company_screen import named_companies, named_company_plan_errors
from src.retriever.xbrl.operation_cues import operation_plan_errors
from src.retriever.xbrl.plan_validator import PlanValidator
from src.retriever.xbrl.universe_routing import group_plan_errors

logger = get_logger(__name__)

# The prompt's error contract (v10+) names the failure in `error_code`. A nested ranking
# (a screen over another screen's members) cannot be one plan; the retrieval pipeline
# re-decomposes the question once on it (XbrlRetrievalPipeline._redecompose_on_refusal).
NESTED_RANKING_ERROR = "nested_ranking"


class PlanGenerator:
    """Generates and validates a typed execution plan for COMPUTE/HYBRID/dimensional queries.

    Example:
        >>> generator = PlanGenerator()
        >>> plan = generator.generate_plan(
        ...     "What was NVIDIA's Data Center revenue growth from FY2025 to FY2026?",
        ...     intent_result,
        ... )
        >>> plan["steps"]
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        plan_validator: PlanValidator | None = None,
        max_retries: int = 1,
        prompt_version: str | None = None,
        prompt_provider: str | None = None,
        prompt_series: str | None = None,
        prompt_registry: PromptRegistry | None = None,
    ):
        # The model comes from the LLM profile active now (an explicit model wins), and
        # the key from that model's provider (docs/specs/llm-provider-profiles.md).
        stage_model = resolve_stage("plan_generator", model)
        self.api_key = require_api_key(stage_model.model, stage_model.settings, api_key)
        self.model = stage_model.model
        self._llm_settings = stage_model.settings
        self.plan_validator = plan_validator or PlanValidator()
        self.max_retries = max_retries

        self._model_ref = resolve_model_ref(self.model)
        self.model_config = self._model_ref.config
        self._registry = prompt_registry or get_xbrl_registry()
        self._prompt = self._registry.resolve(
            "plan_generator",
            provider=prompt_provider or self._model_ref.provider,
            series=prompt_series or self._model_ref.series,
            version=prompt_version,
        )
        # The plan-generator system prompt is static and full of literal JSON
        # braces - use the raw template text, never PromptTemplate.format() it.
        self._system_prompt_text = self._prompt.system.template
        self._prompt_sig = self._prompt.signature()
        self._llm_client = build_xbrl_gateway("plan_generator", api_key=api_key)

        logger.info(f"Initialized XBRL Plan Generator with model={model}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_plan(self, query: str, intent_result: IntentRouterResult, **kwargs) -> dict:
        """Produce a validated `{"steps": [...]}` plan, or an `{"error": ...}` object.

        Runs the local validate-retry loop (below) on the base model; if that loop
        still ends in `plan_validation_failed`, escalate one model tier up (per
        `ESCALATION_POLICY["plan_generator"]`, medium→frontier) and re-run the whole
        loop on the stronger model. Only `plan_validation_failed` is an escalation
        trigger — a JSON-parse error or the prompt's own error contract is returned
        as-is (mirrors the synthesizer / verifier wiring; A7).
        """
        refs = escalation_ladder("plan_generator", self._model_ref, settings=self._llm_settings)
        result: dict = {}
        for i, ref in enumerate(refs):
            result = self._generate_plan_once(query, intent_result, ref, **kwargs)
            if "error" not in result:
                return result
            reason = result["error"]
            if i + 1 < len(refs) and is_escalation_trigger("plan_generator", reason):
                emit_escalation("plan_generator", ref, refs[i + 1], reason, attempt=i + 1)
                continue
            return result  # success already returned above; non-trigger error → as-is
        return result

    def _generate_plan_once(
        self, query: str, intent_result: IntentRouterResult, model_ref, **kwargs
    ) -> dict:
        """One validate-retry loop on a single model tier.

        Makes at most `1 + max_retries` LLM calls: if `PlanValidator.validate()`
        returns errors, the validator's message is appended to the user prompt as
        `VALIDATION_FEEDBACK` and the call is retried. After `max_retries` failed
        attempts, returns a `{"error": "plan_validation_failed", ...}` object in the
        same shape as the prompt's own error contract.
        """
        validation_feedback = ""
        last_errors: list[str] = []

        for attempt in range(self.max_retries + 1):
            messages = self._build_messages(query, intent_result, validation_feedback)
            response_text = self._do_llm_call(messages, model_ref=model_ref, **kwargs)
            data = self._parse_json(response_text, query)

            if "error" in data:
                return data  # plan_generator's own error contract - pass through

            errors = self.plan_validator.validate(data)
            # The plan must screen the universe the router resolved and aggregate it when
            # the question cues it (docs/specs/screen-aggregates.md R6); same retry.
            errors += group_plan_errors(
                data,
                getattr(intent_result, "universe", None),
                getattr(intent_result, "aggregate", None),
            )
            # A CAGR or ratio the question cues must be computed by the plan
            # (docs/specs/plan-cagr-and-ratios.md, Requirement 7); same retry.
            errors += operation_plan_errors(data, getattr(intent_result, "operation", None))
            # A ranking among the companies the question names must screen exactly those
            # companies (docs/specs/named-company-screen.md, Requirement 5); same retry.
            named = named_companies(getattr(intent_result, "companies", None) or [])
            named_errors = named_company_plan_errors(
                data, named, getattr(intent_result, "universe", None)
            )
            if named_errors:
                log_event(
                    "decision", point="plan_validation", decision="reject_named_company_screen",
                    attempt=attempt + 1, named=named, errors=named_errors,
                )
            errors += named_errors
            if not errors:
                return data

            last_errors = errors
            logger.warning(f"Plan validation failed (attempt {attempt + 1}/{self.max_retries + 1}): {errors}")
            validation_feedback = (
                "\nVALIDATION_FEEDBACK: Your previous response had error: "
                + "; ".join(errors)
                + ". Fix and resend JSON only."
            )

        return {
            "error": "plan_validation_failed",
            "user_query": query,
            "reason": "; ".join(last_errors),
            "suggestion": "Rephrase the query or simplify the request (fewer companies/periods/metrics).",
        }

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def _build_messages(self, query: str, intent_result: IntentRouterResult, validation_feedback: str) -> list[dict]:
        user_prompt_text = self._prompt.user.format(
            resolved_entities_block=self._format_resolved_entities(intent_result),
            fiscal_years_in_scope_json=json.dumps(self._fiscal_years_in_scope(intent_result)),
            concept_phrases_json=json.dumps(intent_result.concept_candidates),
            dimension_hint_present=json.dumps(intent_result.dimension_hint_present),
            # The router's rule-resolved group and cue (prompt v7+; older versions ignore
            # the extra keys).
            universe_json=json.dumps(getattr(intent_result, "universe", None)),
            aggregate_json=json.dumps(getattr(intent_result, "aggregate", None)),
            # The rule-read CAGR/ratio cue (prompt v8+; older versions ignore the key).
            operation_json=json.dumps(getattr(intent_result, "operation", None)),
            user_query=query,
            validation_feedback=validation_feedback,
        )
        return [
            {"role": "system", "content": [{"type": "text", "text": self._system_prompt_text}]},
            {"role": "user", "content": [{"type": "text", "text": user_prompt_text}]},
        ]

    @staticmethod
    def _format_resolved_entities(intent_result: IntentRouterResult) -> str:
        lines: list[str] = []
        for c in intent_result.companies:
            if c.is_ambiguous:
                lines.append(f"- {c.mention}: UNRESOLVED (could not match a known filer)")
                continue
            fye = f"{c.fye_month:02d}-{c.fye_day:02d}" if c.fye_month and c.fye_day else "unknown"
            lines.append(f"- {c.ticker or c.mention} (cik={c.cik}): fye={fye}, last_seen_fy={c.last_seen_fy}")

        # Surface resolved periods with any quarter/YTD selection so the plan can
        # carry fiscal_period. Only emit the directive when a non-annual period
        # was actually detected, to avoid nudging annual queries.
        quarter_periods = [p for p in intent_result.periods
                           if not p.ambiguous and p.fiscal_year is not None and p.fiscal_period != "FY"]
        if quarter_periods:
            lines.append("")
            lines.append("RESOLVED_PERIODS (10-Q quarters detected):")
            for p in quarter_periods:
                kind = "year-to-date/cumulative" if p.ytd else "discrete quarter"
                lines.append(f"- FY{p.fiscal_year} {p.fiscal_period} ({kind})")
            lines.append(
                'Add "fiscal_period": "Q1"|"Q2"|"Q3"|"Q4" (and "ytd": true for a '
                'cumulative year-to-date figure) to each lookup/screen step that '
                'targets one of these quarters. Omit fiscal_period (defaults to "FY") '
                "for annual figures."
            )
        return "\n".join(lines) if lines else "(none resolved - infer companies from the query)"

    @staticmethod
    def _fiscal_years_in_scope(intent_result: IntentRouterResult) -> list[int]:
        return sorted({p.fiscal_year for p in intent_result.periods if p.fiscal_year is not None})

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _do_llm_call(self, messages: list[dict], model_ref=None, **kwargs) -> str:
        """Run the plan-generator LLM call through the instrumented gateway.

        `model_ref` selects the tier (base by default; the frontier tier when
        escalated). The escalated model's own per-call token/temperature budget is
        used (`ref.config`), falling back to the base config. Latency, token usage /
        cost, transient-error retry and the structured ``llm_call`` event are handled
        by the gateway; the ``response_format`` JSON-mode constraint is preserved.
        """
        ref = model_ref or self._model_ref
        cfg = ref.config or self.model_config
        result = self._llm_client.complete(
            messages=messages,
            model_ref=ref,
            stage="plan_generator",
            **stage_call_params(cfg, temperature=0.1),
            response_format="json",
            prompt_templates=self._prompt_sig["templates"],
            prompt_version=self._prompt_sig["version"],
            **kwargs,
        )
        return result.content

    @staticmethod
    def _parse_json(response_text: str, query: str) -> dict:
        try:
            return extract_json(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Plan generator returned invalid JSON: {e}")
            return {
                "error": "invalid_json_response",
                "user_query": query,
                "reason": str(e),
                "suggestion": "Retry the query.",
            }


if __name__ == "__main__":
    from src.retriever.xbrl.entity_extractor import EntityExtractor
    from src.retriever.xbrl.intent_router import IntentRouter

    router = IntentRouter(entity_extractor=EntityExtractor.from_env())
    generator = PlanGenerator()

    query = "What was the year-over-year growth in Google's R&D expense from FY2022 to FY2023?"
    intent_result = router.route(query)
    plan = generator.generate_plan(query, intent_result)
    print(json.dumps(plan, indent=2))
