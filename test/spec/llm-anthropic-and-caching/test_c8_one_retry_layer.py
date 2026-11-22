"""C8: one retry layer (requirement 6).

Both adapters build their SDK client with ``max_retries=0``. The keys are dummies from the
environment; constructing an SDK client makes no request.
"""

from __future__ import annotations

import pytest

from src.llm.gateway.adapters import AnthropicAdapter, OpenAIAdapter, OpenAICompatAdapter


@pytest.mark.criterion("C8")
def test_both_adapters_build_clients_without_sdk_retries(llm_env):
    # oracle: requirement 6 — the SDK client's max_retries is 0, so the gateway's loop is
    # the only retry layer
    llm_env.setenv("OPENAI_API_KEY", "sk-spec-offline")
    llm_env.setenv("ANTHROPIC_API_KEY", "sk-ant-spec-offline")
    openai_client = OpenAIAdapter().get_client()
    anthropic_client = AnthropicAdapter().get_client()
    assert openai_client.max_retries == 0
    assert anthropic_client.max_retries == 0


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@pytest.mark.parametrize("make", [
    lambda: OpenAIAdapter(api_key="sk-spec-other"),
    lambda: OpenAIAdapter(api_key="sk-spec-other", base_url="http://127.0.0.1:9/v1"),
    lambda: OpenAICompatAdapter(api_key="sk-spec-other", base_url="http://127.0.0.1:9/v1"),
    lambda: AnthropicAdapter(api_key="sk-ant-spec-other"),
], ids=["openai-param-key", "openai-base-url", "openai-compat", "anthropic-param-key"])
def test_no_sdk_retries_however_the_adapter_is_built(llm_env, make):
    # oracle: requirement 6 — changing how the key or endpoint is supplied does not change
    # max_retries (0), and the cached client keeps it on every later call
    adapter = make()
    first = adapter.get_client()
    assert first.max_retries == 0
    assert adapter.get_client() is first
    assert adapter.get_client().max_retries == 0
