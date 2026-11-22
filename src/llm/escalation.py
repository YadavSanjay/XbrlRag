"""Model-escalation primitives for the XBRL query stages.

Design: docs/design/XbrlRag_LLM_Harness.md.

A stage that detects a deterioration signal (parse failure, empty content, …)
retries the *same* request one rung up the active LLM profile's tier ladder and
records a ``model_escalation`` event. This module centralizes the ladder
resolution, the trigger check, and the event emission; each stage keeps its own
short retry loop (the "what counts as a failure" logic is stage-specific and
belongs in the stage).

Escalation follows the **active profile** (configs/llm/llm.yaml): under ``openai`` it
climbs gpt-5-mini -> gpt-5, under ``anthropic`` the Claude tiers. A base model on a
provider the profile does not use (an explicit override) climbs that provider's legacy
``MODEL_TIERS`` ladder instead, as before profiles existed.
"""
from __future__ import annotations

from typing import List

from src.llm.config import ESCALATION_POLICY, MODEL_TIERS
from src.llm.gateway.model_ref import ModelRef, resolve_model_ref
from src.llm.profiles import LlmSettings, load_committed_settings
from src.observability.trace_context import log_event


def _provider_key(provider: str) -> str:
    """Map a ModelRef provider to its llm.yaml provider name (and MODEL_TIERS key).

    ``deepseek`` keeps its own name: mapped to ``openai``, a DeepSeek stage would climb the
    OpenAI ladder and call an OpenAI model. It has no ``MODEL_TIERS`` entry, so outside a
    DeepSeek profile it does not escalate.
    """
    if provider in ("anthropic", "deepseek"):
        return provider
    return "openai"


def _ladder_tiers(base_ref: ModelRef, settings: LlmSettings) -> dict[str, str]:
    """The ``{tier: model}`` map a base model climbs: the profile's, if it uses the provider."""
    if _provider_key(base_ref.provider) in settings.providers_in_use():
        return settings.tier_models()
    return MODEL_TIERS.get(_provider_key(base_ref.provider), {})


def escalation_ladder(
    stage: str, base_ref: ModelRef, *, settings: LlmSettings | None = None
) -> list[ModelRef]:
    """Ordered model refs to try for *stage*, starting at the base tier and climbing.

    Resolves the policy's ladder tiers to concrete models through the active profile
    (or, for a base on a provider the profile does not use, that provider's
    ``MODEL_TIERS``). Returns ``[base_ref]`` when the stage has no policy (i.e. no
    escalation), and de-duplicates so a base already at the top tier yields a
    single attempt.

    Args:
        stage: The stage's name in ``ESCALATION_POLICY``.
        base_ref: The stage's base model.
        settings: The settings whose active profile to climb; the committed ones with
            the profile active now when None. Stages pass the settings they were built
            with, so a run's ladder matches its base model's profile.

    Returns:
        The refs to try, in order.
    """
    policy = ESCALATION_POLICY.get(stage)
    if not policy:
        return [base_ref]

    tiers = _ladder_tiers(base_ref, settings if settings is not None else load_committed_settings())
    refs: list[ModelRef] = []
    seen: set[str] = set()
    for tier in policy.get("ladder", []):
        model_id = tiers.get(tier)
        if model_id and model_id not in seen:
            seen.add(model_id)
            refs.append(resolve_model_ref(model_id))
    return refs or [base_ref]


def is_escalation_trigger(stage: str, reason: str) -> bool:
    """Whether *reason* justifies climbing a rung for *stage* (per policy)."""
    policy = ESCALATION_POLICY.get(stage)
    return bool(policy) and reason in policy.get("triggers", [])


def emit_escalation(
    stage: str, from_ref: ModelRef, to_ref: ModelRef, reason: str, attempt: int
) -> None:
    """Record a ``model_escalation`` event on the active trace (audit.v_escalations)."""
    log_event(
        "model_escalation",
        stage=stage,
        reason=reason,
        attempt=attempt,
        from_model=from_ref.model,
        to_model=to_ref.model,
        from_tier=from_ref.tier,
        to_tier=to_ref.tier,
        provider=to_ref.provider,
    )
