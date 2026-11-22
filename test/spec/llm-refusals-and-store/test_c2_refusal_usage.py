"""C2: a refusal carries its usage (requirement 2).

The adapter is the real ``AnthropicAdapter``; its input is a fake raw reply (conftest.py).
The usage mapping oracle is ``llm-anthropic-and-caching`` C3: prompt tokens are input plus
cache reads plus cache writes, and the cache counts are subsets of the prompt.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters import AnthropicAdapter
from src.llm.gateway.adapters.base import LLMRefusalError

BILLED = ("bio", "frontier_llm", "reasoning_extraction")


def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(api_key="sk-ant-spec-offline")


def _refusal_from(reply) -> LLMRefusalError:
    with pytest.raises(LLMRefusalError) as caught:
        _adapter().parse(reply, stage="spec")
    return caught.value


@pytest.mark.criterion("C2")
def test_bio_refusal_carries_usage_and_is_billed(anthropic_reply):
    # oracle: the spec's C2 example by hand — prompt 1000 + 9000 + 500 = 10500, cached
    # 9000, written 500, completion 0; category bio is billed (requirement 1)
    reply = anthropic_reply(
        [], stop_reason="refusal", stop_details=SimpleNamespace(category="bio"),
        input_tokens=1000, cache_read=9000, cache_creation=500, output_tokens=0,
    )
    exc = _refusal_from(reply)
    assert getattr(exc, "category", None) == "bio"
    usage = getattr(exc, "usage", None)
    assert usage is not None
    assert usage.prompt_tokens == 10500
    assert usage.cached_prompt_tokens == 9000
    assert usage.cache_write_tokens == 500
    assert usage.completion_tokens == 0
    assert usage.content == ""
    assert getattr(exc, "billed", None) is True
    # requirement 2: the message text is unchanged
    assert str(exc) == "the model declined to answer (refusal category: bio)"


@pytest.mark.criterion("C2")
def test_cyber_refusal_is_not_billed_but_keeps_usage(anthropic_reply):
    # oracle: requirement 1 (cyber with 0 output is not billed) and requirement 2 (usage
    # is carried either way): prompt 1000 + 9000 + 500 = 10500
    reply = anthropic_reply(
        [], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
        input_tokens=1000, cache_read=9000, cache_creation=500, output_tokens=0,
    )
    exc = _refusal_from(reply)
    assert getattr(exc, "category", None) == "cyber"
    assert getattr(exc, "billed", None) is False
    usage = getattr(exc, "usage", None)
    assert usage is not None
    assert usage.prompt_tokens == 10500


@pytest.mark.criterion("C2")
def test_refusal_without_stop_details_has_no_category(anthropic_reply):
    # oracle: requirement 2 — no stop_details means category None; None with 0 output is
    # not billed (requirement 1); prompt 40 + 0 + 0 = 40
    reply = anthropic_reply([], stop_reason="refusal", stop_details=None,
                            input_tokens=40, output_tokens=0)
    exc = _refusal_from(reply)
    assert hasattr(exc, "category")
    assert exc.category is None
    assert getattr(exc, "billed", None) is False
    usage = getattr(exc, "usage", None)
    assert usage is not None
    assert usage.prompt_tokens == 40
    assert str(exc) == "the model declined to answer (refusal)"


@pytest.mark.criterion("C2")
def test_message_only_constructor_keeps_working():
    # oracle: requirement 2 — LLMRefusalError("x") has category None, usage None and
    # billed False, and its message is unchanged
    exc = LLMRefusalError("x")
    assert str(exc) == "x"
    assert hasattr(exc, "category")
    assert hasattr(exc, "usage")
    assert exc.category is None
    assert exc.usage is None
    assert getattr(exc, "billed", None) is False


_COUNT = st.integers(min_value=0, max_value=2_000_000)


@pytest.mark.criterion("C2")
@given(
    input_tokens=_COUNT, cache_read=_COUNT, cache_creation=_COUNT,
    output_tokens=st.integers(min_value=0, max_value=200_000),
    category=st.one_of(st.none(), st.sampled_from(BILLED + ("cyber", "general_harms"))),
)
def test_refusal_usage_matches_a_successful_reply(input_tokens, cache_read, cache_creation,
                                                 output_tokens, category):
    # oracle: requirement 2 and llm-anthropic-and-caching C3 — prompt = input + cache_read +
    # cache_creation (closed form), and the refusal's usage equals what parse gives a
    # successful reply with the same usage, with content ""; billed by requirement 1
    details = SimpleNamespace(category=category) if category is not None else None
    usage_fields = dict(input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read_input_tokens=cache_read,
                        cache_creation_input_tokens=cache_creation)
    refused = SimpleNamespace(
        content=[], stop_reason="refusal", stop_details=details,
        usage=SimpleNamespace(**usage_fields),
    )
    ok = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="answer")], stop_reason="end_turn",
        stop_details=None, usage=SimpleNamespace(**usage_fields),
    )
    exc = _refusal_from(refused)
    success = _adapter().parse(ok, stage="spec")
    usage = getattr(exc, "usage", None)
    assert usage is not None
    assert usage.prompt_tokens == input_tokens + cache_read + cache_creation
    assert usage.prompt_tokens == success.prompt_tokens
    assert usage.completion_tokens == success.completion_tokens == output_tokens
    assert usage.cached_prompt_tokens == success.cached_prompt_tokens == cache_read
    assert usage.cache_write_tokens == success.cache_write_tokens == cache_creation
    assert usage.content == ""
    assert getattr(exc, "category", None) == category
    assert getattr(exc, "billed", None) is (output_tokens > 0 or category in BILLED)
