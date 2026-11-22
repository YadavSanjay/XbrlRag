"""C3: tool calling on the Anthropic Messages API."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway import AnthropicAdapter, LLMRefusalError
from src.llm.gateway.tools import ToolSpec, tool_result_message

MODEL = "claude-opus-5-5"
SCHEMA = {"type": "object", "properties": {"expression": {"type": "string"}}}
SYSTEM = {"role": "system", "content": "be careful"}
USER = {"role": "user", "content": "question"}
EPHEMERAL = {"type": "ephemeral"}


def _spec() -> ToolSpec:
    return ToolSpec("calculate", "Does arithmetic.", SCHEMA)


class Block:
    """An SDK content block: attributes plus ``model_dump``."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)
        self._fields = fields

    def model_dump(self, **_kwargs: Any) -> dict:
        return dict(self._fields)


def _reply(blocks: list[Block], stop_reason: str = "tool_use"):
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason, stop_details=None, model=MODEL,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=800,
                              cache_creation_input_tokens=25),
    )


@pytest.mark.criterion("C3")
def test_the_request():
    # oracle: requirement 7 on top of llm-anthropic-and-caching's request, read from llm.yaml's row
    params = AnthropicAdapter(api_key="sk-ant-spec").build_tool_params(
        messages=[SYSTEM, USER], model=MODEL, tools=[_spec()], max_completion_tokens=8000,
    )
    assert params == {
        "model": MODEL,
        "max_tokens": 16000,
        "system": [{"type": "text", "text": "be careful", "cache_control": EPHEMERAL}],
        "messages": [{"role": "user", "content": "question"}],
        "output_config": {"effort": "high"},
        "tools": [{"name": "calculate", "description": "Does arithmetic.",
                   "input_schema": SCHEMA}],
        "tool_choice": {"type": "auto"},
    }


@pytest.mark.criterion("C3")
def test_tool_choice_none():
    # oracle: requirement 7
    params = AnthropicAdapter(api_key="sk-ant-spec").build_tool_params(
        messages=[USER], model=MODEL, tools=[_spec()], tool_choice="none",
    )
    assert params["tool_choice"] == {"type": "none"}


@pytest.mark.criterion("C3")
def test_thinking_blocks_go_back_verbatim():
    # oracle: requirement 7 — the API requires thinking blocks and signatures during tool use
    blocks = [
        {"type": "thinking", "thinking": "hmm", "signature": "sig-1"},
        {"type": "tool_use", "id": "t1", "name": "calculate", "input": {"expression": "1+1"}},
    ]
    assistant = {"role": "assistant", "content": "", "tool_calls": [],
                 "provider_state": {"content": blocks}}
    params = AnthropicAdapter(api_key="sk-ant-spec").build_tool_params(
        messages=[USER, assistant, tool_result_message("t1", "2")], model=MODEL, tools=[_spec()],
    )
    assert params["messages"][1] == {"role": "assistant", "content": blocks}
    assert params["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "2"}],
    }


@pytest.mark.criterion("C3")
def test_the_reply_gives_calls_and_keeps_every_block():
    # oracle: requirement 7, and usage normalized as _usage does (cached is part of the prompt)
    reply = _reply([
        Block(type="thinking", thinking="hmm", signature="sig-1"),
        Block(type="text", text="Let me compute."),
        Block(type="tool_use", id="t1", name="calculate", input={"expression": "1+1"}),
    ])
    parsed = AnthropicAdapter(api_key="sk-ant-spec").parse_tool_turn(reply, stage="s")
    assert [(c.call_id, c.name, c.arguments) for c in parsed.tool_calls] == [
        ("t1", "calculate", {"expression": "1+1"})]
    assert parsed.content == "Let me compute."
    assert parsed.prompt_tokens == 925
    assert parsed.cached_prompt_tokens == 800
    assert parsed.cache_write_tokens == 25
    assert parsed.assistant_message["provider_state"]["content"] == [
        {"type": "thinking", "thinking": "hmm", "signature": "sig-1"},
        {"type": "text", "text": "Let me compute."},
        {"type": "tool_use", "id": "t1", "name": "calculate", "input": {"expression": "1+1"}},
    ]
    json.dumps(parsed.assistant_message)


@pytest.mark.criterion("C3")
def test_a_final_reply_has_no_calls():
    # oracle: requirement 7
    parsed = AnthropicAdapter(api_key="sk-ant-spec").parse_tool_turn(
        _reply([Block(type="text", text="done")], "end_turn"), stage="s"
    )
    assert parsed.tool_calls == []
    assert parsed.content == "done"


@pytest.mark.criterion("C3")
def test_a_refusal_raises():
    # oracle: requirement 7 — as parse does
    with pytest.raises(LLMRefusalError):
        AnthropicAdapter(api_key="sk-ant-spec").parse_tool_turn(
            _reply([], "refusal"), stage="s"
        )


@pytest.mark.criterion("C3")
@given(n=st.integers(1, 8), marked=st.booleans())
def test_tool_results_and_the_status_form_one_user_message(n, marked):
    # oracle: requirement 7 — the API takes every result of a turn in one user message
    assistant = {"role": "assistant", "content": "", "tool_calls": [],
                 "provider_state": {"content": [{"type": "text", "text": "x"}]}}
    results = [tool_result_message(f"t{i}", f"r{i}") for i in range(n)]
    status: dict[str, Any] = {"role": "user", "content": "3 steps left"}
    if marked:
        status["cache_point"] = True
    params = AnthropicAdapter(api_key="sk-ant-spec").build_tool_params(
        messages=[USER, assistant, *results, status], model=MODEL, tools=[_spec()],
    )
    assert len(params["messages"]) == 3
    merged = params["messages"][2]
    assert merged["role"] == "user"
    assert len(merged["content"]) == n + 1
    assert [b["tool_use_id"] for b in merged["content"][:n]] == [f"t{i}" for i in range(n)]
    last = merged["content"][-1]
    assert last["type"] == "text"
    assert last["text"] == "3 steps left"
    assert ("cache_control" in last) == marked
