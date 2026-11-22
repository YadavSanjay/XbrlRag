"""LLM provider profiles, model rows and the pricing card (docs/specs/llm-provider-profiles.md).

Model choice and prices are committed config, not Python constants:

- ``configs/llm/llm.yaml`` declares the providers (``openai``, ``anthropic``, ``deepseek``;
  only ``deepseek`` carries a ``base_url``), one row per model (provider, output-token
  ceiling, whether it takes ``temperature``, an optional Anthropic or DeepSeek ``effort``)
  and the *profiles*: each maps XbrlRag's three tiers (small / medium / frontier) to a
  model (docs/specs/deepseek-provider.md).
- ``configs/llm/pricing.yaml`` is the rate card, USD per 1,000 tokens, with optional cache
  rates and prompt-size tiers.

Both are validated strictly (an unknown key, a missing key or a wrong type raises
:class:`LlmConfigError`), and every model a profile uses must have a price, so no call can
be recorded at $0.

Which profile is active, first match wins: an explicit ``profile`` argument (a run's
``--llm-profile``), the expanded ``active_profile`` (``XBRLRAG_LLM_PROFILE``), the legacy
``XBRLRAG_USE_REPLACEMENT_MODELS`` switch (``openai-5.6``), then ``openai``.

This module imports nothing from :mod:`src.llm.config`, which reads its tables from here.
"""

from __future__ import annotations

import copy
import functools
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src import PROJECT_ROOT

LLM_CONFIG_DIR = Path(PROJECT_ROOT) / "configs" / "llm"
LLM_CONFIG_FILE = LLM_CONFIG_DIR / "llm.yaml"
PRICING_FILE = LLM_CONFIG_DIR / "pricing.yaml"

TIERS: tuple[str, ...] = ("small", "medium", "frontier")
DEFAULT_PROFILE = "openai-6.1"
LEGACY_REPLACEMENT_PROFILE = "openai-5.6"

# The tier each XBRL query stage runs on; src.llm.config re-exports it. The rationale for
# each choice is beside XBRL_QUERY_MODEL_TIERS in src/llm/config.py.
XBRL_STAGE_TIERS: dict[str, str] = {
    "query_decomposer": "medium",
    "intent_router": "medium",
    "plan_generator": "medium",
    "synthesizer": "medium",
    "faithfulness_verifier": "small",
    # Eval baseline arms (docs/specs/baseline-arms.md): same tier as the synthesizer, so
    # a comparison against XbrlRag is not a comparison of models.
    "closed_book": "medium",
    "naive_rag": "medium",
}

# The legacy model-set switch (docs/specs/model-set-switch.md).
REPLACEMENT_SWITCH_ENV = "XBRLRAG_USE_REPLACEMENT_MODELS"
SWITCH_ON_WORDS = frozenset({"1", "true", "yes", "on"})
SWITCH_OFF_WORDS = frozenset({"0", "false", "no", "off"})

# Providers whose model rows may set ``effort``: Anthropic sends it as output_config.effort,
# DeepSeek as reasoning_effort (docs/specs/deepseek-provider.md).
_EFFORT_PROVIDERS = frozenset({"anthropic", "deepseek"})

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class LlmConfigError(ValueError):
    """The LLM provider configuration is invalid or incomplete."""


# ---------------------------------------------------------------------------- schema

_STRICT = ConfigDict(extra="forbid", strict=True, frozen=True)


class _ProviderRow(BaseModel):
    model_config = _STRICT

    api_key_env: str
    base_url: str | None = None


class _ModelRow(BaseModel):
    model_config = _STRICT

    provider: str
    max_output_tokens: int = Field(gt=0)
    temperature: bool = True
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class _ProfileRow(BaseModel):
    model_config = _STRICT

    small: str
    medium: str
    frontier: str


class _LlmDoc(BaseModel):
    model_config = _STRICT

    active_profile: str
    providers: dict[Literal["openai", "anthropic", "deepseek"], _ProviderRow]
    models: dict[str, _ModelRow]
    profiles: dict[str, _ProfileRow]


class _RateTier(BaseModel):
    model_config = _STRICT

    above_prompt_tokens: int = Field(gt=0)
    input: float = Field(ge=0, allow_inf_nan=False)
    output: float = Field(ge=0, allow_inf_nan=False)
    cached_input: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class _RateRow(BaseModel):
    model_config = _STRICT

    input: float = Field(ge=0, allow_inf_nan=False)
    output: float = Field(ge=0, allow_inf_nan=False)
    cached_input: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tiers: list[_RateTier] | None = None


def _validated(model: type[BaseModel], data: Any, what: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise LlmConfigError(f"invalid {what}: {exc}") from exc


def _rate_dict(row: BaseModel) -> dict[str, Any]:
    """A rate row as a plain dict without its absent optional keys."""
    return row.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------- settings


class LlmSettings:
    """The validated LLM configuration with one profile active."""

    def __init__(
        self,
        *,
        active: str,
        providers: Mapping[str, _ProviderRow],
        models: Mapping[str, _ModelRow],
        profiles: Mapping[str, _ProfileRow],
        pricing: Mapping[str, dict[str, Any]],
    ) -> None:
        """Builds settings from already-validated parts; use :func:`load_llm_settings`.

        Args:
            active: The active profile's name (a key of *profiles*).
            providers: Provider name -> its row.
            models: Model ID -> its row.
            profiles: Profile name -> its tier row.
            pricing: Model ID -> its rate row as a plain dict.
        """
        self._active = active
        self._providers = dict(providers)
        self._models = dict(models)
        self._profiles = dict(profiles)
        self._pricing = {model: copy.deepcopy(row) for model, row in pricing.items()}

    @property
    def active_profile(self) -> str:
        """The name of the active profile."""
        return self._active

    @property
    def profile_names(self) -> list[str]:
        """Every configured profile name, sorted."""
        return sorted(self._profiles)

    def model_for_tier(self, tier: str) -> str:
        """Returns the active profile's model for a tier.

        Args:
            tier: ``small``, ``medium`` or ``frontier``.

        Returns:
            The model ID.

        Raises:
            LlmConfigError: If the tier is unknown.
        """
        if tier not in TIERS:
            raise LlmConfigError(f"unknown model tier {tier!r}; expected one of {TIERS}")
        return str(getattr(self._profiles[self._active], tier))

    def tier_models(self) -> dict[str, str]:
        """The active profile's ``{tier: model}`` map, as a fresh dict."""
        return {tier: self.model_for_tier(tier) for tier in TIERS}

    def model_for_stage(self, stage: str) -> str:
        """Returns the active profile's model for an XBRL stage's tier.

        Args:
            stage: A stage in ``XBRL_STAGE_TIERS``.

        Returns:
            The model ID.

        Raises:
            LlmConfigError: If the stage is unknown.
        """
        tier = XBRL_STAGE_TIERS.get(stage)
        if tier is None:
            raise LlmConfigError(
                f"unknown XBRL stage {stage!r}; expected one of {sorted(XBRL_STAGE_TIERS)}"
            )
        return self.model_for_tier(tier)

    def stage_models(self) -> dict[str, str]:
        """The active profile's ``{stage: model}`` map for every stage, as a fresh dict."""
        return {stage: self.model_for_stage(stage) for stage in XBRL_STAGE_TIERS}

    def has_model(self, model: str) -> bool:
        """Whether *model* has a row in ``llm.yaml``."""
        return model in self._models

    def _row(self, model: str) -> _ModelRow:
        row = self._models.get(model)
        if row is None:
            raise LlmConfigError(f"model {model!r} is not configured in llm.yaml")
        return row

    def provider_for(self, model: str) -> str:
        """Returns the provider name of a configured model."""
        return self._row(model).provider

    def temperature_allowed(self, model: str) -> bool:
        """Whether the model accepts a ``temperature`` parameter."""
        return self._row(model).temperature

    def effort_for(self, model: str) -> str | None:
        """The configured effort level of a model, or None."""
        return self._row(model).effort

    def max_output_tokens_for(self, model: str) -> int:
        """The configured output-token ceiling of a model."""
        return self._row(model).max_output_tokens

    def api_key_env_for(self, provider: str) -> str:
        """The environment variable holding *provider*'s key.

        Raises:
            LlmConfigError: If the provider is not declared.
        """
        row = self._providers.get(provider)
        if row is None:
            raise LlmConfigError(f"provider {provider!r} is not declared in llm.yaml")
        return row.api_key_env

    def base_url_for(self, provider: str) -> str | None:
        """The configured ``base_url`` of *provider*, or None when it has none.

        Raises:
            LlmConfigError: If the provider is not declared.
        """
        row = self._providers.get(provider)
        if row is None:
            raise LlmConfigError(f"provider {provider!r} is not declared in llm.yaml")
        return row.base_url

    def rates_for(self, model: str) -> dict[str, Any]:
        """The pricing row of a model, in USD per 1,000 tokens.

        Raises:
            LlmConfigError: If the model has no pricing row.
        """
        row = self._pricing.get(model)
        if row is None:
            raise LlmConfigError(f"model {model!r} has no row in pricing.yaml")
        return copy.deepcopy(row)

    def pricing_table(self) -> dict[str, dict[str, Any]]:
        """Every pricing row, model ID -> rates, as a fresh dict."""
        return copy.deepcopy(self._pricing)

    def providers_in_use(self) -> set[str]:
        """The providers the active profile's models belong to."""
        return {self.provider_for(model) for model in self.tier_models().values()}


# ---------------------------------------------------------------------------- loading


def expand_placeholders(text: str, environ: Mapping[str, str]) -> str:
    """Expands ``${VAR}`` and ``${VAR:-default}`` in *text* from *environ*.

    ``${VAR:-default}`` takes the default when the variable is unset or empty, as in a
    POSIX shell.

    Args:
        text: The string to expand.
        environ: The environment.

    Returns:
        The expanded string.

    Raises:
        LlmConfigError: If a ``${VAR}`` without a default names an unset variable.
    """

    def substitute(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = environ.get(name)
        if default is not None:
            return value if value else default
        if value is None:
            raise LlmConfigError(f"${{{name}}} is not set and has no default")
        return value

    return _PLACEHOLDER.sub(substitute, text)


def legacy_switch_enabled(environ: Mapping[str, str], default: bool = False) -> bool:
    """Reads the legacy ``XBRLRAG_USE_REPLACEMENT_MODELS`` switch.

    Args:
        environ: The environment to read.
        default: The value when the variable is unset.

    Returns:
        True when the switch is on.

    Raises:
        LlmConfigError: If the value is not a recognised on/off word.
    """
    raw = environ.get(REPLACEMENT_SWITCH_ENV)
    if raw is None:
        return default
    word = raw.strip().lower()
    if word in SWITCH_ON_WORDS:
        return True
    if word in SWITCH_OFF_WORDS:
        return False
    raise LlmConfigError(
        f"{REPLACEMENT_SWITCH_ENV}={raw!r} is not an on/off value; use one of "
        f"{sorted(SWITCH_ON_WORDS | SWITCH_OFF_WORDS)}"
    )


def _check_references(doc: _LlmDoc, pricing: Mapping[str, Any]) -> None:
    """The cross-field rules a per-field schema cannot express."""
    for provider_name, provider_row in doc.providers.items():
        url = provider_row.base_url
        if provider_name == "deepseek":
            if url is None or not url.startswith("https://"):
                raise LlmConfigError(
                    f"provider {provider_name!r} needs an https base_url, got {url!r}"
                )
        elif url is not None:
            raise LlmConfigError(
                f"provider {provider_name!r} takes no base_url (its SDK default applies)"
            )
    for model, row in doc.models.items():
        if row.provider not in doc.providers:
            raise LlmConfigError(
                f"model {model!r} names provider {row.provider!r}, which is not declared"
            )
        if row.effort is not None and row.provider not in _EFFORT_PROVIDERS:
            raise LlmConfigError(
                f"model {model!r}: effort is allowed only on {sorted(_EFFORT_PROVIDERS)} models"
            )
    for name, profile in doc.profiles.items():
        for tier in TIERS:
            model = getattr(profile, tier)
            if model not in doc.models:
                raise LlmConfigError(
                    f"profile {name!r} tier {tier!r} names model {model!r}, which is not "
                    "in models"
                )
            if model not in pricing:
                raise LlmConfigError(
                    f"profile {name!r} uses model {model!r}, which has no pricing row"
                )


def _choose_profile(
    doc: _LlmDoc, profile: str | None, environ: Mapping[str, str]
) -> str:
    """Requirement 3's precedence: argument, active_profile, legacy switch, openai."""
    if profile is not None:
        chosen = profile
    else:
        expanded = expand_placeholders(doc.active_profile, environ).strip()
        if expanded:
            chosen = expanded
        elif legacy_switch_enabled(environ):
            chosen = LEGACY_REPLACEMENT_PROFILE
        else:
            chosen = DEFAULT_PROFILE
    if chosen not in doc.profiles:
        raise LlmConfigError(
            f"unknown LLM profile {chosen!r}; configured profiles: {sorted(doc.profiles)}"
        )
    return chosen


def load_llm_settings(
    doc: Mapping[str, Any],
    pricing: Mapping[str, Any],
    *,
    profile: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LlmSettings:
    """Validates the model and pricing documents and selects the active profile.

    Args:
        doc: The parsed ``llm.yaml`` document.
        pricing: The parsed ``pricing.yaml`` document.
        profile: An explicit profile name, which wins over the environment.
        environ: The environment to expand placeholders from; ``os.environ`` when None.

    Returns:
        The settings with the chosen profile active.

    Raises:
        LlmConfigError: If either document is invalid or the profile is unknown.
    """
    env = os.environ if environ is None else environ
    if not isinstance(doc, Mapping):
        raise LlmConfigError("llm.yaml must be a mapping")
    if not isinstance(pricing, Mapping):
        raise LlmConfigError("pricing.yaml must be a mapping")
    parsed = _validated(_LlmDoc, dict(doc), "llm.yaml")
    rates: dict[str, dict[str, Any]] = {}
    for model, row in pricing.items():
        if not isinstance(model, str):
            raise LlmConfigError(f"pricing.yaml: model key {model!r} is not a string")
        rates[model] = _rate_dict(_validated(_RateRow, row, f"pricing row {model!r}"))
    _check_references(parsed, rates)
    active = _choose_profile(parsed, profile, env)
    return LlmSettings(
        active=active,
        providers=parsed.providers,
        models=parsed.models,
        profiles=parsed.profiles,
        pricing=rates,
    )


@functools.lru_cache(maxsize=4)
def _read_yaml(path: str, mtime_ns: int) -> Any:
    """Parses a YAML file; cached per (path, modification time)."""
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _committed_doc(path: Path) -> Any:
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise LlmConfigError(f"LLM config file {path} is missing") from exc
    # A deep copy, so no caller can mutate the cached document.
    return copy.deepcopy(_read_yaml(str(path), mtime))


def load_committed_settings(
    *,
    profile: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LlmSettings:
    """Loads ``configs/llm/llm.yaml`` and ``configs/llm/pricing.yaml``.

    Args:
        profile: An explicit profile name, which wins over the environment.
        environ: The environment to read; ``os.environ`` when None.

    Returns:
        The committed settings with the chosen profile active.
    """
    return load_llm_settings(
        _committed_doc(LLM_CONFIG_FILE),
        _committed_doc(PRICING_FILE),
        profile=profile,
        environ=environ,
    )


# ---------------------------------------------------------------------------- cost


def cost_usd(
    rates: Mapping[str, Any],
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """The USD cost of one call.

    Args:
        rates: A pricing row, in USD per 1,000 tokens.
        prompt_tokens: All prompt tokens, cached and written ones included.
        completion_tokens: Output tokens.
        cached_prompt_tokens: Prompt tokens read from the cache.
        cache_write_tokens: Prompt tokens written to the cache.

    Returns:
        The cost in USD.

    Raises:
        ValueError: If a count is negative or cached plus written exceed the prompt.
    """
    counts = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
        "cache_write_tokens": cache_write_tokens,
    }
    for name, value in counts.items():
        if value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")
    if cached_prompt_tokens + cache_write_tokens > prompt_tokens:
        raise ValueError(
            f"cached ({cached_prompt_tokens}) + written ({cache_write_tokens}) tokens exceed "
            f"the prompt ({prompt_tokens})"
        )
    applied: Mapping[str, Any] = rates
    best = -1
    for tier in rates.get("tiers") or ():
        threshold = int(tier["above_prompt_tokens"])
        if best < threshold < prompt_tokens:
            applied, best = tier, threshold
    input_rate = float(applied["input"])
    cached_rate = applied.get("cached_input")
    write_rate = applied.get("cache_write")
    cached_rate = input_rate if cached_rate is None else float(cached_rate)
    write_rate = input_rate if write_rate is None else float(write_rate)
    uncached = prompt_tokens - cached_prompt_tokens - cache_write_tokens
    total = (
        uncached * input_rate
        + cached_prompt_tokens * cached_rate
        + cache_write_tokens * write_rate
        + completion_tokens * float(applied["output"])
    )
    cost = total / 1000
    if not math.isfinite(cost):
        raise ValueError(f"non-finite cost from rates {dict(rates)!r}")
    return cost


# ---------------------------------------------------------------------------- keys


def api_key(env_var: str, environ: Mapping[str, str] | None = None) -> str:
    """Returns a provider key from the variable or from the file ``<env_var>_FILE`` names.

    Args:
        env_var: The key's environment variable.
        environ: The environment to read; ``os.environ`` when None.

    Returns:
        The key.

    Raises:
        LlmConfigError: If neither the variable nor its file is set.
    """
    env = os.environ if environ is None else environ
    value = env.get(env_var)
    if value:
        return value
    file_var = f"{env_var}_FILE"
    path = env.get(file_var)
    if path:
        try:
            key = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise LlmConfigError(f"{file_var} names an unreadable file: {exc}") from exc
        if key:
            return key
        raise LlmConfigError(f"{file_var} names an empty file")
    raise LlmConfigError(f"no API key: set {env_var} or {file_var}")


def _has_key_source(env_var: str, environ: Mapping[str, str]) -> bool:
    return bool(environ.get(env_var)) or bool(environ.get(f"{env_var}_FILE"))


def missing_api_keys(
    settings: LlmSettings, environ: Mapping[str, str] | None = None
) -> list[str]:
    """Lists the key variables missing for the providers the active profile uses.

    Args:
        settings: The loaded settings.
        environ: The environment to read; ``os.environ`` when None.

    Returns:
        The names of the variables with neither a value nor a ``_FILE``.
    """
    env = os.environ if environ is None else environ
    names = {settings.api_key_env_for(p) for p in settings.providers_in_use()}
    return sorted(name for name in names if not _has_key_source(name, env))
