"""C6: OpenAI Responses calls are not stored (requirement 7).

The adapter is the real ``OpenAIAdapter``; ``build_params`` makes no call. The rest of the
Responses dict is written by hand from ``llm-anthropic-and-caching`` requirement 7 (roles
mapped system -> developer, ``input_text``/``output_text`` parts, an explicit breakpoint on
each marked message, explicit cache options, then the optional keys, ``extra`` last).
OpenAI's Responses API reference: ``store`` defaults to ``true``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters import OpenAIAdapter

EXPLICIT = {"mode": "explicit"}


def _adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="sk-spec-offline")


@pytest.mark.criterion("C6")
def test_responses_request_sends_store_false_and_chat_does_not():
    # oracle: requirement 7, hand-built — the Responses dict with "store": False; the same
    # request without a cache point (Chat Completions) has no store key
    marked = [
        {"role": "system", "content": "You answer from the filing."},
        {"role": "user", "content": "FILING", "cache_point": True},
        {"role": "user", "content": "QUESTION"},
    ]
    params = _adapter().build_params(messages=marked, model="gpt-5.6-sol",
                                     max_completion_tokens=8000, response_format="json")
    assert params == {
        "model": "gpt-5.6-sol",
        "input": [
            {"role": "developer",
             "content": [{"type": "input_text", "text": "You answer from the filing."}]},
            {"role": "user",
             "content": [{"type": "input_text", "text": "FILING",
                          "prompt_cache_breakpoint": EXPLICIT}]},
            {"role": "user", "content": [{"type": "input_text", "text": "QUESTION"}]},
        ],
        "prompt_cache_options": EXPLICIT,
        "max_output_tokens": 8000,
        "text": {"format": {"type": "json_object"}},
        "store": False,
    }
    plain = [{k: v for k, v in m.items() if k != "cache_point"} for m in marked]
    chat = _adapter().build_params(messages=plain, model="gpt-5.6-sol",
                                   max_completion_tokens=8000, response_format="json")
    assert "store" not in chat
    assert "messages" in chat


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_extra_store_true_is_applied_last():
    # oracle: requirement 7 — store is False by default on the Responses path; passing
    # extra={"store": True} flips only that key (extra is applied last)
    messages = [{"role": "user", "content": "FILING", "cache_point": True},
                {"role": "user", "content": "QUESTION"}]
    base = _adapter().build_params(messages=messages, model="gpt-5.6-terra")
    overridden = _adapter().build_params(messages=messages, model="gpt-5.6-terra",
                                         extra={"store": True})
    assert base.get("store") is False
    assert overridden.get("store") is True
    assert {k: v for k, v in overridden.items() if k != "store"} == \
        {k: v for k, v in base.items() if k != "store"}


_ROLE_MAP = {"system": "developer", "user": "user", "assistant": "assistant"}
_PART_TYPE = {"system": "input_text", "user": "input_text", "assistant": "output_text"}
_TEXT = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=0x2FF), min_size=1,
                max_size=40)


@pytest.mark.criterion("C6")
@given(
    body=st.lists(st.tuples(st.sampled_from(["system", "user", "assistant"]), _TEXT),
                  min_size=1, max_size=7),
    marks=st.sets(st.integers(min_value=0, max_value=6), min_size=1, max_size=3),
    model=st.sampled_from(["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]),
    max_tokens=st.one_of(st.none(), st.integers(min_value=1, max_value=128_000)),
    temperature=st.one_of(st.none(), st.sampled_from([0.0, 0.5, 1.0])),
    effort=st.one_of(st.none(), st.sampled_from(["low", "medium", "high"])),
    json_mode=st.booleans(),
)
def test_any_responses_request_has_store_false(body, marks, model, max_tokens, temperature,
                                               effort, json_mode):
    # oracle: requirement 7 here (store False) and llm-anthropic-and-caching requirement 7
    # for the rest of the dict, built by hand below
    marked = {m % len(body) for m in marks}
    messages = []
    expected_input = []
    for i, (role, text) in enumerate(body):
        message: dict = {"role": role, "content": text}
        part = {"type": _PART_TYPE[role], "text": text}
        if i in marked:
            message["cache_point"] = True
            part["prompt_cache_breakpoint"] = EXPLICIT
        messages.append(message)
        expected_input.append({"role": _ROLE_MAP[role], "content": [part]})
    expected: dict = {"model": model, "input": expected_input,
                      "prompt_cache_options": EXPLICIT}
    if max_tokens is not None:
        expected["max_output_tokens"] = max_tokens
    if temperature is not None:
        expected["temperature"] = temperature
    if effort is not None:
        expected["reasoning"] = {"effort": effort}
    if json_mode:
        expected["text"] = {"format": {"type": "json_object"}}

    params = _adapter().build_params(
        messages=messages, model=model, max_completion_tokens=max_tokens,
        temperature=temperature, reasoning_effort=effort,
        response_format="json" if json_mode else None,
    )
    assert params.get("store") is False
    assert {k: v for k, v in params.items() if k != "store"} == expected
