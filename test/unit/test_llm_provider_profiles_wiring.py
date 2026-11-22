"""Unit tests for the llm-provider-profiles wiring the spec tests do not cover.

- the OpenAI adapter's cache-write usage parsing;
- escalation following the active profile;
- ``call_params`` (token budget and temperature per model);
- a stage's provider-aware key;
- every model a legacy (PDF-flow) caller can pick is priced, so the cost tracker's
  refusal of unpriced models cannot break those paths.

Spec: docs/specs/llm-provider-profiles.md. No network: adapters parse hand-built usage
blocks, stages are only constructed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.llm.config import (
    ANTHROPIC_MODEL_ALIASES,
    MODEL_PRICING,
    SEC_TABLE_MODEL_CONFIGS,
    HuggingFaceConfig,
    LLMConfig,
    OpenAIConfig,
)
from src.llm.escalation import escalation_ladder
from src.llm.gateway.adapters import OpenAIAdapter
from src.llm.gateway.model_ref import call_params, resolve_model_ref
from src.llm.profiles import LlmConfigError, load_committed_settings
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer

_PROFILE_ENV = ("XBRLRAG_LLM_PROFILE", "XBRLRAG_USE_REPLACEMENT_MODELS")
_KEY_ENV = ("OPENAI_API_KEY", "OPENAI_API_KEY_FILE", "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_FILE")


@pytest.fixture
def clean_env(monkeypatch):
    """No profile, switch or key variables; returns ``monkeypatch``."""
    for name in (*_PROFILE_ENV, *_KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _openai_raw(usage):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}"))],
        usage=usage,
    )


def _usage(prompt, completion, details=None, **extra):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        completion_tokens_details=None,
        prompt_tokens_details=details,
        **extra,
    )


# ---------------------------------------------------------------- OpenAI usage parsing


def test_openai_adapter_reads_cache_writes_from_prompt_details():
    details = SimpleNamespace(cached_tokens=300, cache_write_tokens=200)
    parsed = OpenAIAdapter(api_key="sk-unit").parse(_openai_raw(_usage(1000, 10, details)),
                                                    stage="unit")
    assert parsed.prompt_tokens == 1000
    assert parsed.cached_prompt_tokens == 300
    assert parsed.cache_write_tokens == 200


def test_openai_adapter_falls_back_to_a_top_level_field():
    details = SimpleNamespace(cached_tokens=0)
    raw = _openai_raw(_usage(1000, 10, details, cache_write_tokens=150))
    assert OpenAIAdapter(api_key="sk-unit").parse(raw, stage="unit").cache_write_tokens == 150


def test_openai_adapter_reports_zero_when_absent():
    # The gpt-5 family reports cached tokens only.
    details = SimpleNamespace(cached_tokens=40)
    parsed = OpenAIAdapter(api_key="sk-unit").parse(_openai_raw(_usage(100, 5, details)),
                                                    stage="unit")
    assert parsed.cache_write_tokens == 0
    assert OpenAIAdapter(api_key="sk-unit").parse(
        _openai_raw(_usage(100, 5, None)), stage="unit"
    ).cache_write_tokens == 0


def test_openai_adapter_never_claims_more_written_than_the_prompt_holds():
    details = SimpleNamespace(cached_tokens=80, cache_write_tokens=50)
    parsed = OpenAIAdapter(api_key="sk-unit").parse(_openai_raw(_usage(100, 5, details)),
                                                    stage="unit")
    assert parsed.cached_prompt_tokens + parsed.cache_write_tokens == 100


# ---------------------------------------------------------------- escalation


def test_escalation_climbs_the_anthropic_profile(clean_env):
    settings = load_committed_settings(profile="anthropic")
    refs = escalation_ladder("synthesizer", resolve_model_ref("claude-sonnet-5-5"),
                             settings=settings)
    assert [r.model for r in refs] == ["claude-sonnet-5-5", "claude-opus-5-5"]
    refs = escalation_ladder("faithfulness_verifier", resolve_model_ref("claude-haiku-5-5"),
                             settings=settings)
    assert [r.model for r in refs] == ["claude-haiku-5-5", "claude-sonnet-5-5"]


def test_escalation_follows_the_profile_in_the_environment(clean_env):
    clean_env.setenv("XBRLRAG_LLM_PROFILE", "openai-5.6")
    refs = escalation_ladder("plan_generator", resolve_model_ref("gpt-5.6-terra"))
    assert [r.model for r in refs] == ["gpt-5.6-terra", "gpt-5.6-sol"]


def test_escalation_of_an_off_profile_provider_uses_its_legacy_tiers(clean_env):
    # Under the openai profile an explicit Claude 4 base climbs the legacy alias ladder.
    refs = escalation_ladder("synthesizer", resolve_model_ref("claude-sonnet-4-6"),
                             settings=load_committed_settings(profile="openai"))
    assert [r.model for r in refs] == ["claude-sonnet-4-6", "claude-opus-4-8"]


def test_a_stage_escalates_within_the_profile_it_was_built_with(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-unit")
    clean_env.setenv("XBRLRAG_LLM_PROFILE", "anthropic")
    synthesizer = XbrlRagSynthesizer()
    clean_env.setenv("XBRLRAG_LLM_PROFILE", "openai")  # a later change does not move it
    refs = escalation_ladder("synthesizer", synthesizer._model_ref,
                             settings=synthesizer._llm_settings)
    assert [r.model for r in refs] == ["claude-sonnet-5-5", "claude-opus-5-5"]


# ---------------------------------------------------------------- call parameters


def test_call_params_keep_the_openai_models_config():
    params = call_params(resolve_model_ref("gpt-5-mini").config, max_completion_tokens=2000,
                         temperature=0.1)
    assert params == {"max_completion_tokens": 8000, "temperature": 1}


def test_call_params_cap_the_default_and_drop_temperature_for_claude():
    config = resolve_model_ref("claude-haiku-5-5").config
    ceiling = load_committed_settings(profile="anthropic").max_output_tokens_for(
        "claude-haiku-5-5"
    )
    assert call_params(config, max_completion_tokens=2000, temperature=0.1) == {
        "max_completion_tokens": min(2000, ceiling), "temperature": None,
    }
    big = call_params(config, max_completion_tokens=10**9, temperature=0.1)
    assert big["max_completion_tokens"] == ceiling


def test_call_params_for_an_unconfigured_model_are_the_defaults():
    assert call_params({}, max_completion_tokens=1234, temperature=0.3) == {
        "max_completion_tokens": 1234, "temperature": 0.3,
    }


# ---------------------------------------------------------------- keys


def test_a_claude_stage_without_its_key_names_the_missing_variable(clean_env):
    clean_env.setenv("XBRLRAG_LLM_PROFILE", "anthropic")
    clean_env.setenv("OPENAI_API_KEY", "sk-unit")  # the wrong provider's key
    with pytest.raises(LlmConfigError, match="ANTHROPIC_API_KEY"):
        XbrlRagSynthesizer()


def test_a_key_file_supplies_the_stage_key(clean_env, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("sk-from-file\n", encoding="utf-8")
    clean_env.setenv("OPENAI_API_KEY_FILE", str(key_file))
    assert XbrlRagSynthesizer().api_key == "sk-from-file"


def test_an_explicit_key_and_model_still_win(clean_env):
    clean_env.setenv("XBRLRAG_LLM_PROFILE", "anthropic")
    synthesizer = XbrlRagSynthesizer(api_key="sk-explicit", model="gpt-5")
    assert synthesizer.model == "gpt-5"
    assert synthesizer.api_key == "sk-explicit"


# ---------------------------------------------------------------- legacy callers are priced


def test_every_model_a_legacy_caller_can_pick_is_priced():
    legacy = {
        *SEC_TABLE_MODEL_CONFIGS,  # the PDF flow's table/page/query models
        *ANTHROPIC_MODEL_ALIASES.values(),  # AnthropicWrapper / AnthropicConfig tiers
        LLMConfig().model,
        OpenAIConfig().model,
        HuggingFaceConfig().model,
        # The long-context and tool arms default to each profile's frontier model
        # (docs/specs/arm-model-from-profile.md), so every one of them must be priced.
        *(load_committed_settings(profile=p).model_for_tier("frontier")
          for p in load_committed_settings().profile_names),
    }
    assert sorted(m for m in legacy if m not in MODEL_PRICING) == []
