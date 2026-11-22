"""C4: tool calling on Chat Completions: the OpenAI-compatible and DeepSeek adapters."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway import OpenAICompatAdapter
from src.llm.gateway.adapters.deepseek_adapter import DeepSeekAdapter
from src.llm.gateway.tools import ToolSpec, tool_result_message

MODEL = "deepseek-flash"
SCHEMA = {"type": "object", "properties": {"expression": {"type": "string"}}}
USER = {"role": "user", "content": "question"}
ASSISTANT = {
    "role": "assistant",
    "content": "",
    "tool_calls": [{"id": "c1", "name": "calculate", "arguments": {"expression": "1+1"}}],
    "provider_state": {"reasoning_content": "first add"},
}
WIRE_TOOL = {"type": "function",
             "function": {"name": "calculate", "description": "Does arithmetic.",
                          "parameters": SCHEMA}}


def _spec() -> ToolSpec:
    return ToolSpec("calculate", "Does arithmetic.", SCHEMA)


def _deepseek() -> DeepSeekAdapter:
    return DeepSeekAdapter(api_key="sk-spec", base_url="http://127.0.0.1:9")


def _compat() -> OpenAICompatAdapter:
    return OpenAICompatAdapter(api_key="sk-spec", base_url="http://127.0.0.1:9/v1")


def _reply(message: Any, finish_reason: str = "tool_calls"):
    return SimpleNamespace(
        model=MODEL,
        choices=[SimpleNamespace(finish_reason=finish_reason, message=message)],
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=20,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=5),
            prompt_tokens_details=SimpleNamespace(cached_tokens=60),
        ),
    )


def _wire_call() -> SimpleNamespace:
    return SimpleNamespace(
        id="c1", type="function",
        function=SimpleNamespace(name="calculate", arguments='{"expression": "1+1"}'),
    )


@pytest.mark.criterion("C4")
def test_the_deepseek_request():
    # oracle: requirement 8 and deepseek-provider requirement 4 (max_tokens, configured effort)
    params = _deepseek().build_tool_params(
        messages=[USER, ASSISTANT, tool_result_message("c1", "2")],
        model=MODEL, tools=[_spec()], max_completion_tokens=8000, temperature=0.1,
    )
    assert params == {
        "model": MODEL,
        "messages": [
            USER,
            {"role": "assistant", "content": "", "reasoning_content": "first add",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "calculate",
                                          "arguments": json.dumps({"expression": "1+1"})}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "2"},
        ],
        "tools": [WIRE_TOOL],
        "tool_choice": "auto",
        "max_tokens": 8000,
        "temperature": 0.1,
        "reasoning_effort": "low",
    }


@pytest.mark.criterion("C4")
def test_the_compat_adapter_gives_the_chat_shape():
    # oracle: requirement 8 — Chat Completions, with the OpenAI output-budget name
    params = _compat().build_tool_params(
        messages=[USER], model="some-model", tools=[_spec()], tool_choice="none",
        max_completion_tokens=500,
    )
    assert params == {
        "model": "some-model",
        "messages": [USER],
        "tools": [WIRE_TOOL],
        "tool_choice": "none",
        "max_completion_tokens": 500,
    }


@pytest.mark.criterion("C4")
def test_the_reply_keeps_reasoning_for_the_next_turn():
    # oracle: requirement 8
    message = SimpleNamespace(content=None, tool_calls=[_wire_call()],
                              reasoning_content="first add")
    parsed = _deepseek().parse_tool_turn(_reply(message), stage="s")
    assert [(c.call_id, c.name, c.arguments) for c in parsed.tool_calls] == [
        ("c1", "calculate", {"expression": "1+1"})]
    assert parsed.content == ""
    assert (parsed.prompt_tokens, parsed.completion_tokens) == (100, 20)
    assert parsed.cached_prompt_tokens == 60
    assert parsed.assistant_message["provider_state"]["reasoning_content"] == "first add"
    assert parsed.assistant_message["tool_calls"] == [
        {"id": "c1", "name": "calculate", "arguments": {"expression": "1+1"}}]


@pytest.mark.criterion("C4")
def test_a_final_reply_and_unreadable_arguments():
    # oracle: requirements 5, 8
    final = _compat().parse_tool_turn(
        _reply(SimpleNamespace(content="done", tool_calls=None), "stop"), stage="s"
    )
    assert final.tool_calls == []
    assert final.content == "done"
    broken = SimpleNamespace(
        id="c2", type="function", function=SimpleNamespace(name="calculate", arguments="{oops"),
    )
    parsed = _compat().parse_tool_turn(
        _reply(SimpleNamespace(content=None, tool_calls=[broken])), stage="s"
    )
    assert parsed.tool_calls[0].arguments is None
    assert parsed.tool_calls[0].arguments_error


@pytest.mark.criterion("C4")
@given(flags=st.lists(st.booleans(), min_size=1, max_size=5))
def test_a_cache_point_never_reaches_the_wire(flags):
    # oracle: requirement 8 — DeepSeek's caching is automatic, and it would reject the key
    messages = [
        {"role": "user", "content": str(i), **({"cache_point": True} if flag else {})}
        for i, flag in enumerate(flags)
    ]
    params = _deepseek().build_tool_params(messages=messages, model=MODEL, tools=[_spec()])
    assert [m["content"] for m in params["messages"]] == [str(i) for i in range(len(flags))]
    assert all("cache_point" not in m for m in params["messages"])
