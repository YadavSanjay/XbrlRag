"""C2: the committed openai-6.1 profile, its model rows and their per-call parameters.

Oracle: requirement 2 (measurement plan decision 1: gpt-6-luna small, gpt-6.1-terra medium,
gpt-6.1-sol frontier), copied by hand into conftest.PROFILE_61 and conftest.CEILINGS_61.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.model_ref import call_params, resolve_model_ref
from src.llm.profiles import XBRL_STAGE_TIERS, load_committed_settings, missing_api_keys

PROFILE = "openai-6.1"


def _settings_61():
    names = load_committed_settings(environ={}).profile_names
    assert PROFILE in names, f"no {PROFILE} profile in the committed llm.yaml: {names}"
    return load_committed_settings(profile=PROFILE, environ={})


@pytest.mark.criterion("C2")
def test_profile_tiers_rows_and_provider(spec):
    # oracle: requirement 2 — the tier models, all on openai, ceilings 4000 / 8000 / 8000,
    # no effort, temperature allowed
    settings = _settings_61()
    assert settings.active_profile == PROFILE
    assert settings.tier_models() == spec.profile_61
    assert settings.providers_in_use() == {"openai"}
    for model, ceiling in spec.ceilings_61.items():
        assert settings.provider_for(model) == "openai", model
        assert settings.max_output_tokens_for(model) == ceiling, model
        assert settings.effort_for(model) is None, model
        assert settings.temperature_allowed(model) is True, model
    assert missing_api_keys(settings, environ={}) == ["OPENAI_API_KEY"]


@pytest.mark.criterion("C2")
def test_other_profiles_and_the_default_are_unchanged(spec):
    # oracle: requirement 2 — openai stays the default with the gpt-5 family; the other
    # profiles keep their models (the default is not repointed by this change)
    _settings_61()
    default = load_committed_settings(environ={})
    # This change left openai the default; arm-model-from-profile later makes openai-6.1
    # the default (the gpt-5 family shuts down 2026-12-11) and pins it in its own C3.
    assert default.active_profile in ("openai", "openai-6.1")
    for name, row in spec.unchanged_profiles.items():
        assert load_committed_settings(profile=name, environ={}).tier_models() == row, name


@pytest.mark.criterion("C2")
def test_call_params_give_each_model_its_ceiling_and_temperature_one(spec):
    # oracle: requirement 2 — like the other OpenAI reasoning models, each openai-6.1 model's
    # own per-call config wins: budget = its max_output_tokens, temperature 1
    _settings_61()
    for model, ceiling in spec.ceilings_61.items():
        ref = resolve_model_ref(model)
        assert ref.provider == "openai", model
        got = call_params(ref.config, max_completion_tokens=16_000, temperature=0.0)
        assert got == {"max_completion_tokens": ceiling, "temperature": 1}, model


@pytest.mark.criterion("C2")
@given(stage=st.sampled_from(sorted(XBRL_STAGE_TIERS)))
def test_every_stage_gets_the_profiles_model_for_its_tier(spec, stage):
    # oracle: requirement 2's table indexed by the stage's tier in XBRL_STAGE_TIERS
    settings = _settings_61()
    assert settings.model_for_stage(stage) == spec.profile_61[XBRL_STAGE_TIERS[stage]]


@pytest.mark.criterion("C2")
@given(model=st.sampled_from(["gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol"]),
       budget=st.integers(min_value=1, max_value=200_000),
       temperature=st.floats(min_value=0.0, max_value=2.0))
def test_any_caller_default_gives_the_models_own_parameters(spec, model, budget, temperature):
    # oracle: requirement 2 — the model's own config wins over any caller default
    _settings_61()
    got = call_params(resolve_model_ref(model).config, max_completion_tokens=budget,
                      temperature=temperature)
    assert got == {"max_completion_tokens": spec.ceilings_61[model], "temperature": 1}
