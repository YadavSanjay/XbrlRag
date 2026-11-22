"""DeepSeek wiring beyond the deepseek-provider spec: escalation stays on DeepSeek, the
adapter never picks up the OpenAI key or base URL, and a DeepSeek stage builds against the
deepseek profile. No network: replies and clients are never created.
"""

from __future__ import annotations

import pytest

from src.llm.escalation import escalation_ladder
from src.llm.gateway.adapters import DeepSeekAdapter
from src.llm.gateway.model_ref import resolve_model_ref
from src.llm.profiles import load_committed_settings
from src.llm.stage_models import key_env_for


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("XBRLRAG_LLM_PROFILE", "XBRLRAG_USE_REPLACEMENT_MODELS", "OPENAI_API_KEY",
                 "OPENAI_API_KEY_FILE", "OPENAI_BASE_URL", "OPENAI_COMPAT_API_KEY",
                 "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_deepseek_escalation_climbs_the_deepseek_profile(clean_env):
    # A DeepSeek stage must climb flash -> v4-pro, never to an OpenAI model.
    settings = load_committed_settings(profile="deepseek")
    refs = escalation_ladder("plan_generator", resolve_model_ref("deepseek-flash"),
                             settings=settings)
    assert [r.model for r in refs] == ["deepseek-flash", "deepseek-v4-pro"]
    assert {r.provider for r in refs} == {"deepseek"}


def test_deepseek_base_outside_a_deepseek_profile_does_not_escalate(clean_env):
    # Under the openai profile an explicit DeepSeek base has no ladder of its own.
    refs = escalation_ladder("synthesizer", resolve_model_ref("deepseek-flash"),
                             settings=load_committed_settings(profile="openai"))
    assert [r.model for r in refs] == ["deepseek-flash"]


def test_adapter_never_uses_the_openai_key_or_base_url(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "sk-openai-must-not-leak")
    clean_env.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    adapter = DeepSeekAdapter(base_url="https://api.deepseek.com")
    assert adapter.api_key is None
    assert adapter.base_url == "https://api.deepseek.com"
    clean_env.setenv("DEEPSEEK_API_KEY", "sk-deepseek-unit")
    assert DeepSeekAdapter(base_url="https://api.deepseek.com").api_key == "sk-deepseek-unit"


def test_a_stage_on_the_deepseek_profile_reads_the_deepseek_key(clean_env):
    settings = load_committed_settings(profile="deepseek")
    for model in settings.tier_models().values():
        assert key_env_for(model, settings) == "DEEPSEEK_API_KEY", model


def test_the_deepseek_profile_resolves_every_stage_to_deepseek(clean_env):
    settings = load_committed_settings(profile="deepseek")
    for stage, model in settings.stage_models().items():
        assert resolve_model_ref(model).provider == "deepseek", stage
