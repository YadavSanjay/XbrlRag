"""C6: OpenAI Responses replies (requirement 8) and their cost.

``parse`` maps a Responses reply per requirement 8's table. The gateway-cost tests drive
the real ``OpenAIAdapter`` (Responses path, through a cache-point request) behind a real
``LLMGateway``; only the SDK client is a fake that returns a hand-built Responses reply.
Expected costs are llm-provider-profiles' closed form over gpt-5.6-sol's committed rates
(configs/llm/pricing.yaml, USD per 1,000 tokens).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters import OpenAIAdapter
from src.observability.trace_context import trace_query

# gpt-5.6-sol's committed rates in configs/llm/pricing.yaml, USD per 1,000 tokens.
SOL = {"input": 0.004, "output": 0.02, "cached_input": 0.0004, "cache_write": 0.005}
CACHED_REQUEST = [
    {"role": "system", "content": "You answer from the filing."},
    {"role": "user", "content": "FILING", "cache_point": True},
    {"role": "user", "content": "QUESTION"},
]
# A handful of float products and sums: exact up to rounding, 1e-12 relative.
EXACT = 1e-12


def _adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="sk-spec-offline")


def _parse(raw):
    """parse, turning today's "no choices" ValueError into an assertion failure."""
    try:
        return _adapter().parse(raw, stage="spec")
    except ValueError as exc:
        pytest.fail(f"the Responses reply was not parsed: {exc!r}")


def closed_form(prompt: int, completion: int, cached: int, written: int) -> float:
    """llm-provider-profiles' cost rule for a row without tiers."""
    uncached = prompt - cached - written
    return (uncached * SOL["input"] + cached * SOL["cached_input"]
            + written * SOL["cache_write"] + completion * SOL["output"]) / 1000


@pytest.mark.criterion("C6")
def test_completed_reply_maps_every_field(responses_reply):
    # oracle: requirement 8's table, hand-built reply
    raw = responses_reply(
        '{"value": 391035}', input_tokens=100_000, cached_tokens=9000,
        cache_write_tokens=1000, output_tokens=2500, reasoning_tokens=1800,
    )
    parsed = _parse(raw)
    assert parsed.content == '{"value": 391035}'
    assert parsed.finish_reason == "stop"
    assert parsed.prompt_tokens == 100_000
    assert parsed.cached_prompt_tokens == 9000
    assert parsed.cache_write_tokens == 1000
    assert parsed.completion_tokens == 2500
    assert parsed.reasoning_tokens == 1800


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("details", ["present", "none", "absent"])
def test_missing_details_count_as_zero(responses_reply, details):
    # oracle: requirement 8 — missing details (empty, None or absent) count as 0
    raw = responses_reply("answer", input_tokens=500, output_tokens=40, details=details)
    parsed = _parse(raw)
    assert parsed.content == "answer"
    assert parsed.prompt_tokens == 500
    assert parsed.completion_tokens == 40
    assert parsed.cached_prompt_tokens == 0
    assert parsed.cache_write_tokens == 0
    assert parsed.reasoning_tokens == 0


@pytest.mark.criterion("C6")
def test_incomplete_reply_is_length(responses_reply):
    # oracle: requirement 8 — status "incomplete" with reason max_output_tokens gives
    # finish_reason "length"
    raw = responses_reply('{"value": 1', status="incomplete",
                          incomplete_reason="max_output_tokens", input_tokens=10,
                          output_tokens=8000)
    parsed = _parse(raw)
    assert parsed.finish_reason == "length"
    assert parsed.content == '{"value": 1'
    assert parsed.completion_tokens == 8000


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_emptying_output_text_turns_a_reply_into_value_error(responses_reply):
    # oracle: requirement 8 — the same reply parses with text and raises ValueError once
    # output_text is empty
    assert _parse(responses_reply("ok", input_tokens=5, output_tokens=1)).content == "ok"
    with pytest.raises(ValueError):
        _adapter().parse(responses_reply("", input_tokens=5, output_tokens=1), stage="spec")


@pytest.mark.criterion("C6")
def test_gateway_prices_a_cached_responses_call(fake_client, client_openai_adapter,
                                                make_gateway, responses_reply):
    # oracle: llm-provider-profiles' cost rule over gpt-5.6-sol's committed rates — 100,000
    # prompt (9,000 read, 1,000 written), 2,000 completion:
    # (90,000*0.004 + 9,000*0.0004 + 1,000*0.005 + 2,000*0.02) / 1000 = 0.4086
    raw = responses_reply("{}", input_tokens=100_000, cached_tokens=9000,
                          cache_write_tokens=1000, output_tokens=2000)
    client = fake_client(responses_reply=raw)
    gateway = make_gateway(client_openai_adapter(client), max_attempts=1)
    with trace_query("c6-cost") as trace:
        result = gateway.complete(messages=CACHED_REQUEST, model="gpt-5.6-sol", stage="spec")
    assert len(client.responses_create.calls) == 1
    assert client.chat_create.calls == []
    assert result.cached_prompt_tokens == 9000
    assert result.cache_write_tokens == 1000
    assert result.cost_usd == pytest.approx(0.4086, rel=EXACT)  # exact up to float rounding
    assert trace.total_cost_usd == pytest.approx(0.4086, rel=EXACT)  # same sum


@pytest.mark.criterion("C6")
@given(
    prompt=st.integers(min_value=0, max_value=250_000),
    cached_share=st.integers(min_value=0, max_value=100),
    written_share=st.integers(min_value=0, max_value=100),
    completion=st.integers(min_value=0, max_value=16_000),
)
def test_cost_over_any_usage_is_the_closed_form(fake_client, client_openai_adapter,
                                                make_gateway, responses_reply, prompt,
                                                cached_share, written_share, completion):
    # oracle: llm-provider-profiles' cost rule, closed form over gpt-5.6-sol's committed rates
    cached = prompt * cached_share // 100
    written = (prompt - cached) * written_share // 100
    raw = responses_reply("{}", input_tokens=prompt, cached_tokens=cached,
                          cache_write_tokens=written, output_tokens=completion)
    client = fake_client(responses_reply=raw)
    gateway = make_gateway(client_openai_adapter(client), max_attempts=1)
    with trace_query("c6-prop"):
        result = gateway.complete(messages=CACHED_REQUEST, model="gpt-5.6-sol", stage="spec")
    assert client.chat_create.calls == []
    expected = closed_form(prompt, completion, cached, written)
    # a few float products and sums: exact to 1e-12 relative, 1e-15 absolute near zero
    assert result.cost_usd == pytest.approx(expected, rel=EXACT, abs=1e-15)
