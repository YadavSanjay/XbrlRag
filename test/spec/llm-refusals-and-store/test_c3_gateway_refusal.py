"""C3: the gateway records a refusal's tokens and cost (requirement 3).

The gateway, its ``CostTracker`` and the trace are real; the adapter is the fake
``RefusingAdapter`` (conftest.py) raising a refusal that carries usage. Expected costs are
the closed form over claude-opus-5-5's committed rates in configs/llm/pricing.yaml (USD per
1,000 tokens), read in the test: uncached prompt at ``input``, cache reads at
``cached_input``, cache writes at ``cache_write``, completion at ``output``
(``llm-provider-profiles``' cost rule).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.base import LLMRefusalError
from src.observability.trace_context import trace_query

MODEL = "claude-opus-5-5"
MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens",
                "cached_prompt_tokens", "cache_write_tokens")


def _closed_form(rates: dict, *, prompt: int, cached: int, written: int,
                 completion: int) -> float:
    assert "tiers" not in rates  # the closed form below has no long-context tier
    uncached = prompt - cached - written
    return (uncached * float(rates["input"]) + cached * float(rates["cached_input"])
            + written * float(rates["cache_write"])
            + completion * float(rates["output"])) / 1000


def _llm_calls(trace) -> list[dict]:
    return [e for e in trace.events if e.get("kind") == "llm_call"]


def _run(make_gateway, refusing_adapter, refusal, *, max_attempts: int = 3,
         consecutive_failures: int = 0):
    """Runs one refused call inside a trace; returns (adapter, gateway, trace, caught, before)."""
    adapter = refusing_adapter(refusal)
    sleeps: list[float] = []
    gateway = make_gateway(adapter, max_attempts=max_attempts, sleeps=sleeps)
    with trace_query("c3-refusal") as trace:
        trace.consecutive_failures = consecutive_failures
        before = (trace.llm_calls, trace.total_cost_usd, trace.total_prompt_tokens,
                  trace.total_completion_tokens)
        with pytest.raises(LLMRefusalError) as caught:
            gateway.complete(messages=MESSAGES, model=MODEL, stage="spec")
    assert sleeps == []
    return adapter, gateway, trace, caught.value, before


@pytest.mark.criterion("C3")
def test_billed_refusal_is_recorded_with_its_cost(make_gateway, refusing_adapter,
                                                  make_refusal, pricing_row):
    # oracle: requirement 3 and the closed form at the committed opus rates — 10,000
    # uncached at input + 90,000 cached at cached_input + 0 written + 0 completion
    refusal = make_refusal(category="bio", billed=True, prompt_tokens=100_000,
                           cached_prompt_tokens=90_000, cache_write_tokens=0,
                           completion_tokens=0)
    expected = _closed_form(pricing_row(MODEL), prompt=100_000, cached=90_000, written=0,
                            completion=0)
    adapter, gateway, trace, raised, before = _run(make_gateway, refusing_adapter, refusal)

    assert adapter.invoked == 1
    assert adapter.parsed == 1
    assert raised is refusal
    events = _llm_calls(trace)
    assert len(events) == 1
    event = events[0]
    assert event.get("outcome") == "refusal"
    assert {k: event.get(k) for k in TOKEN_FIELDS} == {
        "prompt_tokens": 100_000, "completion_tokens": 0, "total_tokens": 100_000,
        "cached_prompt_tokens": 90_000, "cache_write_tokens": 0,
    }
    assert event.get("refusal_category") == "bio"
    assert event.get("billed") is True
    # the event rounds cost_usd to 6 decimals, as a successful call's event does
    assert event.get("cost_usd") == pytest.approx(expected, abs=1e-6)
    # the trace's running total is not rounded: only float summation error
    assert trace.llm_calls == before[0] + 1
    assert trace.total_cost_usd - before[1] == pytest.approx(expected, rel=1e-12)
    assert trace.total_prompt_tokens == before[2] + 100_000
    summary = gateway.cost_tracker.get_summary()
    assert summary["total_calls"] == 1
    # the tracker's record is the unrounded cost: only float summation error
    assert summary["total_cost_usd"] == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C3")
def test_unbilled_refusal_costs_nothing_but_keeps_its_tokens(make_gateway, refusing_adapter,
                                                            make_refusal):
    # oracle: requirement 3 — an unbilled refusal has cost_usd 0 and no cost-tracker
    # record, while its tokens are still on the event and in the trace
    refusal = make_refusal(category="cyber", billed=False, prompt_tokens=100_000,
                           cached_prompt_tokens=90_000, cache_write_tokens=0,
                           completion_tokens=0)
    adapter, gateway, trace, raised, before = _run(make_gateway, refusing_adapter, refusal)

    assert adapter.invoked == 1
    assert raised is refusal
    events = _llm_calls(trace)
    assert len(events) == 1
    event = events[0]
    assert event.get("outcome") == "refusal"
    assert event.get("prompt_tokens") == 100_000
    assert event.get("cached_prompt_tokens") == 90_000
    assert event.get("total_tokens") == 100_000
    assert event.get("refusal_category") == "cyber"
    assert event.get("billed") is False
    assert event.get("cost_usd") == 0
    assert gateway.cost_tracker.get_summary()["total_calls"] == 0
    assert trace.llm_calls == before[0] + 1
    assert trace.total_prompt_tokens == before[2] + 100_000
    assert trace.total_cost_usd == before[1]


@pytest.mark.criterion("C3")
def test_refusal_without_usage_costs_nothing(make_gateway, refusing_adapter, make_refusal):
    # oracle: requirement 3 — usage None gives cost_usd 0, no tracker record, and the
    # gateway still raises only the refusal itself
    refusal = make_refusal(category=None, billed=False, prompt_tokens=None)
    adapter, gateway, trace, raised, _before = _run(make_gateway, refusing_adapter, refusal)

    assert raised is refusal
    events = _llm_calls(trace)
    assert len(events) == 1
    event = events[0]
    assert event.get("outcome") == "refusal"
    assert event.get("cost_usd") == 0
    assert "refusal_category" in event
    assert event.get("refusal_category") is None
    assert event.get("billed") is False
    assert gateway.cost_tracker.get_summary()["total_calls"] == 0


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("start", [0, 2])
def test_refusal_leaves_the_circuit_breaker_alone(make_gateway, refusing_adapter,
                                                  make_refusal, pricing_row, start):
    # oracle: requirement 3 — a refusal neither trips nor resets the breaker, while it is
    # still counted on the trace with its closed-form cost (2,000 uncached + 10 completion)
    refusal = make_refusal(category="frontier_llm", billed=True, prompt_tokens=2_000,
                           completion_tokens=10)
    expected = _closed_form(pricing_row(MODEL), prompt=2_000, cached=0, written=0,
                            completion=10)
    _adapter, _gateway, trace, _raised, before = _run(
        make_gateway, refusing_adapter, refusal, consecutive_failures=start)
    assert trace.consecutive_failures == start
    assert trace.llm_calls == before[0] + 1
    # the trace's running total is not rounded: only float summation error
    assert trace.total_cost_usd - before[1] == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("prompt,cached,written,completion", [
    (100_000, 90_000, 0, 0),
    (12_345, 1_000, 2_000, 0),
    (5_000, 0, 5_000, 321),
    (1, 0, 0, 1),
])
def test_doubling_tokens_doubles_cost(make_gateway, refusing_adapter, make_refusal,
                                      prompt, cached, written, completion):
    # oracle: requirement 3 and llm-provider-profiles' cost rule — cost is linear in the
    # token counts (no tier on claude-opus-5-5), so doubling every count doubles cost_usd
    costs = []
    for k in (1, 2):
        refusal = make_refusal(category="bio", billed=True, prompt_tokens=k * prompt,
                               cached_prompt_tokens=k * cached,
                               cache_write_tokens=k * written,
                               completion_tokens=k * completion)
        _a, _g, trace, _r, _b = _run(make_gateway, refusing_adapter, refusal)
        (event,) = _llm_calls(trace)
        costs.append(event.get("cost_usd"))
    assert isinstance(costs[0], (int, float))
    assert costs[0] > 0
    # each event cost is rounded to 6 decimals, so the doubled one may differ by 2e-6
    assert costs[1] == pytest.approx(2 * costs[0], abs=2e-6)


_COUNT = st.integers(min_value=0, max_value=500_000)


@pytest.mark.criterion("C3")
@given(
    uncached=_COUNT, cached=_COUNT, written=_COUNT,
    completion=st.integers(min_value=0, max_value=50_000), billed=st.booleans(),
)
def test_refusal_cost_closed_form(make_gateway, refusing_adapter, make_refusal, pricing_row,
                                  uncached, cached, written, completion, billed):
    # oracle: requirement 3, closed form at the committed opus rates — billed: the same
    # formula as a successful call and one tracker record; not billed: 0 and none. Tokens
    # reach the event and the trace either way.
    prompt = uncached + cached + written
    refusal = make_refusal(category="bio" if billed else "cyber", billed=billed,
                           prompt_tokens=prompt, cached_prompt_tokens=cached,
                           cache_write_tokens=written, completion_tokens=completion)
    expected = _closed_form(pricing_row(MODEL), prompt=prompt, cached=cached,
                            written=written, completion=completion) if billed else 0.0
    _a, gateway, trace, _r, before = _run(make_gateway, refusing_adapter, refusal)
    (event,) = _llm_calls(trace)
    assert event.get("prompt_tokens") == prompt
    assert event.get("completion_tokens") == completion
    assert event.get("total_tokens") == prompt + completion
    assert event.get("billed") is billed
    # the event rounds cost_usd to 6 decimals
    assert event.get("cost_usd") == pytest.approx(expected, abs=1e-6)
    assert trace.llm_calls == before[0] + 1
    assert trace.total_prompt_tokens == before[2] + prompt
    assert trace.total_completion_tokens == before[3] + completion
    # the trace total is unrounded: only float summation error
    assert trace.total_cost_usd - before[1] == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert gateway.cost_tracker.get_summary()["total_calls"] == (1 if billed else 0)
