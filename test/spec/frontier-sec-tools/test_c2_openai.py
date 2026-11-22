"""C2: tool calling on the OpenAI Responses API."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway import OpenAIAdapter
from src.llm.gateway.tools import ToolSpec, tool_result_message

MODEL = "gpt-5.6-sol"
SCHEMA = {"type": "object", "properties": {"expression": {"type": "string"}}}
SYSTEM = {"role": "system", "content": "be careful"}
USER = {"role": "user", "content": "question"}


def _spec() -> ToolSpec:
    return ToolSpec("calculate", "Does arithmetic.", SCHEMA)


# Re-lock: Responses function tools default to strict, which needs every property required and
# additionalProperties false; the tools' optional arguments are not, so requirement 6 sends
# "strict": False.
WIRE_TOOL = {"type": "function", "name": "calculate", "description": "Does arithmetic.",
             "parameters": SCHEMA, "strict": False}


class Item:
    """An SDK output item: attributes plus ``model_dump``."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)
        self._fields = fields

    def model_dump(self, **_kwargs: Any) -> dict:
        return dict(self._fields)


def _reply(output: list[Item], *, text: str = "", status: str = "completed", **usage: Any):
    return SimpleNamespace(
        object="response", status=status, output=output, output_text=text, model=MODEL,
        incomplete_details=None,
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens", 900),
            output_tokens=usage.get("output_tokens", 100),
            input_tokens_details=SimpleNamespace(cached_tokens=usage.get("cached", 300),
                                                 cache_write_tokens=usage.get("written", 50)),
            output_tokens_details=SimpleNamespace(reasoning_tokens=usage.get("reasoning", 40)),
        ),
    )


@pytest.mark.criterion("C2")
def test_the_request_without_a_cache_point():
    # oracle: requirement 6's literal request
    params = OpenAIAdapter(api_key="sk-spec").build_tool_params(
        messages=[SYSTEM, USER], model=MODEL, tools=[_spec()], max_completion_tokens=8000,
        temperature=1.0, reasoning_effort="high",
    )
    assert params == {
        "model": MODEL,
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": "be careful"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "question"}]},
        ],
        "tools": [WIRE_TOOL],
        "tool_choice": "auto",
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "max_output_tokens": 8000,
        "temperature": 1.0,
        "reasoning": {"effort": "high"},
    }


@pytest.mark.criterion("C2")
def test_the_request_with_a_cache_point():
    # oracle: requirement 6 — explicit mode, and the existing breakpoint on the marked part
    params = OpenAIAdapter(api_key="sk-spec").build_tool_params(
        messages=[SYSTEM, {**USER, "cache_point": True}], model=MODEL, tools=[_spec()],
        tool_choice="none",
    )
    assert params["prompt_cache_options"] == {"mode": "explicit"}
    assert params["tool_choice"] == "none"
    part = params["input"][1]["content"][0]
    assert part["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert "max_output_tokens" not in params
    assert "temperature" not in params


@pytest.mark.criterion("C2")
def test_a_tool_result_becomes_a_function_call_output():
    # oracle: requirement 6
    state = {"output": [{"type": "function_call", "call_id": "c1", "name": "calculate",
                         "arguments": "{}"}]}
    params = OpenAIAdapter(api_key="sk-spec").build_tool_params(
        messages=[USER,
                  {"role": "assistant", "content": "", "tool_calls": [], "provider_state": state},
                  tool_result_message("c1", '{"value": 2}')],
        model=MODEL, tools=[_spec()],
    )
    assert params["input"][1:] == [
        state["output"][0],
        {"type": "function_call_output", "call_id": "c1", "output": '{"value": 2}'},
    ]


@pytest.mark.criterion("C2")
def test_the_reply_gives_calls_text_and_usage():
    # oracle: requirement 6 — usage is read as _parse_responses reads it
    reply = _reply(
        [Item(type="reasoning", id="r1", encrypted_content="abc"),
         Item(type="function_call", call_id="c1", name="calculate",
              arguments='{"expression": "1+1"}')],
        text="thinking aloud",
    )
    parsed = OpenAIAdapter(api_key="sk-spec").parse_tool_turn(reply, stage="s")
    assert [(c.call_id, c.name, c.arguments) for c in parsed.tool_calls] == [
        ("c1", "calculate", {"expression": "1+1"})]
    assert parsed.content == "thinking aloud"
    assert (parsed.prompt_tokens, parsed.completion_tokens) == (900, 100)
    assert (parsed.cached_prompt_tokens, parsed.cache_write_tokens) == (300, 50)
    assert parsed.reasoning_tokens == 40
    assert parsed.served_model == MODEL
    message = parsed.assistant_message
    assert message["role"] == "assistant"
    assert message["content"] == "thinking aloud"
    assert message["tool_calls"] == [
        {"id": "c1", "name": "calculate", "arguments": {"expression": "1+1"}}]
    assert message["provider_state"]["output"] == [
        {"type": "reasoning", "id": "r1", "encrypted_content": "abc"},
        {"type": "function_call", "call_id": "c1", "name": "calculate",
         "arguments": '{"expression": "1+1"}'},
    ]
    json.dumps(message)


@pytest.mark.criterion("C2")
def test_a_final_reply_has_no_calls():
    # oracle: requirement 6 — text alone
    parsed = OpenAIAdapter(api_key="sk-spec").parse_tool_turn(
        _reply([Item(type="message", id="m1")], text="done"), stage="s"
    )
    assert parsed.tool_calls == []
    assert parsed.content == "done"
    assert parsed.finish_reason == "stop"


@pytest.mark.criterion("C2")
def test_unreadable_arguments_are_reported_not_raised():
    # oracle: requirement 5 — a reply cut off mid-call is the usual cause
    reply = _reply(
        [Item(type="function_call", call_id="c1", name="calculate", arguments='{"expr')],
        status="incomplete",
    )
    parsed = OpenAIAdapter(api_key="sk-spec").parse_tool_turn(reply, stage="s")
    call = parsed.tool_calls[0]
    assert call.arguments is None
    assert call.arguments_error


@pytest.mark.criterion("C2")
def test_arguments_that_are_not_an_object_are_reported():
    # oracle: requirement 5 — valid JSON, but not an object
    reply = _reply([Item(type="function_call", call_id="c1", name="calculate", arguments="[1]")])
    call = OpenAIAdapter(api_key="sk-spec").parse_tool_turn(reply, stage="s").tool_calls[0]
    assert call.arguments is None
    assert call.arguments_error


@pytest.mark.criterion("C2")
def test_the_request_goes_to_the_responses_endpoint():
    # oracle: requirement 6 — the existing invoke routes on `input`
    seen: dict[str, Any] = {}

    def create(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "raw"

    adapter = OpenAIAdapter(api_key="sk-spec")
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    params = adapter.build_tool_params(messages=[USER], model=MODEL, tools=[_spec()])
    assert adapter.invoke(client, params) == "raw"
    assert seen["tools"] == [WIRE_TOOL]


_ITEM = st.fixed_dictionaries({
    "type": st.sampled_from(["reasoning", "function_call", "message"]),
    "id": st.text(min_size=1, max_size=8),
    "encrypted_content": st.text(max_size=20),
})


@pytest.mark.criterion("C2")
@given(items=st.lists(_ITEM, min_size=1, max_size=6))
def test_output_items_go_back_verbatim(items):
    # oracle: requirement 6 — reasoning survives only if every item goes back unchanged, in place
    assistant = {"role": "assistant", "content": "", "tool_calls": [],
                 "provider_state": {"output": items}}
    params = OpenAIAdapter(api_key="sk-spec").build_tool_params(
        messages=[USER, assistant, tool_result_message("c1", "r")], model=MODEL, tools=[_spec()],
    )
    assert params["input"][1:1 + len(items)] == items
    assert params["input"][1 + len(items)]["type"] == "function_call_output"
    assert len(params["input"]) == len(items) + 2
