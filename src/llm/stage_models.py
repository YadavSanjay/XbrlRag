"""Model and API key of an XBRL stage, read from the active LLM profile when it is built.

The five query stages and the eval arms' ``StageLLM`` call :func:`resolve_stage` from their
constructors, so the profile (``XBRLRAG_LLM_PROFILE`` or a run's ``--llm-profile``) is
read when a stage is constructed, not only when :mod:`src.llm.config` is imported.

Spec: [docs/specs/llm-provider-profiles.md](../../docs/specs/llm-provider-profiles.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.llm.gateway.model_ref import infer_provider
from src.llm.profiles import LlmSettings, api_key, load_committed_settings

# Key variables for a model outside configs/llm/llm.yaml (an explicit override such as
# gpt-4o), by the provider its ID implies.
_FALLBACK_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY"}
_DEFAULT_KEY_ENV = "OPENAI_API_KEY"


@dataclass(frozen=True)
class StageModel:
    """The model one stage runs on and the settings it was chosen from.

    Attributes:
        model: The concrete model ID.
        settings: The committed settings with the profile active at construction; the
            stage's escalation ladder climbs this profile's tiers.
    """

    model: str
    settings: LlmSettings


def resolve_stage(
    stage: str, model: str | None = None, *, settings: LlmSettings | None = None
) -> StageModel:
    """Chooses a stage's model: an explicit *model*, else the active profile's.

    Args:
        stage: The stage's name in ``XBRL_STAGE_TIERS`` (needed only without *model*).
        model: An explicit model ID, which wins.
        settings: Settings to choose from; the committed ones, with the profile active
            now, when None.

    Returns:
        The stage's model and the settings it came from.

    Raises:
        LlmConfigError: If the profile or (without *model*) the stage is unknown.
    """
    chosen = settings if settings is not None else load_committed_settings()
    return StageModel(model=model or chosen.model_for_stage(stage), settings=chosen)


def frontier_model(settings: LlmSettings | None = None) -> str:
    """The active profile's frontier model: the long-context and tool arms' default.

    Read when an arm is built, like :func:`resolve_stage`, so a run's ``--llm-profile``
    (``XBRLRAG_LLM_PROFILE``) chooses it.

    Spec: [docs/specs/arm-model-from-profile.md](../../docs/specs/arm-model-from-profile.md).

    Args:
        settings: Settings to choose from; the committed ones, with the profile active
            now, when None.

    Returns:
        The model ID of the profile's ``frontier`` tier.

    Raises:
        LlmConfigError: If the active profile is unknown.
    """
    chosen = settings if settings is not None else load_committed_settings()
    return chosen.model_for_tier("frontier")


def key_env_for(model: str, settings: LlmSettings) -> str:
    """The environment variable holding the key for *model*'s provider."""
    if settings.has_model(model):
        return settings.api_key_env_for(settings.provider_for(model))
    return _FALLBACK_KEY_ENV.get(infer_provider(model), _DEFAULT_KEY_ENV)


def require_api_key(model: str, settings: LlmSettings, explicit: str | None = None) -> str:
    """The API key a stage on *model* will use: *explicit*, else its provider's key.

    Args:
        model: The stage's model.
        settings: The settings the model was chosen from.
        explicit: A key the caller passed to the stage.

    Returns:
        The key.

    Raises:
        LlmConfigError: (a ``ValueError``) If no key is set for the model's provider.
    """
    if explicit:
        return explicit
    return api_key(key_env_for(model, settings))
