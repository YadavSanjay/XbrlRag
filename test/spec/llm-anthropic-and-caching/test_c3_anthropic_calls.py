"""C3: Anthropic calls and replies (requirement 3).

``invoke`` always streams (``messages.stream`` then ``get_final_message``) and never
calls ``messages.create``; ``parse`` joins the text blocks, skips thinking blocks, maps
usage (cache read and creation as in llm-provider-profiles), raises ``LLMRefusalError`` on
a refusal and ``ValueError`` on a reply with no text. The client and replies are fakes
(conftest.py); the adapter is real.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters import AnthropicAdapter
from src.llm.gateway.adapters.base import LLMRefusalError


def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(api_key="sk-ant-spec-offline")


def _raised(call) -> BaseException | None:
    """The exception *call* raised, or None (so a wrong exception type fails an assert)."""
    try:
        call()
    except Exception as exc:  # the caller asserts on the type
        return exc
    return None


def _stream_round_trip(fake_client, reply, params=None):
    """invoke on a fake client streaming *reply*, then parse; returns (client, raw, parsed)."""
    client = fake_client(stream_reply=reply)
    params = params or {"model": "claude-opus-5-5", "max_tokens": 16000,
                        "messages": [{"role": "user", "content": "hi"}]}
    raw = _adapter().invoke(client, params)
    assert client.create.calls == []
    parsed = _adapter().parse(raw, stage="spec")
    return client, raw, parsed


@pytest.mark.criterion("C3")
def test_invoke_streams_once_and_returns_the_final_message(fake_client, anthropic_reply):
    # oracle: requirement 3 — one messages.stream call with exactly the params, the final
    # message returned as is, messages.create never touched
    reply = anthropic_reply([("text", "ok")])
    client = fake_client(stream_reply=reply)
    params = {
        "model": "claude-opus-5-5",
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"effort": "high"},
    }
    raw = _adapter().invoke(client, params)
    assert client.create.calls == []
    assert client.stream.calls == [params]
    assert client.stream.final_calls == 1
    assert raw is reply


@pytest.mark.criterion("C3")
def test_streamed_reply_parses_text_skips_thinking_and_maps_usage(fake_client, anthropic_reply):
    # oracle: requirement 3 + llm-provider-profiles' usage rule — text blocks joined in
    # order, thinking skipped; prompt = 100 input + 30 read + 20 created = 150, cached 30,
    # written 20, completion 7
    reply = anthropic_reply(
        [("thinking", "Let me look at the income statement."), ("text", '{"value": '),
         ("thinking", "Units are millions."), ("text", "391035}")],
        input_tokens=100, output_tokens=7, cache_read=30, cache_creation=20,
    )
    _, _, parsed = _stream_round_trip(fake_client, reply)
    assert parsed.content == '{"value": 391035}'
    assert parsed.finish_reason == "end_turn"
    assert parsed.prompt_tokens == 150
    assert parsed.cached_prompt_tokens == 30
    assert parsed.cache_write_tokens == 20
    assert parsed.completion_tokens == 7


@pytest.mark.criterion("C3")
def test_refusal_raises_llm_refusal_error_with_its_category(anthropic_reply):
    # oracle: requirement 3 — stop_reason "refusal" raises LLMRefusalError (a ValueError),
    # carrying the reply's refusal category when there is one; with or without text
    with_text = anthropic_reply(
        [("text", "I can't help with that.")], stop_reason="refusal",
        stop_details=SimpleNamespace(type="refusal", category="cyber", explanation=None),
    )
    exc = _raised(lambda: _adapter().parse(with_text, stage="spec"))
    assert isinstance(exc, LLMRefusalError)
    assert isinstance(exc, ValueError)
    assert "cyber" in str(exc)

    no_text = anthropic_reply([], stop_reason="refusal", stop_details=None)
    assert isinstance(_raised(lambda: _adapter().parse(no_text, stage="spec")), LLMRefusalError)


@pytest.mark.criterion("C3")
def test_reply_with_no_text_raises_value_error_not_refusal(fake_client, anthropic_reply):
    # oracle: requirement 3 — a streamed reply with only thinking blocks, or blank text,
    # has no text: ValueError as today, and not a refusal
    for blocks in ([("thinking", "hmm")], [("thinking", "hmm"), ("text", "   ")]):
        client = fake_client(stream_reply=anthropic_reply(blocks))
        raw = _adapter().invoke(client, {"model": "claude-opus-5-5", "max_tokens": 10,
                                         "messages": [{"role": "user", "content": "hi"}]})
        assert client.create.calls == []
        exc = _raised(lambda raw=raw: _adapter().parse(raw, stage="spec"))
        assert isinstance(exc, ValueError)
        assert not isinstance(exc, LLMRefusalError)


_WORD = st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1,
                max_size=12)


@pytest.mark.criterion("C3")
@given(
    blocks=st.lists(st.tuples(st.sampled_from(["text", "thinking"]), _WORD), max_size=8),
    anchor=_WORD,
    position=st.integers(min_value=0, max_value=8),
    usage=st.tuples(*(st.integers(min_value=0, max_value=50_000) for _ in range(4))),
)
def test_content_is_the_joined_text_blocks(fake_client, anthropic_reply, blocks, anchor,
                                           position, usage):
    # oracle: requirement 3 — content is the in-order concatenation of the text blocks
    # (thinking blocks dropped); usage maps as in llm-provider-profiles
    blocks = list(blocks)
    blocks.insert(position % (len(blocks) + 1), ("text", anchor))  # at least one text block
    fresh, output, read, created = usage
    reply = anthropic_reply(blocks, input_tokens=fresh, output_tokens=output,
                            cache_read=read, cache_creation=created)
    _, _, parsed = _stream_round_trip(fake_client, reply)
    assert parsed.content == "".join(text for kind, text in blocks if kind == "text")
    assert parsed.prompt_tokens == fresh + read + created
    assert parsed.cached_prompt_tokens == read
    assert parsed.cache_write_tokens == created
    assert parsed.completion_tokens == output
