"""C2: the Anthropic request (requirement 2).

``AnthropicAdapter.build_params`` returns exactly the kwargs of one ``messages.stream``
call. Expected dicts are written by hand from requirement 2; the per-model ``max_tokens``
and ``effort`` come from the committed configs/llm/llm.yaml (llm-provider-profiles).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters import AnthropicAdapter

EPHEMERAL = {"type": "ephemeral"}
NEVER_SENT = ("temperature", "thinking", "response_format")


def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(api_key="sk-ant-spec-offline")


@pytest.mark.criterion("C2")
def test_opus_request_with_system_cache_point_and_question():
    # oracle: requirement 2, hand-built — claude-opus-5-5's committed max_output_tokens
    # (16000) and effort ("high"); system as one cached text block; the cache-point
    # message as a cached text block; the plain message as a string
    messages = [
        {"role": "system", "content": "You answer from the filing."},
        {"role": "user", "content": "FILING: \"Revenue | 391,035\"", "cache_point": True},
        {"role": "user", "content": "QUESTION: What was revenue?"},
    ]
    params = _adapter().build_params(
        messages=messages,
        model="claude-opus-5-5",
        max_completion_tokens=2000,
        temperature=None,
        response_format="json",
        extra=None,
    )
    assert params == {
        "model": "claude-opus-5-5",
        "max_tokens": 16000,
        "system": [
            {"type": "text", "text": "You answer from the filing.", "cache_control": EPHEMERAL}
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "FILING: \"Revenue | 391,035\"",
                        "cache_control": EPHEMERAL,
                    }
                ],
            },
            {"role": "user", "content": "QUESTION: What was revenue?"},
        ],
        "output_config": {"effort": "high"},
    }


@pytest.mark.criterion("C2")
def test_each_committed_claude_model_uses_its_budget_and_effort(committed_claude_models):
    # oracle: requirement 2 + the committed configs/llm/llm.yaml (llm-provider-profiles):
    # max_tokens is the model's max_output_tokens, output_config.effort its effort
    assert set(committed_claude_models) >= {
        "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
    }
    for model, cfg in committed_claude_models.items():
        params = _adapter().build_params(
            messages=[{"role": "user", "content": "hi"}],
            model=model,
            max_completion_tokens=2000,
            extra={"metadata": {"user_id": "spec"}},
        )
        expected = {
            "model": model,
            "max_tokens": cfg["max_output_tokens"],
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"user_id": "spec"},
        }
        if cfg.get("effort") is not None:
            expected["output_config"] = {"effort": cfg["effort"]}
        assert params == expected, model


@pytest.mark.criterion("C2")
def test_temperature_for_a_no_temperature_model_raises():
    # oracle: requirement 2 — a non-None temperature for a model the config marks
    # temperature: false (all three committed Claude models) raises ValueError
    for model in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
        with pytest.raises(ValueError):
            _adapter().build_params(
                messages=[{"role": "user", "content": "hi"}], model=model, temperature=0.2
            )


@pytest.mark.criterion("C2")
def test_unconfigured_model_keeps_the_callers_budget():
    # oracle: requirement 2 — an unconfigured model's max_tokens is max_completion_tokens,
    # else 4096; no effort; temperature is never sent; the cache-point message is a
    # cached text block
    messages = [
        {"role": "user", "content": "FILING", "cache_point": True},
        {"role": "user", "content": "QUESTION"},
    ]
    expected_messages = [
        {"role": "user", "content": [{"type": "text", "text": "FILING",
                                      "cache_control": EPHEMERAL}]},
        {"role": "user", "content": "QUESTION"},
    ]
    with_budget = _adapter().build_params(
        messages=messages, model="claude-zz-unconfigured-9", max_completion_tokens=1234,
        temperature=0.3,
    )
    assert with_budget == {
        "model": "claude-zz-unconfigured-9",
        "max_tokens": 1234,
        "messages": expected_messages,
    }
    without_budget = _adapter().build_params(messages=messages, model="claude-zz-unconfigured-9")
    assert without_budget == {
        "model": "claude-zz-unconfigured-9",
        "max_tokens": 4096,
        "messages": expected_messages,
    }


_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=0x2FF), min_size=1, max_size=40
)
_MESSAGE = st.tuples(st.sampled_from(["user", "assistant"]), _TEXT, st.booleans())


@pytest.mark.criterion("C2")
@given(
    system=st.one_of(st.none(), _TEXT),
    body=st.lists(_MESSAGE, min_size=1, max_size=6),
    forced=st.integers(min_value=0, max_value=5),
    model=st.sampled_from(["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]),
    response_format=st.sampled_from([None, "json"]),
)
def test_only_cache_point_messages_carry_cache_control(system, body, forced, model,
                                                       response_format):
    # oracle: requirement 2 — over random messages, a marked message's content is one
    # cached text block with its text unchanged, an unmarked one stays the same string,
    # order is kept, and temperature/thinking/response_format are never sent
    body = list(body)
    role, text, _ = body[forced % len(body)]
    body[forced % len(body)] = (role, text, True)  # at least one cache point
    messages: list[dict] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    for role, text, marked in body:
        message: dict = {"role": role, "content": text}
        if marked:
            message["cache_point"] = True
        messages.append(message)

    params = _adapter().build_params(
        messages=messages, model=model, max_completion_tokens=500,
        response_format=response_format,
    )
    for key in NEVER_SENT:
        assert key not in params
    sent = params["messages"]
    assert len(sent) == len(body)
    for (role, text, marked), out in zip(body, sent, strict=True):
        assert out["role"] == role
        assert set(out) == {"role", "content"}
        if marked:
            assert out["content"] == [
                {"type": "text", "text": text, "cache_control": EPHEMERAL}
            ]
        else:
            assert out["content"] == text
    if system is not None:
        assert params["system"] == [{"type": "text", "text": system, "cache_control": EPHEMERAL}]
    else:
        assert "system" not in params
