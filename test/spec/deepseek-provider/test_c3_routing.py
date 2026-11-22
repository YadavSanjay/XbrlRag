"""C3: routing follows the configured provider.

Oracle: requirement 3 and the committed ``configs/llm/llm.yaml``. The gateway is built with an
explicit key and no adapters; building an adapter makes no network call.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.deepseek_adapter import DeepSeekAdapter
from src.llm.gateway.model_ref import infer_provider, resolve_model_ref
from src.llm.profiles import load_committed_settings, missing_api_keys
from src.llm.stage_models import key_env_for

DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")


def _assert_deepseek_configured(spec) -> None:
    models = spec.committed_llm_doc()["models"]
    for model in DEEPSEEK_MODELS:
        assert model in models, f"{model} is not in the committed llm.yaml"
        assert models[model]["provider"] == "deepseek", model


@pytest.mark.criterion("C3")
def test_resolve_model_ref_routes_by_configured_provider(spec):
    # oracle: requirement 3 — a deepseek row resolves to provider deepseek with the provider's
    # base_url, unless the caller passes one; other models are unchanged
    ref = resolve_model_ref("deepseek-flash")
    assert ref.provider == "deepseek"
    assert ref.base_url == "https://api.deepseek.com"
    assert resolve_model_ref("deepseek-flash", base_url="https://x").base_url == "https://x"
    mini = resolve_model_ref("gpt-5-mini")
    assert mini.provider == "openai"
    assert mini.base_url is None
    assert resolve_model_ref("gpt-4o").provider == "openai"


@pytest.mark.criterion("C3")
def test_deepseek_key_env_and_missing_keys(spec):
    # oracle: requirement 3 — DEEPSEEK_API_KEY is the deepseek models' key; it is missing for
    # the deepseek profile when unset
    _assert_deepseek_configured(spec)
    settings = load_committed_settings(profile="deepseek", environ={})
    assert key_env_for("deepseek-v4-pro", settings) == "DEEPSEEK_API_KEY"
    assert key_env_for("deepseek-flash", settings) == "DEEPSEEK_API_KEY"
    assert missing_api_keys(settings, {}) == ["DEEPSEEK_API_KEY"]
    assert missing_api_keys(settings, {"DEEPSEEK_API_KEY": "sk-spec"}) == []


@pytest.mark.criterion("C3")
def test_gateway_default_adapter_for_deepseek(spec):
    # oracle: requirement 3 — the default adapter for provider deepseek is a DeepSeekAdapter
    # with the ref's base_url and the gateway's API key
    ref = resolve_model_ref("deepseek-flash")
    assert ref.provider == "deepseek"
    gateway = LLMGateway(stage="spec", api_key="sk-spec-gateway", rate_limiter=None,
                         max_attempts=1)
    adapter = gateway._build_default_adapter(ref)
    assert isinstance(adapter, DeepSeekAdapter)
    assert adapter.base_url == ref.base_url == "https://api.deepseek.com"
    assert adapter.api_key == "sk-spec-gateway"


@pytest.mark.criterion("C3")
@given(data=st.data())
def test_any_committed_model_resolves_to_its_rows_provider(spec, data):
    # oracle: C3's property over the committed llm.yaml — provider deepseek exactly when the
    # row's provider is deepseek (with that provider's base_url), else infer_provider(m)
    _assert_deepseek_configured(spec)
    doc = spec.committed_llm_doc()
    model = data.draw(st.sampled_from(sorted(doc["models"])))
    row_provider = doc["models"][model]["provider"]
    ref = resolve_model_ref(model)
    if row_provider == "deepseek":
        assert ref.provider == "deepseek"
        assert ref.base_url == doc["providers"]["deepseek"]["base_url"]
    else:
        assert ref.provider == infer_provider(model)
        assert ref.base_url is None


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_explicit_provider_or_base_url_wins(spec):
    # oracle: requirement 3 — "unless the caller passes an explicit provider or base_url":
    # an explicit base_url replaces the configured one; an explicit provider replaces deepseek
    default = resolve_model_ref("deepseek-v4-pro")
    assert default.provider == "deepseek"
    assert default.base_url == "https://api.deepseek.com"
    for url in ("https://x", "https://proxy.example/v1"):
        assert resolve_model_ref("deepseek-v4-pro", base_url=url).base_url == url
    assert resolve_model_ref("deepseek-v4-pro", provider="openai").provider == "openai"
