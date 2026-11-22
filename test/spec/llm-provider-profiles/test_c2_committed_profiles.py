"""C2: the committed profiles are exactly requirement 2's table; Claude models take no temperature.

Oracle: requirement 2 (the table is copied into conftest.py as ``PROFILE_TABLE``).
"""

from __future__ import annotations

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import load_committed_settings

PROFILES = ("openai", "openai-5.6", "anthropic")
TIERS = ("small", "medium", "frontier")


@pytest.mark.criterion("C2")
def test_committed_llm_yaml_holds_exactly_the_three_profiles(spec):
    # oracle: requirement 2 — the committed profiles are exactly openai, openai-5.6 and
    # anthropic with the table's models; requirement 1 — the committed active_profile is
    # "${XBRLRAG_LLM_PROFILE:-}"
    path = spec.repo_root / "configs" / "llm" / "llm.yaml"
    assert path.is_file(), "configs/llm/llm.yaml is not committed"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    # deepseek-provider: the committed file may also hold the deepseek profile and provider;
    # the three profiles here keep exactly the table's models, and the deepseek entries are
    # pinned by test/spec/deepseek-provider
    for name, row in spec.profile_table.items():
        assert doc["profiles"][name] == row, name
    # openai-6-1-profile: the openai-6.1 profile is pinned by test/spec/openai-6-1-profile
    assert set(doc["profiles"]) - set(spec.profile_table) <= {"deepseek", "openai-6.1"}
    assert doc["active_profile"] == "${XBRLRAG_LLM_PROFILE:-}"
    assert {"openai", "anthropic"} <= set(doc["providers"]) <= {"openai", "anthropic", "deepseek"}


@pytest.mark.criterion("C2")
def test_loaded_profiles_match_the_table(llm_env, spec):
    # oracle: requirement 2's table, read through the settings interface
    for name, row in spec.profile_table.items():
        settings = load_committed_settings(profile=name, environ={})
        assert {t: settings.model_for_tier(t) for t in TIERS} == row


@pytest.mark.criterion("C2")
def test_claude_models_take_no_temperature_and_openai_models_do(llm_env, spec):
    # oracle: requirement 2 — the Claude models have temperature: false; requirement 9 —
    # under openai the call parameters are today's, which send a temperature
    settings = load_committed_settings(profile="anthropic", environ={})
    for model in spec.profile_table["anthropic"].values():
        assert settings.provider_for(model) == "anthropic"
        assert settings.temperature_allowed(model) is False
    for model in spec.profile_table["openai"].values():
        assert settings.provider_for(model) == "openai"
        assert settings.temperature_allowed(model) is True


@pytest.mark.criterion("C2")
@given(name=st.sampled_from(PROFILES), tier=st.sampled_from(TIERS))
def test_any_profile_tier_is_the_tables_model_and_provider(spec, name, tier):
    # oracle: requirement 2 — every (profile, tier) cell; the provider follows the model's
    # family (claude-* is anthropic, gpt-* is openai) and the active profile is the one asked
    settings = load_committed_settings(profile=name, environ={})
    model = spec.profile_table[name][tier]
    assert settings.active_profile == name
    assert settings.model_for_tier(tier) == model
    family = "anthropic" if model.startswith("claude-") else "openai"
    assert settings.provider_for(model) == family
    assert settings.providers_in_use() == {"anthropic" if name == "anthropic" else "openai"}
