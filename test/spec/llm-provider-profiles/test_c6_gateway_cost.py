"""C6: no $0 calls — the gateway prices every call, cache writes included.

Oracle: requirement 7, with expected costs from requirement 6's closed form over C5's table
of committed rates. The provider adapter is the fake in conftest.py (the I/O boundary),
behind a real ``LLMGateway`` and ``CostTracker``.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.cost_tracker import CostTracker
from src.llm.gateway.adapters import AnthropicAdapter
from src.llm.gateway.adapters.base import ParsedResponse
from src.observability.trace_context import trace_query

# C5's table, USD per 1,000 tokens.
OPUS = {"input": 0.004, "output": 0.02, "cached_input": 0.0002, "cache_write": 0.005}
MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
# Exact to 1e-12 relative (C5): a handful of float products and sums.
EXACT = 1e-12


def closed_form(rates: dict, prompt: int, completion: int, cached: int, written: int) -> float:
    """Requirement 6 for a row without tiers."""
    uncached = prompt - cached - written
    return (
        uncached * rates["input"] + cached * rates["cached_input"]
        + written * rates["cache_write"] + completion * rates["output"]
    ) / 1000


def _llm_calls(trace) -> list[dict]:
    return [e for e in trace.events if e.get("kind") == "llm_call"]


@pytest.mark.criterion("C6")
def test_parsed_response_has_cache_write_tokens():
    # oracle: requirement 7 — ParsedResponse gains cache_write_tokens, defaulting to 0 like
    # cached_prompt_tokens
    names = {f.name for f in dataclasses.fields(ParsedResponse)}
    assert "cache_write_tokens" in names
    parsed = ParsedResponse(content="x", finish_reason="stop", prompt_tokens=3, completion_tokens=1)
    assert parsed.cache_write_tokens == 0


@pytest.mark.criterion("C6")
def test_gateway_prices_cache_writes_and_records_them(llm_env, make_gateway):
    # oracle: requirement 7 + C5's sonnet rates — 10000 prompt (3000 cached, 2000 written) and
    # 500 completion: (5000*0.002 + 3000*0.0001 + 2000*0.0025 + 500*0.01) / 1000 = 0.0203
    # (llm-pricing-refresh C1: Sonnet 5.5 cache reads are 0.0001 per 1K; were 0.0002, 0.0206)
    assert "cache_write_tokens" in {f.name for f in dataclasses.fields(ParsedResponse)}
    gateway, adapter, tracker = make_gateway(
        prompt_tokens=10_000, completion_tokens=500, cached_prompt_tokens=3000,
        cache_write_tokens=2000,
    )
    with trace_query("c6") as trace:
        result = gateway.complete(messages=MESSAGES, model="claude-sonnet-5-5", stage="spec")
    assert adapter.calls == 1
    assert result.cost_usd == pytest.approx(0.0203, rel=EXACT)  # exact up to float rounding
    assert tracker.get_summary()["total_cost_usd"] == pytest.approx(0.0203, rel=EXACT)  # same sum
    assert trace.total_cost_usd == pytest.approx(0.0203, rel=EXACT)  # same sum
    events = _llm_calls(trace)
    assert len(events) == 1
    assert events[0]["outcome"] == "ok"
    assert events[0]["cache_write_tokens"] == 2000
    assert events[0]["cached_prompt_tokens"] == 3000


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("model", ["claude-zz-invented-9", "gpt-zz-invented-9"])
def test_unpriced_model_raises_and_records_no_cost(llm_env, make_gateway, model):
    # oracle: requirement 7 — a model with no pricing row raises ValueError; nothing is
    # recorded at $0 (no usage record, no successful llm_call event, no trace cost)
    gateway, adapter, tracker = make_gateway(prompt_tokens=100, completion_tokens=10)
    with trace_query("c6-unpriced") as trace:
        with pytest.raises(ValueError):
            gateway.complete(messages=MESSAGES, model=model, stage="spec")
    assert tracker.get_summary()["total_calls"] == 0
    assert trace.llm_calls == 0
    assert all(e.get("outcome") != "ok" for e in _llm_calls(trace))


@pytest.mark.criterion("C6")
def test_cost_tracker_rejects_an_unpriced_model():
    # oracle: requirement 7 — the cost tracker never records $0 for an unpriced model
    tracker = CostTracker()
    with pytest.raises(ValueError):
        tracker.record_usage(model="gpt-zz-invented-9", prompt_tokens=100, completion_tokens=10)
    assert tracker.get_summary()["total_calls"] == 0


@pytest.mark.criterion("C6")
def test_anthropic_adapter_maps_cache_creation_tokens():
    # oracle: requirement 7 — the Anthropic adapter maps usage.cache_creation_input_tokens to
    # cache_write_tokens; cache reads stay cached_prompt_tokens, and both are part of the
    # prompt (requirement 6: cached and written are counts out of prompt_tokens)
    raw = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"ok": true}')],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=100, output_tokens=7, cache_read_input_tokens=30,
                              cache_creation_input_tokens=20),
    )
    parsed = AnthropicAdapter(api_key="sk-ant-spec-offline").parse(raw, stage="spec")
    assert getattr(parsed, "cache_write_tokens", None) == 20
    assert parsed.cached_prompt_tokens == 30
    assert parsed.prompt_tokens == 150


@pytest.mark.criterion("C6")
@given(
    fresh=st.integers(min_value=0, max_value=200_000),
    read=st.integers(min_value=0, max_value=200_000),
    created=st.integers(min_value=0, max_value=200_000),
)
def test_anthropic_adapter_any_usage(fresh, read, created):
    # oracle: requirement 7 — for any usage block, written = cache_creation_input_tokens and
    # the prompt holds all three counts
    raw = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="x")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=fresh, output_tokens=1, cache_read_input_tokens=read,
                              cache_creation_input_tokens=created),
    )
    parsed = AnthropicAdapter(api_key="sk-ant-spec-offline").parse(raw, stage="spec")
    assert getattr(parsed, "cache_write_tokens", None) == created
    assert parsed.prompt_tokens == fresh + read + created


@st.composite
def _usage(draw):
    prompt = draw(st.integers(min_value=0, max_value=500_000))
    cached = draw(st.integers(min_value=0, max_value=prompt))
    written = draw(st.integers(min_value=0, max_value=prompt - cached))
    completion = draw(st.integers(min_value=0, max_value=100_000))
    return prompt, completion, cached, written


@pytest.mark.criterion("C6")
@given(usage=_usage())
def test_any_call_records_the_closed_form_cost(make_gateway, usage):
    # oracle: requirement 7 — every call is priced by requirement 6 over the model's committed
    # rates (C5's opus row); the llm_call event carries the written tokens
    prompt, completion, cached, written = usage
    assert "cache_write_tokens" in {f.name for f in dataclasses.fields(ParsedResponse)}
    gateway, _, tracker = make_gateway(
        prompt_tokens=prompt, completion_tokens=completion, cached_prompt_tokens=cached,
        cache_write_tokens=written,
    )
    with trace_query("c6-any") as trace:
        result = gateway.complete(messages=MESSAGES, model="claude-opus-5-5", stage="spec")
    want = closed_form(OPUS, prompt, completion, cached, written)
    assert result.cost_usd == pytest.approx(want, rel=EXACT, abs=1e-15)  # exact up to rounding
    assert tracker.get_summary()["total_cost_usd"] == pytest.approx(want, rel=EXACT, abs=1e-15)
    assert _llm_calls(trace)[0]["cache_write_tokens"] == written
