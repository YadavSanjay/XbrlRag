"""C1: the committed configuration declares the DeepSeek provider, models, profile and prices.

Oracle: requirements 1 and 2; the prices are DeepSeek's Models & Pricing page (USD, retrieved
2026-10-29), peak rates per 1M tokens divided by 1,000 (``DEEPSEEK_RATES`` in conftest.py).
Each test first asserts the DeepSeek entries exist, so it fails by assertion while they don't.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import load_committed_settings

TIERS = ("small", "medium", "frontier")
# The profiles requirement 1 leaves unchanged, with their committed tier models.
UNCHANGED_PROFILES = {
    "openai": {"small": "gpt-5-nano", "medium": "gpt-5-mini", "frontier": "gpt-5"},
    "openai-5.6": {"small": "gpt-5.6-luna", "medium": "gpt-5.6-terra", "frontier": "gpt-5.6-sol"},
    "anthropic": {
        "small": "claude-haiku-5-5", "medium": "claude-sonnet-5-5", "frontier": "claude-opus-5-5",
    },
}
# tolerance: C1 states prices within abs=1e-12 (the rates are decimal literals, ~1e-6 each)
PRICE_ABS = 1e-12


def _deepseek_settings():
    names = load_committed_settings(environ={}).profile_names
    assert "deepseek" in names, f"no deepseek profile in the committed llm.yaml: {names}"
    return load_committed_settings(profile="deepseek", environ={})


@pytest.mark.criterion("C1")
def test_deepseek_profile_tiers_and_provider(spec):
    # oracle: requirement 1 — profile deepseek: small/medium deepseek-flash, frontier
    # deepseek-v4-pro; providers_in_use() is {"deepseek"}
    settings = _deepseek_settings()
    assert settings.active_profile == "deepseek"
    assert settings.tier_models() == {
        "small": "deepseek-flash", "medium": "deepseek-flash", "frontier": "deepseek-v4-pro",
    }
    assert settings.providers_in_use() == {"deepseek"}


@pytest.mark.criterion("C1")
def test_base_url_and_key_env_of_each_provider(spec):
    # oracle: requirement 1 — deepseek: {api_key_env: DEEPSEEK_API_KEY, base_url:
    # "https://api.deepseek.com"}; openai and anthropic carry no base_url
    settings = _deepseek_settings()
    assert settings.base_url_for("deepseek") == "https://api.deepseek.com"
    assert settings.api_key_env_for("deepseek") == "DEEPSEEK_API_KEY"
    assert settings.base_url_for("openai") is None
    assert settings.base_url_for("anthropic") is None


@pytest.mark.criterion("C1")
def test_deepseek_model_rows(spec):
    # oracle: requirement 1 — deepseek-flash (8000, low) and deepseek-v4-pro (16000, high),
    # both on deepseek
    settings = _deepseek_settings()
    assert settings.effort_for("deepseek-flash") == "low"
    assert settings.effort_for("deepseek-v4-pro") == "high"
    assert settings.max_output_tokens_for("deepseek-flash") == 8000
    assert settings.max_output_tokens_for("deepseek-v4-pro") == 16000
    assert settings.provider_for("deepseek-flash") == "deepseek"
    assert settings.provider_for("deepseek-v4-pro") == "deepseek"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_deepseek_prices(spec, model):
    # oracle: requirement 2's table — flash 0.0003 / 0.000006 / 0.0012 and v4-pro
    # 0.00132 / 0.000044 / 0.00396 (input / cached_input / output); no cache_write, no tiers
    settings = _deepseek_settings()
    table = settings.pricing_table()
    assert model in table, f"{model} has no row in the committed pricing.yaml"
    rates = settings.rates_for(model)
    assert set(rates) == {"input", "cached_input", "output"}
    expected = {
        "deepseek-flash": {"input": 0.0003, "cached_input": 0.000006, "output": 0.0012},
        "deepseek-v4-pro": {"input": 0.00132, "cached_input": 0.000044, "output": 0.00396},
    }[model]
    for key, value in expected.items():
        assert rates[key] == pytest.approx(value, abs=PRICE_ABS), key


@pytest.mark.criterion("C1")
@given(
    model=st.sampled_from(["deepseek-flash", "deepseek-v4-pro"]),
    key=st.sampled_from(["input", "cached_input", "output"]),
)
def test_any_deepseek_rate_is_the_pages_peak_rate(spec, model, key):
    # oracle: DeepSeek's pricing page, per-1M peak rate / 1000 (conftest DEEPSEEK_RATES)
    table = load_committed_settings(environ={}).pricing_table()
    assert model in table, f"{model} has no row in the committed pricing.yaml"
    assert table[model][key] == pytest.approx(spec.rates[model][key], abs=PRICE_ABS)
    assert "cache_write" not in table[model]
    assert "tiers" not in table[model]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_adding_deepseek_leaves_the_other_profiles_unchanged(spec):
    # oracle: requirement 1 — the other profiles and active_profile are unchanged; the committed
    # YAML declares the deepseek provider row exactly
    doc = spec.committed_llm_doc()
    assert doc["providers"].get("deepseek") == {
        "api_key_env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
    }
    assert doc["profiles"].get("deepseek") == spec.profile
    assert doc["active_profile"] == "${XBRLRAG_LLM_PROFILE:-}"
    for name, row in UNCHANGED_PROFILES.items():
        assert doc["profiles"][name] == row, name
        settings = load_committed_settings(profile=name, environ={})
        assert settings.tier_models() == row, name
        assert "deepseek" not in settings.providers_in_use(), name
    # Not deepseek. arm-model-from-profile moves the fallback from openai to openai-6.1 (the
    # gpt-5 family shuts down 2026-12-11) and pins it in its own C3.
    assert load_committed_settings(environ={}).active_profile in ("openai", "openai-6.1")
