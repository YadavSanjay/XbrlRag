"""C5: OpenAI routing and the Responses request (requirement 7).

No cache point: Chat Completions exactly as on ``main`` (the expected dicts are copied
from ``OpenAIAdapter.build_params`` on main at f5c337e). At least one cache point: the
Responses API, with the kwargs written by hand from requirement 7 and OpenAI's
prompt-caching guide (2026-10, GPT-5.6+ explicit breakpoints). The client is a fake.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters import OpenAIAdapter

EXPLICIT = {"mode": "explicit"}


def _adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="sk-spec-offline")


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_no_cache_point_is_chat_completions_and_a_marker_moves_it_to_responses(fake_client):
    # oracle: main's OpenAIAdapter.build_params (f5c337e), copied by hand — model, messages
    # verbatim, extra, max_completion_tokens, temperature, response_format "json" ->
    # json_object, reasoning_effort; metamorphic: marking one message routes the same
    # request to responses.create instead
    messages = [
        {"role": "system", "content": "You answer from the filing."},
        {"role": "user", "content": "What was revenue?"},
    ]
    chat = _adapter().build_params(
        messages=messages, model="gpt-5-mini", max_completion_tokens=2000, temperature=0.5,
        response_format="json", reasoning_effort="low", extra={"seed": 7},
    )
    assert chat == {
        "model": "gpt-5-mini",
        "messages": [
            {"role": "system", "content": "You answer from the filing."},
            {"role": "user", "content": "What was revenue?"},
        ],
        "seed": 7,
        "max_completion_tokens": 2000,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
    }
    schema = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    assert _adapter().build_params(
        messages=messages, model="gpt-5", response_format=schema
    ) == {"model": "gpt-5", "messages": messages, "response_format": schema}

    client = fake_client(chat_reply="chat-raw", responses_reply="responses-raw")
    assert _adapter().invoke(client, chat) == "chat-raw"
    assert client.chat_create.calls == [chat]
    assert client.responses_create.calls == []

    marked = [messages[0], {**messages[1], "cache_point": True}]
    responses = _adapter().build_params(
        messages=marked, model="gpt-5-mini", max_completion_tokens=2000,
        response_format="json",
    )
    assert "messages" not in responses
    assert "input" in responses
    client = fake_client(chat_reply="chat-raw", responses_reply="responses-raw")
    assert _adapter().invoke(client, responses) == "responses-raw"
    assert client.responses_create.calls == [responses]
    assert client.chat_create.calls == []


@pytest.mark.criterion("C5")
def test_responses_request_with_a_cache_point(fake_client):
    # oracle: requirement 7, hand-built — system -> developer, input_text parts, the marked
    # part carries the explicit breakpoint, explicit cache options, max_output_tokens,
    # reasoning.effort, text.format json_object, extra passed through
    messages = [
        {"role": "system", "content": "You answer from the filing."},
        {"role": "user", "content": "FILING: \"Revenue | 391,035\"", "cache_point": True},
        {"role": "user", "content": "QUESTION: What was revenue?"},
    ]
    params = _adapter().build_params(
        messages=messages, model="gpt-5.6-sol", max_completion_tokens=8000,
        response_format="json", reasoning_effort="medium", extra={"store": False},
    )
    assert params == {
        "model": "gpt-5.6-sol",
        "input": [
            {"role": "developer",
             "content": [{"type": "input_text", "text": "You answer from the filing."}]},
            {"role": "user",
             "content": [{"type": "input_text", "text": "FILING: \"Revenue | 391,035\"",
                          "prompt_cache_breakpoint": EXPLICIT}]},
            {"role": "user",
             "content": [{"type": "input_text", "text": "QUESTION: What was revenue?"}]},
        ],
        "prompt_cache_options": EXPLICIT,
        "max_output_tokens": 8000,
        "reasoning": {"effort": "medium"},
        "text": {"format": {"type": "json_object"}},
        "store": False,
    }
    client = fake_client(responses_reply="responses-raw")
    assert _adapter().invoke(client, params) == "responses-raw"
    assert client.responses_create.calls == [params]
    assert client.chat_create.calls == []


@pytest.mark.criterion("C5")
def test_responses_request_with_assistant_turn_and_temperature():
    # oracle: requirement 7, hand-built — an assistant message keeps its role with an
    # output_text part; temperature is sent when given; no max_output_tokens, reasoning or
    # text key when those are not given
    messages = [
        {"role": "user", "content": "Earlier question", "cache_point": True},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Follow-up"},
    ]
    params = _adapter().build_params(messages=messages, model="gpt-5.6-terra", temperature=0.5)
    assert params == {
        "model": "gpt-5.6-terra",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Earlier question",
                                          "prompt_cache_breakpoint": EXPLICIT}]},
            {"role": "assistant", "content": [{"type": "output_text",
                                               "text": "Earlier answer"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "Follow-up"}]},
        ],
        "prompt_cache_options": EXPLICIT,
        "temperature": 0.5,
        # added by llm-refusals-and-store (requirement 7): Responses calls are not stored
        "store": False,
    }


@pytest.mark.criterion("C5")
def test_dict_response_format_with_a_cache_point_raises():
    # oracle: requirement 7 — a dict response_format with a cache point raises ValueError
    messages = [{"role": "user", "content": "FILING", "cache_point": True},
                {"role": "user", "content": "QUESTION"}]
    with pytest.raises(ValueError):
        _adapter().build_params(
            messages=messages, model="gpt-5.6-sol",
            response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": {}}},
        )


_ROLE_MAP = {"system": "developer", "user": "user", "assistant": "assistant"}
_PART_TYPE = {"system": "input_text", "user": "input_text", "assistant": "output_text"}
_TEXT = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=0x2FF), min_size=1,
                max_size=40)


@pytest.mark.criterion("C5")
@given(
    body=st.lists(st.tuples(st.sampled_from(["system", "user", "assistant"]), _TEXT),
                  min_size=1, max_size=7),
    marks=st.sets(st.integers(min_value=0, max_value=6), min_size=1, max_size=3),
    model=st.sampled_from(["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]),
)
def test_only_marked_parts_carry_a_breakpoint(fake_client, body, marks, model):
    # oracle: requirement 7 and OpenAI's prompt-caching guide — one input item per message,
    # roles mapped (system -> developer), input_text/output_text parts with the text
    # unchanged, a breakpoint exactly on the marked messages, and invoke uses Responses
    marked = {m % len(body) for m in marks}
    messages = []
    for i, (role, text) in enumerate(body):
        message: dict = {"role": role, "content": text}
        if i in marked:
            message["cache_point"] = True
        messages.append(message)
    params = _adapter().build_params(messages=messages, model=model)
    assert "messages" not in params
    assert params["prompt_cache_options"] == EXPLICIT
    items = params["input"]
    assert len(items) == len(body)
    for i, ((role, text), item) in enumerate(zip(body, items, strict=True)):
        assert item["role"] == _ROLE_MAP[role]
        part = {"type": _PART_TYPE[role], "text": text}
        if i in marked:
            part["prompt_cache_breakpoint"] = EXPLICIT
        assert item["content"] == [part]
    client = fake_client(chat_reply="chat-raw", responses_reply="responses-raw")
    assert _adapter().invoke(client, params) == "responses-raw"
    assert client.chat_create.calls == []
