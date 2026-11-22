"""C2: the gateway carries the served id onto its result and its llm_call events.

Spec: docs/specs/served-model-id.md, requirement 2 and criterion C2.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway import LLMCallResult, LLMRefusalError
from src.observability.trace_context import trace_query

# A requested id with a committed pricing row, and a served id with none (spec Notes).
REQUESTED = "gpt-5-mini"
UNPRICED_SERVED = "gpt-5-mini-2099-01-01"
MESSAGES = [{"role": "system", "content": "You are a test."},
            {"role": "user", "content": "Say ok."}]

SERVED_IDS = st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != "")


def _llm_calls(trace) -> list[dict]:
    return [e for e in trace.events if e.get("kind") == "llm_call"]


def _expected_cost(row: dict, prompt_tokens: int, completion_tokens: int) -> float:
    """USD from the committed row (USD per 1,000 tokens), no cache tokens."""
    return prompt_tokens / 1000.0 * row["input"] + completion_tokens / 1000.0 * row["output"]


# ---------------------------------------------------------------------------- point oracles


@pytest.mark.criterion("C2")
def test_result_keeps_requested_model_and_carries_served_model(scripted_adapter,
                                                               make_gateway):
    # oracle: the fake adapter's configured served id and the requested id passed in
    gateway = make_gateway(scripted_adapter([("ok", "gpt-5-mini-2025-08-07")]))
    with trace_query("q") as trace:
        result = gateway.complete(messages=MESSAGES, model=REQUESTED, stage="spec")
    assert "served_model" in {f.name for f in dataclasses.fields(LLMCallResult)}
    assert result.model == REQUESTED
    assert getattr(result, "served_model", None) == "gpt-5-mini-2025-08-07"
    events = _llm_calls(trace)
    assert len(events) == 1
    assert events[0]["outcome"] == "ok"
    assert events[0]["model"] == REQUESTED
    assert events[0].get("served_model") == "gpt-5-mini-2025-08-07"


@pytest.mark.criterion("C2")
def test_refusal_event_carries_the_refusal_usage_served_model(scripted_adapter,
                                                              make_gateway):
    # oracle: the served id the fake refusal's usage was configured with
    gateway = make_gateway(scripted_adapter([("refusal", "gpt-5-mini-2025-08-07")]))
    with trace_query("q") as trace:
        with pytest.raises(LLMRefusalError):
            gateway.complete(messages=MESSAGES, model=REQUESTED, stage="spec")
    events = _llm_calls(trace)
    assert len(events) == 1
    assert events[0]["outcome"] == "refusal"
    assert events[0]["model"] == REQUESTED
    assert events[0].get("served_model") == "gpt-5-mini-2025-08-07"


@pytest.mark.criterion("C2")
def test_error_event_carries_served_model_none(scripted_adapter, make_gateway):
    # oracle: requirement 2: an error has no response, so its event's served_model is None
    gateway = make_gateway(scripted_adapter([("error", None)]))
    with trace_query("q") as trace:
        with pytest.raises(ValueError):
            gateway.complete(messages=MESSAGES, model=REQUESTED, stage="spec")
    events = _llm_calls(trace)
    assert len(events) == 1
    assert events[0]["outcome"] == "error"
    assert "served_model" in events[0]
    assert events[0]["served_model"] is None


@pytest.mark.criterion("C2")
def test_cost_uses_requested_id_even_when_served_id_is_unpriced(scripted_adapter,
                                                                make_gateway, pricing_row):
    # oracle: the committed pricing row for the requested id (gpt-5-mini):
    # 1000 prompt tokens x input + 100 completion tokens x output, per 1,000 tokens
    assert pricing_row(UNPRICED_SERVED) is None  # the served id really has no row
    expected = _expected_cost(pricing_row(REQUESTED), 1000, 100)
    for served in (None, UNPRICED_SERVED):
        gateway = make_gateway(scripted_adapter([("ok", served)], prompt_tokens=1000,
                                                completion_tokens=100))
        with trace_query("q") as trace:
            result = gateway.complete(messages=MESSAGES, model=REQUESTED, stage="spec")
        # tolerance: float rounding of a two-term sum
        assert result.cost_usd == pytest.approx(expected, rel=1e-9)
        assert getattr(result, "served_model", None) == served
        event = _llm_calls(trace)[0]
        assert "served_model" in event
        assert event["served_model"] == served


# ---------------------------------------------------------------------------- properties


@pytest.mark.criterion("C2")
@given(served=SERVED_IDS, prompt_tokens=st.integers(0, 50_000),
       completion_tokens=st.integers(0, 5_000))
def test_any_served_id_is_carried_and_never_changes_cost(
        served, prompt_tokens, completion_tokens, scripted_adapter, make_gateway,
        pricing_row):
    # oracle: the fake's configured served id; cost from the committed requested-id row
    # (metamorphic: same call with served None costs the same)
    costs = []
    for value in (served, None):
        gateway = make_gateway(scripted_adapter([("ok", value)], prompt_tokens=prompt_tokens,
                                                completion_tokens=completion_tokens))
        with trace_query("q") as trace:
            result = gateway.complete(messages=MESSAGES, model=REQUESTED, stage="spec")
        assert result.model == REQUESTED
        assert getattr(result, "served_model", None) == value
        event = _llm_calls(trace)[0]
        assert "served_model" in event
        assert event["served_model"] == value
        costs.append(result.cost_usd)
    expected = _expected_cost(pricing_row(REQUESTED), prompt_tokens, completion_tokens)
    # tolerance: float rounding of a two-term sum
    assert costs[0] == pytest.approx(expected, rel=1e-9, abs=1e-15)
    assert costs[0] == pytest.approx(costs[1], rel=1e-12, abs=1e-15)  # same arithmetic


@pytest.mark.criterion("C2")
@given(steps=st.lists(
    st.tuples(st.sampled_from(["ok", "refusal", "error"]), st.one_of(st.none(), SERVED_IDS)),
    min_size=1, max_size=6))
def test_every_outcome_event_carries_served_model(steps, scripted_adapter, make_gateway):
    # oracle: the scripted sequence: ok/refusal events carry the configured served id,
    # error events carry None
    gateway = make_gateway(scripted_adapter(steps))
    with trace_query("q") as trace:
        for _ in steps:
            try:
                gateway.complete(messages=MESSAGES, model=REQUESTED, stage="spec")
            except (LLMRefusalError, ValueError):
                pass
    events = _llm_calls(trace)
    assert [e["outcome"] for e in events] == [k for k, _ in steps]
    for event, (kind, served) in zip(events, steps, strict=True):
        assert "served_model" in event
        assert event["served_model"] == (None if kind == "error" else served)
