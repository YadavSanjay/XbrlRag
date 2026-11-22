"""C4: the OpenAI adapter sends the service tier on every call; no other adapter does.

Oracle: requirement 4. The SDK client is the fake of conftest (the network boundary); the
adapters, the gateway and its cost tracker are the real ones.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.anthropic_adapter import AnthropicAdapter
from src.llm.gateway.adapters.deepseek_adapter import DeepSeekAdapter
from src.llm.gateway.adapters.openai_adapter import OpenAIAdapter, OpenAICompatAdapter
from src.llm.gateway.model_ref import resolve_model_ref
from src.llm.gateway.tools import ToolSpec
from src.llm.profiles import LlmConfigError

TOOL = ToolSpec("lookup", "Looks a fact up.", {"type": "object", "properties": {}})


def _chat(adapter, messages):
    return adapter.build_params(messages=[dict(m) for m in messages], model="gpt-5-mini",
                                max_completion_tokens=500, response_format="json")


def _tools(adapter, messages):
    return adapter.build_tool_params(messages=[dict(m) for m in messages], model="gpt-5-mini",
                                     tools=[TOOL], max_completion_tokens=500)


def _three_requests(spec):
    adapter = OpenAIAdapter(api_key="sk-spec")
    return {
        "chat": _chat(adapter, spec.messages),
        "responses": _chat(adapter, spec.cached_messages),
        "tools": _tools(adapter, spec.messages),
    }


@pytest.mark.criterion("C4")
def test_flex_is_on_every_openai_request_and_absent_when_unset(spec):
    # oracle: requirement 4 — with the tier set, each of the three builds carries
    # service_tier == the tier and is otherwise the unset request; unset, no key at all
    with spec.tier_env(None):
        unset = _three_requests(spec)
    with spec.tier_env("flex"):
        flex = _three_requests(spec)
    assert "messages" in unset["chat"] and "input" in unset["responses"]
    for path, request in unset.items():
        assert "service_tier" not in request, path
        assert flex[path].get("service_tier") == "flex", path
        assert {k: v for k, v in flex[path].items() if k != "service_tier"} == request, path


@pytest.mark.criterion("C4")
def test_other_adapters_never_send_a_tier(spec):
    # oracle: requirement 4 — OpenAICompatAdapter, DeepSeekAdapter and AnthropicAdapter
    # never send service_tier, even with the variable set (and OpenAIAdapter does)
    with spec.tier_env("flex"):
        assert _chat(OpenAIAdapter(api_key="sk-spec"), spec.messages).get("service_tier") == "flex"
        compat = OpenAICompatAdapter(api_key="sk-spec", base_url="https://compat.invalid/v1")
        deepseek = DeepSeekAdapter(api_key="sk-spec", base_url="https://api.deepseek.com")
        anthropic = AnthropicAdapter(api_key="sk-spec")
        requests = {
            "compat chat": _chat(compat, spec.messages),
            "compat tools": _tools(compat, spec.messages),
            "deepseek chat": deepseek.build_params(
                messages=[dict(m) for m in spec.messages], model="deepseek-flash",
                max_completion_tokens=500),
            "deepseek tools": deepseek.build_tool_params(
                messages=[dict(m) for m in spec.messages], model="deepseek-flash",
                tools=[TOOL], max_completion_tokens=500),
            "anthropic": anthropic.build_params(
                messages=[dict(m) for m in spec.messages], model="claude-sonnet-5-5",
                max_completion_tokens=500),
        }
    for name, request in requests.items():
        assert "service_tier" not in request, name


@pytest.mark.criterion("C4")
def test_an_invalid_tier_fails_the_build(spec):
    # oracle: requirement 4 — an invalid value raises LlmConfigError from the OpenAI build,
    # before any provider call
    with spec.tier_env("priority"):
        request = _chat(OpenAIAdapter(api_key="sk-spec"), spec.messages)
        assert request.get("service_tier") == "priority"
    with spec.tier_env("bogus"), pytest.raises(LlmConfigError):
        _chat(OpenAIAdapter(api_key="sk-spec"), spec.messages)


@pytest.mark.criterion("C4")
def test_the_tier_reaches_the_sdk_call_and_is_read_per_call(spec):
    # oracle: requirement 4 — through a real gateway, the SDK's create kwargs carry the tier
    # current when each request is built, on Chat Completions and on Responses
    client = spec.fake_openai_client()
    gateway = spec.make_gateway({"openai": spec.fake_client_openai_adapter(client)})
    ref = resolve_model_ref(spec.gateway_model)
    with spec.tier_env("flex"):
        gateway.complete(messages=[dict(m) for m in spec.messages], model_ref=ref, stage="spec")
        gateway.complete(messages=[dict(m) for m in spec.cached_messages], model_ref=ref,
                         stage="spec")
    with spec.tier_env("priority"):
        gateway.complete(messages=[dict(m) for m in spec.messages], model_ref=ref, stage="spec")
    with spec.tier_env(None):
        gateway.complete(messages=[dict(m) for m in spec.messages], model_ref=ref, stage="spec")
    assert [name for name, _ in client.calls] == ["chat", "responses", "chat", "chat"]
    tiers = [kwargs.get("service_tier") for _, kwargs in client.calls]
    assert tiers == ["flex", "flex", "priority", None]
    assert "service_tier" not in client.calls[3][1]


@pytest.mark.criterion("C4")
@given(tier=st.sampled_from([None, "auto", "default", "flex", "priority"]),
       path=st.sampled_from(["chat", "responses", "tools"]),
       budget=st.integers(min_value=1, max_value=16_000))
def test_any_tier_on_any_path(spec, tier, path, budget):
    # oracle: requirement 4 — service_tier present exactly when set, equal to it, and the
    # other keys are the unset request's
    adapter = OpenAIAdapter(api_key="sk-spec")

    def build():
        if path == "tools":
            return adapter.build_tool_params(messages=[dict(m) for m in spec.messages],
                                             model="gpt-5-mini", tools=[TOOL],
                                             max_completion_tokens=budget)
        messages = spec.cached_messages if path == "responses" else spec.messages
        return adapter.build_params(messages=[dict(m) for m in messages], model="gpt-5-mini",
                                    max_completion_tokens=budget)

    with spec.tier_env(None):
        unset = build()
    with spec.tier_env("flex"):
        assert build().get("service_tier") == "flex"
    with spec.tier_env(tier):
        got = build()
    if tier is None:
        assert got == unset
    else:
        assert got.get("service_tier") == tier
        assert {k: v for k, v in got.items() if k != "service_tier"} == unset
