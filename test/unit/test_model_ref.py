"""Unit tests for provider-neutral model reference resolution."""
from src.llm.gateway.model_ref import ModelRef, derive_series, infer_provider, resolve_model_ref


def test_openai_tiers_resolve_provider_series_tier():
    r = resolve_model_ref("gpt-5-mini")
    assert r.provider == "openai"
    assert r.series == "gpt-5"
    assert r.tier == "medium"
    assert r.model == "gpt-5-mini"
    assert r.config.get("model") == "gpt-5-mini"

    assert resolve_model_ref("gpt-5-nano").tier == "small"
    assert resolve_model_ref("gpt-5").tier == "frontier"


def test_gpt4o_has_series_but_no_tier():
    r = resolve_model_ref("gpt-4o")
    assert r.provider == "openai"
    assert r.series == "gpt-4o"
    assert r.tier is None


def test_anthropic_concrete_id_resolves():
    r = resolve_model_ref("claude-opus-4-8")
    assert r.provider == "anthropic"
    assert r.series == "claude-4"
    assert r.tier == "frontier"


def test_anthropic_tier_alias_resolves_to_concrete_id():
    r = resolve_model_ref("sonnet")
    assert r.provider == "anthropic"
    assert r.model == "claude-sonnet-4-6"
    assert r.series == "claude-4"
    assert r.tier == "medium"
    # config comes from ANTHROPIC_MODEL_CONFIGS keyed by the alias
    assert r.config.get("model") == "claude-sonnet-4-6"


def test_base_url_tags_openai_compat():
    r = resolve_model_ref("gpt-5-mini", base_url="http://localhost:8000/v1")
    assert r.provider == "openai_compat"
    assert r.base_url == "http://localhost:8000/v1"
    assert r.series == "gpt-5"


def test_passing_model_ref_is_idempotent():
    r = resolve_model_ref("gpt-5-mini")
    assert resolve_model_ref(r) is r


def test_infer_provider_and_derive_series_helpers():
    assert infer_provider("gpt-5-mini") == "openai"
    assert infer_provider("claude-opus-4-8") == "anthropic"
    assert infer_provider("opus") == "anthropic"  # alias
    assert derive_series("gpt-5-nano", "openai") == "gpt-5"
    assert derive_series("claude-haiku-4-5-20251001", "anthropic") == "claude-4"
