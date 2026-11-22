"""Configuration models for LLM wrappers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from src.llm.profiles import (
    DEFAULT_PROFILE,
    LEGACY_REPLACEMENT_PROFILE,
    REPLACEMENT_SWITCH_ENV,
    SWITCH_OFF_WORDS,
    SWITCH_ON_WORDS,
    XBRL_STAGE_TIERS,
    legacy_switch_enabled,
    load_committed_settings,
)


@dataclass
class LLMConfig:
    """Common configuration for LLM wrappers.

    Attributes:
        model: Model name/identifier
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens in response
        top_p: Nucleus sampling parameter
        frequency_penalty: Penalize frequent tokens
        presence_penalty: Penalize tokens already present
        retry_max_attempts: Max retry attempts for transient errors
        retry_min_wait: Min wait between retries (seconds)
        retry_max_wait: Max wait between retries (seconds)
        rate_limit_rpm: Requests per minute limit (None = no limit)
        rate_limit_tpm: Tokens per minute limit (None = no limit)
        timeout: Request timeout in seconds
    """

    model: str = "gpt-5-mini"
    temperature: float = 0.7
    max_tokens: int = 512
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    retry_max_attempts: int = 3
    retry_min_wait: float = 1.0
    retry_max_wait: float = 60.0
    rate_limit_rpm: int | None = None
    rate_limit_tpm: int | None = None
    timeout: int = 60


@dataclass
class OpenAIConfig(LLMConfig):
    """OpenAI-specific configuration."""

    model: str = "gpt-5-mini"
    api_key: str | None = None  # If None, reads from OPENAI_API_KEY env var
    organization: str | None = None


@dataclass
class AnthropicConfig(LLMConfig):
    """Anthropic-specific configuration.

    `model` accepts either a tier alias (`"haiku"`, `"sonnet"`, `"opus"`) — resolved
    via `ANTHROPIC_MODEL_ALIASES` — or a full Anthropic model id
    (e.g. `"claude-sonnet-4-6"`). Defaults to the `"sonnet"` tier.
    """

    model: str = "sonnet"
    api_key: str | None = None  # If None, reads from ANTHROPIC_API_KEY env var


@dataclass
class HuggingFaceConfig(LLMConfig):
    """HuggingFace-specific configuration."""

    model: str = "meta-llama/Llama-2-7b-chat-hf"
    api_key: str | None = None  # If None, reads from HUGGINGFACEHUB_API_TOKEN env var
    endpoint_url: str | None = None  # Custom inference endpoint
    task: str = "text-generation"


# Friendly tier aliases -> concrete Anthropic model ids.
# Keep in sync with the Claude model family available to this project.
ANTHROPIC_MODEL_ALIASES: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",  # Haiku 4.5 - cheapest/fastest tier
    "sonnet": "claude-sonnet-4-6",  # Sonnet 4.6 - default, balanced tier
    "opus": "claude-opus-4-8",  # Opus 4.8 - frontier tier
}


# Model prices, USD per 1K tokens, read from configs/llm/pricing.yaml (the single source
# of truth; docs/specs/llm-provider-profiles.md). Each row has ``input`` / ``output`` and
# optional ``cached_input`` / ``cache_write`` / ``tiers``; src.llm.profiles.cost_usd prices
# a call from a row. A model with no row cannot be costed, so the cost tracker refuses it.
_COMMITTED_SETTINGS = load_committed_settings()
MODEL_PRICING: dict[str, dict[str, Any]] = _COMMITTED_SETTINGS.pricing_table()


# Deprecated model ids - kept only as a reference for code that may still pass
# them in. New code must not pick these as defaults; MODEL_TIERS below points
# at the current replacements.
DEPRECATED_MODELS: dict[str, str] = {
    "gpt-4": "use 'gpt-5' (frontier) or 'gpt-4o' instead",
    "gpt-4-32k": "use 'gpt-5' instead (272K context)",
    "gpt-3.5-turbo": "use 'gpt-5-nano' or 'gpt-5-mini' instead",
    "gpt-3.5-turbo-16k": "use 'gpt-5-mini' instead",
    # Still the XBRL tiers until the migration lands; shut down 2026-12-11.
    "gpt-5": "shuts down 2026-12-11; OpenAI names 'gpt-5.6-sol' as the replacement",
    "gpt-5-mini": "shuts down 2026-12-11; OpenAI names 'gpt-5.6-terra' as the replacement",
    "gpt-5-nano": "shuts down 2026-12-11; OpenAI names 'gpt-5.6-luna' as the replacement",
}

# ---------------------------------------------------------------------------
# OpenAI model sets and the replacement-model feature switch
# (docs/specs/model-set-switch.md), now backed by the committed profiles in
# configs/llm/llm.yaml: the current set is the ``openai`` profile, the replacement set
# the ``openai-5.6`` profile. XBRLRAG_USE_REPLACEMENT_MODELS is the legacy way to pick the
# latter; XBRLRAG_LLM_PROFILE (or a run's --llm-profile) wins over it.
USE_REPLACEMENT_MODELS = False

_REPLACEMENT_SWITCH_ENV = REPLACEMENT_SWITCH_ENV
_SWITCH_ON_WORDS = SWITCH_ON_WORDS
_SWITCH_OFF_WORDS = SWITCH_OFF_WORDS

OPENAI_MODEL_SETS: dict[str, dict[str, str]] = {
    "current": load_committed_settings(profile=DEFAULT_PROFILE, environ={}).tier_models(),
    "replacement": load_committed_settings(
        profile=LEGACY_REPLACEMENT_PROFILE, environ={}
    ).tier_models(),
}


def openai_model_set(use_replacement: bool) -> dict[str, str]:
    """The OpenAI small / medium / frontier models of the current or replacement set.

    Args:
        use_replacement: True for the replacement set, False for the current one.

    Returns:
        A fresh ``{"small": ..., "medium": ..., "frontier": ...}`` dict.
    """
    return dict(OPENAI_MODEL_SETS["replacement" if use_replacement else "current"])


def xbrl_stage_models(use_replacement: bool) -> dict[str, str]:
    """The model each XBRL query stage runs on, for the current or replacement set.

    Args:
        use_replacement: True for the replacement set, False for the current one.

    Returns:
        A fresh ``{stage: model id}`` dict.
    """
    models = openai_model_set(use_replacement)
    return {stage: models[tier] for stage, tier in XBRL_STAGE_TIERS.items()}


def replacement_models_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the replacement model set is switched on.

    Args:
        env: Environment to read ``XBRLRAG_USE_REPLACEMENT_MODELS`` from; ``os.environ``
            when None. Without the key, the config default applies.

    Returns:
        True when the replacement set is on.

    Raises:
        ValueError: The environment value is not a recognised on/off word.
    """
    source = os.environ if env is None else env
    return legacy_switch_enabled(source, USE_REPLACEMENT_MODELS)


# Resolved once at import: MODEL_TIERS["openai"] follows the legacy switch, as before.
_USE_REPLACEMENT_MODELS = replacement_models_enabled()

# Use-case tiers: small (simple/cheap, high volume), medium (moderate
# complexity, e.g. routing/decomposition/plan generation), frontier
# (complex/cognitive tasks, e.g. synthesis over long contexts). The "anthropic" entry is
# the legacy PDF flow's alias tiers; the XBRL stages and escalation read the active
# profile instead (src.llm.profiles).
MODEL_TIERS: dict[str, dict[str, str]] = {
    "openai": openai_model_set(_USE_REPLACEMENT_MODELS),
    "anthropic": {
        "small": ANTHROPIC_MODEL_ALIASES["haiku"],
        "medium": ANTHROPIC_MODEL_ALIASES["sonnet"],
        "frontier": ANTHROPIC_MODEL_ALIASES["opus"],
    },
}

# ---------------------------------------------------------------------------
# XBRL end-to-end flow — model per query-pipeline stage.
#
# The LLM choices for src/retriever/xbrl/* (the XBRL/iXBRL hybrid query pipeline) live
# in configs/llm/llm.yaml (profiles x tiers); the rationale below is for the default
# ``openai`` profile. The XBRL *ingestion* path
# (src/pipelines/xbrl/load_to_vectordb_pipeline.py) is LLM-free — it uses BGE-M3
# embeddings only — so it has no entry here. This is independent of the legacy
# PDF flow's models (SEC_TABLE_MODEL_CONFIGS callers in the PDF path).
#
# Cost rationale (June 2026 list pricing, USD per 1M tokens, input/output):
#   gpt-5-nano  $0.05 / $0.40     | Haiku 4.5   $1.00 / $5.00
#   gpt-5-mini  $0.25 / $2.00     | Sonnet 4.6  $3.00 / $15.00
#   gpt-5       $1.25 / $10.00    | Opus 4.8    $5.00 / $25.00
# At every capability tier the OpenAI gpt-5 family is 10-20x cheaper than the
# comparable Anthropic tier, so the cost-optimal choice stays on gpt-5:
#   * small / high-volume classify+verify -> gpt-5-nano (cheapest capable model)
#   * structured reasoning + faithful synthesis -> gpt-5-mini
# Bump "synthesizer" to "gpt-5" only if a later performance pass shows the
# user-facing answer quality needs it (cost ~5x mini, still < Opus/Sonnet).
# NB: intent_router and query_decomposer are on gpt-5-mini (not -nano): routing
# and compound-query decomposition are the accuracy-critical triage steps — a
# misroute or a missed decomposition sends the whole query down the wrong branch.
# faithfulness_verifier stays on -nano but runs at reasoning_effort="low" in
# bounded batches (see llm_xbrl_faithfulness_verifier.py) so it can't exhaust its
# token budget on reasoning.
# The stage -> tier assignment is XBRL_STAGE_TIERS (src.llm.profiles). The models come
# from the active profile (configs/llm/llm.yaml), resolved here once at import; the stages
# themselves re-read the profile when they are constructed (src.llm.stage_models).
XBRL_QUERY_MODEL_TIERS: dict[str, str] = _COMMITTED_SETTINGS.stage_models()


# ---------------------------------------------------------------------------
# Model-escalation policy (docs/design/XbrlRag_LLM_Harness.md).
#
# When a stage's cheap model deteriorates on a *detectable* quality signal, the
# stage retries the same request one rung up the active profile's tier ladder
# and records a `model_escalation` event (queryable via audit.v_escalations), so
# we can study over time which stages/models/prompt-versions stop holding up and
# adjust XBRL_QUERY_MODEL_TIERS accordingly.
#
# `ladder`: ordered tiers to try, resolved to concrete models through the active
#   profile (src.llm.escalation) — the first tier is the base tier, subsequent tiers are the
#   climb. `triggers`: the reasons that justify climbing (a non-trigger failure
#   degrades in place instead of escalating).
#
# Wired: all three stages below — synthesizer, faithfulness_verifier, and
# plan_generator (which escalates only after its local validator-retry loop still
# ends in `plan_validation_failed`). `low_confidence` is reserved for a future
# verifier confidence signal and is intentionally not listed until that signal exists.
ESCALATION_POLICY = {
    "synthesizer": {
        "ladder": ["medium", "frontier"],
        "triggers": ["parse_error", "empty_content"],
    },
    "plan_generator": {
        "ladder": ["medium", "frontier"],
        "triggers": ["plan_validation_failed"],
    },
    "faithfulness_verifier": {
        "ladder": ["small", "medium"],
        "triggers": ["parse_error"],
    },
}


# OpenAI model configurations optimized for SEC table/page analysis
SEC_TABLE_MODEL_CONFIGS = {
    "gpt-5": {
        "model": "gpt-5",
        "max_completion_tokens": 8000,
        "temperature": 1,
    },
    "gpt-5-mini": {
        "model": "gpt-5-mini",
        "max_completion_tokens": 8000,
        "temperature": 1,
    },
    "gpt-5-nano": {
        "model": "gpt-5-nano",
        "max_completion_tokens": 4000,
        "temperature": 1,
    },
    # Migration candidates: same shape as the gpt-5 family (reasoning models,
    # temperature fixed at 1). Not used by any stage until XBRL_QUERY_MODEL_TIERS
    # switches; confirm the parameters with a smoke call first.
    "gpt-5.6-sol": {"model": "gpt-5.6-sol", "max_completion_tokens": 8000, "temperature": 1},
    "gpt-5.6-terra": {"model": "gpt-5.6-terra", "max_completion_tokens": 8000, "temperature": 1},
    "gpt-5.6-luna": {"model": "gpt-5.6-luna", "max_completion_tokens": 4000, "temperature": 1},
    "gpt-6-sol": {"model": "gpt-6-sol", "max_completion_tokens": 8000, "temperature": 1},
    "gpt-6-luna": {"model": "gpt-6-luna", "max_completion_tokens": 4000, "temperature": 1},
    # The openai-6.1 profile (docs/specs/openai-6-1-profile.md), same shape.
    "gpt-6.1-sol": {"model": "gpt-6.1-sol", "max_completion_tokens": 8000, "temperature": 1},
    "gpt-6.1-terra": {"model": "gpt-6.1-terra", "max_completion_tokens": 8000, "temperature": 1},
    "gpt-4o-mini": {
        "model": "gpt-4o-mini",
        "max_tokens": 5000,
        "temperature": 0.1,
        "detail": "high",
    },
    "gpt-4o": {
        "model": "gpt-4o",
        "max_tokens": 2000,
        "temperature": 0.1,  # Low temp for factual extraction
        "detail": "high",  # High detail for table analysis
    },
}

# Anthropic model configurations, keyed by the same tier aliases as
# ANTHROPIC_MODEL_ALIASES, for callers that want SEC_TABLE_MODEL_CONFIGS-style
# per-call overrides (max_tokens/temperature) instead of just a model id.
ANTHROPIC_MODEL_CONFIGS = {
    "opus": {
        "model": ANTHROPIC_MODEL_ALIASES["opus"],
        "max_tokens": 8000,
        "temperature": 0.2,
    },
    "sonnet": {
        "model": ANTHROPIC_MODEL_ALIASES["sonnet"],
        "max_tokens": 8000,
        "temperature": 0.2,
    },
    "haiku": {
        "model": ANTHROPIC_MODEL_ALIASES["haiku"],
        "max_tokens": 4000,
        "temperature": 0.1,
    },
}
