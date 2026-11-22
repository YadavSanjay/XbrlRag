"""C1: the adapters report the model id the provider's response names.

Spec: docs/specs/served-model-id.md, requirement 1 and criterion C1.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.anthropic_adapter import AnthropicAdapter
from src.llm.gateway.adapters.base import LLMRefusalError, ParsedResponse
from src.llm.gateway.adapters.openai_adapter import OpenAIAdapter, OpenAICompatAdapter

# Non-empty, not whitespace-only: kept exactly as reported.
SERVED_IDS = st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != "")
# Whitespace-only (or empty) ids, which give None.
BLANK_IDS = st.text(alphabet=" \t\n\r\x0b\x0c", max_size=6)


def _has_field(parsed: ParsedResponse) -> bool:
    return "served_model" in {f.name for f in dataclasses.fields(ParsedResponse)} and \
        hasattr(parsed, "served_model")


def _others(parsed: ParsedResponse) -> dict:
    """Every ParsedResponse field except served_model."""
    return {f.name: getattr(parsed, f.name) for f in dataclasses.fields(parsed)
            if f.name != "served_model"}


def _openai() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="sk-spec-offline")


def _compat() -> OpenAIAdapter:
    return OpenAICompatAdapter(api_key="sk-spec-offline", base_url="http://127.0.0.1:9/v1")


def _anthropic() -> AnthropicAdapter:
    return AnthropicAdapter(api_key="sk-ant-spec-offline")


def _refusal_usage(adapter: AnthropicAdapter, raw) -> ParsedResponse:
    with pytest.raises(LLMRefusalError) as info:
        adapter.parse(raw, stage="spec")
    usage = info.value.usage
    assert usage is not None
    return usage


# ---------------------------------------------------------------------------- point oracles


@pytest.mark.criterion("C1")
def test_openai_chat_served_model_is_the_raw_model(chat_reply, absent):
    # oracle: the raw fake's own `model` attribute ("gpt-5-2025-08-07", the spec's example)
    adapter = _openai()
    parsed = adapter.parse(chat_reply(model="gpt-5-2025-08-07"), stage="spec")
    bare = adapter.parse(chat_reply(model=absent), stage="spec")
    assert getattr(parsed, "served_model", None) == "gpt-5-2025-08-07"
    assert _others(parsed) == _others(bare)


@pytest.mark.criterion("C1")
def test_openai_responses_served_model_is_the_raw_model(responses_reply, absent):
    # oracle: the raw fake's own `model` attribute
    adapter = _openai()
    parsed = adapter.parse(responses_reply(model="gpt-5.6-sol-2026-10-01"), stage="spec")
    bare = adapter.parse(responses_reply(model=absent), stage="spec")
    assert getattr(parsed, "served_model", None) == "gpt-5.6-sol-2026-10-01"
    assert _others(parsed) == _others(bare)


@pytest.mark.criterion("C1")
def test_anthropic_success_served_model_is_the_raw_model(anthropic_reply, absent):
    # oracle: the raw fake's own `model` attribute
    adapter = _anthropic()
    parsed = adapter.parse(anthropic_reply(model="claude-opus-5-5-20261001"), stage="spec")
    bare = adapter.parse(anthropic_reply(model=absent), stage="spec")
    assert getattr(parsed, "served_model", None) == "claude-opus-5-5-20261001"
    assert _others(parsed) == _others(bare)


@pytest.mark.criterion("C1")
def test_anthropic_refusal_usage_carries_the_raw_model(anthropic_reply, absent):
    # oracle: the raw fake's own `model` attribute, on the refusal's usage
    adapter = _anthropic()
    usage = _refusal_usage(adapter, anthropic_reply(
        "", model="claude-opus-5-5-20261001", stop_reason="refusal", category="bio",
        output_tokens=0))
    bare = _refusal_usage(adapter, anthropic_reply(
        "", model=absent, stop_reason="refusal", category="bio", output_tokens=0))
    assert getattr(usage, "served_model", None) == "claude-opus-5-5-20261001"
    assert _others(usage) == _others(bare)


@pytest.mark.criterion("C1")
def test_non_empty_value_is_kept_exactly_without_trimming_or_lowercasing(chat_reply,
                                                                         anthropic_reply):
    # oracle: the raw fake's own `model` attribute, verbatim (requirement 1: no
    # lowercasing or trimming of a non-empty value)
    reported = "  GPT-5-Mini-2025-08-07 \t"
    parsed = _openai().parse(chat_reply(model=reported), stage="spec")
    assert getattr(parsed, "served_model", None) == reported
    parsed = _anthropic().parse(anthropic_reply(model=reported), stage="spec")
    assert getattr(parsed, "served_model", None) == reported


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model", ["absent", None, "", "   ", "\t\n"])
def test_missing_none_empty_or_blank_model_gives_none(model, chat_reply, responses_reply,
                                                      anthropic_reply, absent):
    # oracle: requirement 1: a raw reply with no `model`, or None / "" / whitespace,
    # gives served_model = None (the field exists, its value is None), never an error
    raw_model = absent if model == "absent" else model
    for parsed in (
        _openai().parse(chat_reply(model=raw_model), stage="spec"),
        _openai().parse(responses_reply(model=raw_model), stage="spec"),
        _anthropic().parse(anthropic_reply(model=raw_model), stage="spec"),
        _refusal_usage(_anthropic(), anthropic_reply(
            "", model=raw_model, stop_reason="refusal", category="bio")),
    ):
        assert _has_field(parsed)
        assert parsed.served_model is None


# ---------------------------------------------------------------------------- properties


@pytest.mark.criterion("C1")
@given(served=SERVED_IDS, path=st.sampled_from(
    ["openai_chat", "openai_responses", "compat_chat", "compat_responses",
     "anthropic", "anthropic_refusal"]))
def test_served_model_equals_raw_model_for_any_non_empty_string(
        served, path, chat_reply, responses_reply, anthropic_reply, absent):
    # oracle: the raw fake's own `model` attribute; the other fields equal those of the
    # same reply parsed without a `model` attribute
    def parse(model):
        if path == "openai_chat":
            return _openai().parse(chat_reply(model=model), stage="spec")
        if path == "openai_responses":
            return _openai().parse(responses_reply(model=model), stage="spec")
        if path == "compat_chat":
            return _compat().parse(chat_reply(model=model), stage="spec")
        if path == "compat_responses":
            return _compat().parse(responses_reply(model=model), stage="spec")
        if path == "anthropic":
            return _anthropic().parse(anthropic_reply(model=model), stage="spec")
        return _refusal_usage(_anthropic(), anthropic_reply(
            "", model=model, stop_reason="refusal", category="frontier_llm"))

    parsed = parse(served)
    assert getattr(parsed, "served_model", None) == served
    assert _others(parsed) == _others(parse(absent))


@pytest.mark.criterion("C1")
@given(blank=BLANK_IDS, path=st.sampled_from(["openai_chat", "openai_responses",
                                              "anthropic", "anthropic_refusal"]))
def test_blank_model_gives_none_and_leaves_other_fields_unchanged(
        blank, path, chat_reply, responses_reply, anthropic_reply, absent):
    # oracle: requirement 1: empty or whitespace-only `model` gives None; other fields
    # equal those of the same reply parsed without a `model` attribute
    def parse(model):
        if path == "openai_chat":
            return _openai().parse(chat_reply(model=model), stage="spec")
        if path == "openai_responses":
            return _openai().parse(responses_reply(model=model), stage="spec")
        if path == "anthropic":
            return _anthropic().parse(anthropic_reply(model=model), stage="spec")
        return _refusal_usage(_anthropic(), anthropic_reply(
            "", model=model, stop_reason="refusal", category="bio"))

    parsed = parse(blank)
    assert _has_field(parsed)
    assert parsed.served_model is None
    assert _others(parsed) == _others(parse(absent))
