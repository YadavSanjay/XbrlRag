"""Provider-neutral model reference resolution for the LLM gateway.

Design: docs/design/XbrlRag_LLM_Harness.md.

The XBRL stages historically passed a bare model-id string (e.g. ``"gpt-5-mini"``)
into the OpenAI-bound client. To make the runtime provider-neutral and to let
telemetry slice by *provider* and *model series* (not just the concrete id), a
call now carries a :class:`ModelRef` — the concrete model id plus the
provider / series / capability-tier it belongs to, resolved once from the
config tier maps in :mod:`src.llm.config`.

``resolve_model_ref`` is deliberately pure/deterministic and does **no** network
or SDK work: it only reads the static config maps, so it is safe to call at
component-construction time.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from src.llm.config import (
    ANTHROPIC_MODEL_ALIASES,
    ANTHROPIC_MODEL_CONFIGS,
    MODEL_TIERS,
    SEC_TABLE_MODEL_CONFIGS,
)
from src.llm.profiles import TIERS, LlmSettings, load_committed_settings

# The committed model rows (configs/llm/llm.yaml), read once: a model's provider,
# output-token ceiling and temperature support do not depend on the active profile.
_MODEL_ROWS: LlmSettings = load_committed_settings(environ={})

# Reverse index: concrete model-id -> (provider, capability-tier), built from
# MODEL_TIERS and then every committed profile, so the two never drift. Only models
# named in a tier map resolve to a tier; anything else (e.g. gpt-4o) gets tier=None.
_TIER_BY_MODEL: dict[str, tuple[str, str]] = {}
for _prov, _tiers in MODEL_TIERS.items():
    for _tier, _mid in _tiers.items():
        _TIER_BY_MODEL[_mid] = (_prov, _tier)
for _profile in _MODEL_ROWS.profile_names:
    for _tier, _mid in load_committed_settings(profile=_profile, environ={}).tier_models().items():
        _TIER_BY_MODEL.setdefault(_mid, (_MODEL_ROWS.provider_for(_mid), _tier))


def infer_provider(model: str) -> str:
    """Best-effort provider inference from a concrete model id."""
    m = model.lower()
    if m in ANTHROPIC_MODEL_ALIASES or m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt", "text-", "davinci")):
        return "openai"
    # HuggingFace ids look like "org/model"; treat the rest as OpenAI-compatible.
    if "/" in model:
        return "huggingface"
    return "openai"


def derive_series(model: str, provider: str) -> str:
    """Collapse a concrete model id to its *series* for telemetry grouping.

    e.g. gpt-5 / gpt-5-mini / gpt-5-nano -> ``"gpt-5"``; gpt-4o / gpt-4o-mini ->
    ``"gpt-4o"``; claude-opus-4-8 / claude-sonnet-4-6 / claude-haiku-4-5-* ->
    ``"claude-4"`` (the family major version). Grouping by series is what makes
    "run the old prompt against the new model *series*" a queryable dimension.
    """
    m = model.lower()
    if provider in ("openai", "openai_compat"):
        mt = re.match(r"(gpt-\d+o?|o\d+)", m)
        return mt.group(1) if mt else m
    if provider == "anthropic":
        mt = re.search(r"claude-[a-z]+-(\d+)", m)
        if mt:
            return f"claude-{mt.group(1)}"
        return m
    return m


@dataclass(frozen=True)
class ModelRef:
    """A resolved, provider-tagged reference to a concrete model.

    Attributes:
        model: The concrete model id passed to the provider SDK (e.g. "gpt-5-mini").
        provider: "openai" | "openai_compat" | "anthropic" | "deepseek" | "huggingface".
        series: Telemetry grouping key (e.g. "gpt-5", "claude-4").
        tier: Capability tier ("small"/"medium"/"frontier") when the model is
            named in MODEL_TIERS, else None.
        config: Per-call config (max tokens / temperature) from the provider's
            *_MODEL_CONFIGS map, plus ``max_output_tokens`` and ``temperature_allowed``
            for a model configured in ``configs/llm/llm.yaml``; may be empty for
            unlisted models. Read it through :func:`call_params`.
        base_url: Optional OpenAI-compatible endpoint override.
    """

    model: str
    provider: str
    series: str
    tier: str | None = None
    config: dict = field(default_factory=dict)
    base_url: str | None = None


def resolve_model_ref(
    model: str | ModelRef,
    *,
    provider: str | None = None,
    base_url: str | None = None,
) -> ModelRef:
    """Resolve a model id (or tier alias, or an existing ModelRef) to a ModelRef.

    Pure/deterministic — reads only static config maps. Anthropic tier aliases
    ("haiku"/"sonnet"/"opus") are resolved to their concrete id. A ``base_url``
    tags the ref as the ``openai_compat`` provider so the gateway routes it to an
    OpenAI-compatible endpoint adapter.
    """
    if isinstance(model, ModelRef):
        return model

    model_id = model
    if model in ANTHROPIC_MODEL_ALIASES:
        model_id = ANTHROPIC_MODEL_ALIASES[model]
        provider = provider or "anthropic"

    # A model llm.yaml puts on deepseek routes there with its provider's base_url, unless
    # the caller names a provider or an endpoint itself (docs/specs/deepseek-provider.md).
    if provider is None and _MODEL_ROWS.has_model(model_id) and (
        _MODEL_ROWS.provider_for(model_id) == "deepseek"
    ):
        provider = "deepseek"
        base_url = base_url or _MODEL_ROWS.base_url_for("deepseek")

    prov = provider or infer_provider(model_id)
    if base_url and prov in ("openai", "openai_compat"):
        prov = "openai_compat"

    series = derive_series(model_id, prov)
    tier = _TIER_BY_MODEL.get(model_id, (None, None))[1]

    if prov in ("openai", "openai_compat"):
        config = SEC_TABLE_MODEL_CONFIGS.get(model_id, {})
    elif prov == "anthropic":
        alias = next((a for a, mid in ANTHROPIC_MODEL_ALIASES.items() if mid == model_id), None)
        config = ANTHROPIC_MODEL_CONFIGS.get(alias, {}) if alias else {}
    else:
        config = {}
    config = dict(config)
    if _MODEL_ROWS.has_model(model_id):
        config["max_output_tokens"] = _MODEL_ROWS.max_output_tokens_for(model_id)
        config["temperature_allowed"] = _MODEL_ROWS.temperature_allowed(model_id)

    return ModelRef(
        model=model_id,
        provider=prov,
        series=series,
        tier=tier,
        config=dict(config),
        base_url=base_url,
    )


def call_params(
    config: Mapping[str, Any], *, max_completion_tokens: int, temperature: float
) -> dict[str, Any]:
    """The output-token budget and temperature of one call on a model.

    A model's own per-call config wins (``SEC_TABLE_MODEL_CONFIGS`` for the OpenAI models,
    so their calls are unchanged). Otherwise the caller's defaults apply, the token budget
    capped at the model's ``max_output_tokens``. A model configured with
    ``temperature: false`` gets ``temperature=None``, which every adapter omits.

    Args:
        config: A :class:`ModelRef`'s ``config``.
        max_completion_tokens: The caller's default output-token budget.
        temperature: The caller's default temperature.

    Returns:
        ``{"max_completion_tokens": int, "temperature": float | None}``.
    """
    budget = config.get("max_completion_tokens")
    if budget is None:
        ceiling = config.get("max_output_tokens")
        budget = max_completion_tokens if ceiling is None else min(max_completion_tokens, ceiling)
    chosen: float | None = None
    if config.get("temperature_allowed", True):
        chosen = config.get("temperature", temperature)
    return {"max_completion_tokens": int(budget), "temperature": chosen}


# The output budget of a stage call on a model with neither a budget of its own nor a
# ceiling in configs/llm/llm.yaml: bounded, and the same as the baseline arms'
# (docs/specs/stage-llm-output-cap.md).
DEFAULT_STAGE_MAX_COMPLETION_TOKENS = 8000


def stage_call_params(config: Mapping[str, Any], *, temperature: float) -> dict[str, Any]:
    """The output-token budget and temperature of an XBRL pipeline stage call.

    A reasoning model spends its reasoning against the output budget, so a stage asks for
    the model's whole configured ceiling (``max_output_tokens`` in ``llm.yaml``) rather
    than a fixed per-stage number: a fixed 2,000 left DeepSeek replies cut off with no
    content, and the escalation rung, asked for the same 2,000, failed the same way. The
    ceiling is a cap, not a target, so a reply that finishes early is billed as before;
    the run budget still applies to every call. A model's own budget still wins, and a
    model with neither gets :data:`DEFAULT_STAGE_MAX_COMPLETION_TOKENS`, so the budget is
    never unbounded.

    Args:
        config: A :class:`ModelRef`'s ``config``; read per attempt, so an escalated model
            gets its own ceiling.
        temperature: The stage's default temperature.

    Returns:
        ``{"max_completion_tokens": int, "temperature": float | None}``, as
        :func:`call_params`.
    """
    ceiling = config.get("max_output_tokens")
    budget = DEFAULT_STAGE_MAX_COMPLETION_TOKENS if ceiling is None else int(ceiling)
    return call_params(config, max_completion_tokens=budget, temperature=temperature)
