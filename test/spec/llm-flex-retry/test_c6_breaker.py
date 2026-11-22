"""C6: an exhausted Flex 429 neither advances nor resets the circuit breaker (requirement 6).

A real ``LLMGateway`` with an active ``RunBudget(max_consecutive_failures=3)`` and a real trace;
``ScriptAdapter`` plays the provider (conftest.py).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.run_budget import CircuitBreakerOpen, RunBudget
from src.observability.trace_context import trace_query

FLEX_SHORT = {"XBRLRAG_OPENAI_SERVICE_TIER": "flex", "XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS": "2",
              "XBRLRAG_FLEX_RETRY_BASE_S": "1", "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S": "1"}
FLEX_DEFAULT = {"XBRLRAG_OPENAI_SERVICE_TIER": "flex", "XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS": None,
                "XBRLRAG_FLEX_RETRY_BASE_S": None, "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S": None}


def _call(gateway, h):
    return h.outcome(lambda: gateway.complete(messages=h.messages, model=h.openai_model,
                                              stage="spec"))


@pytest.mark.criterion("C6")
def test_exhausted_flex_leaves_the_breaker(make_gateway, script_adapter, helpers, env_vars):
    # oracle: requirement 6 — two exhausted Flex calls from 2 failures leave 2; the next call
    # is made (no CircuitBreakerOpen at limit 3) and its success resets the count to 0
    h = helpers
    adapter = script_adapter([h.flex() for _ in range(4)], final="after the storm")
    gateway = make_gateway(adapter, run_budget=RunBudget(max_consecutive_failures=3))
    with env_vars(**FLEX_SHORT), trace_query("c6") as trace:
        trace.consecutive_failures = 2
        first = _call(gateway, h)
        after_first = trace.consecutive_failures
        second = _call(gateway, h)
        after_second = trace.consecutive_failures
        third = _call(gateway, h)
    assert first[0] == "raised" and not isinstance(first[1], CircuitBreakerOpen)
    assert second[0] == "raised" and not isinstance(second[1], CircuitBreakerOpen)
    assert after_first == 2
    assert after_second == 2
    assert third[0] == "ok", third[1]
    assert third[1].content == "after the storm"
    assert trace.consecutive_failures == 0
    assert adapter.invoked == 5
    errors = [e for e in h.events(trace, "llm_call") if e.get("outcome") == "error"]
    assert [e.get("error_class") for e in errors] == ["flex_unavailable"] * 2


@pytest.mark.criterion("C6")
def test_flex_retries_then_success(make_gateway, script_adapter, helpers, env_vars):
    # oracle: requirement 6 — five Flex 429s then a reply: one success, so the count resets to
    # 0 and trace.llm_calls goes up by exactly one (the retries are not charged calls)
    h = helpers
    adapter = script_adapter([h.flex() for _ in range(5)], final="ok")
    gateway = make_gateway(adapter, run_budget=RunBudget(max_consecutive_failures=3))
    with env_vars(**FLEX_DEFAULT), trace_query("c6-ok") as trace:
        trace.consecutive_failures = 2
        before = trace.llm_calls
        kind, result = _call(gateway, h)
    assert kind == "ok", result
    assert trace.consecutive_failures == 0
    assert trace.llm_calls == before + 1


@pytest.mark.criterion("C6")
def test_other_errors_still_trip_the_breaker(make_gateway, script_adapter, helpers, env_vars,
                                            sdk_errors):
    # oracle: requirement 6 — an exhausted 500 advances 2 → 3 as today and the next call
    # raises CircuitBreakerOpen before the adapter; an exhausted Flex 429 just before it left
    # the count alone
    h = helpers
    flex_storm = [h.flex(), h.flex()]
    errors = [sdk_errors.openai_status(500) for _ in range(3)]
    adapter = script_adapter(flex_storm + errors, final="unreached")
    gateway = make_gateway(adapter, run_budget=RunBudget(max_consecutive_failures=3))
    with env_vars(**FLEX_SHORT), trace_query("c6-500") as trace:
        trace.consecutive_failures = 2
        flex_kind, _ = _call(gateway, h)
        after_flex = trace.consecutive_failures
        kind, raised = _call(gateway, h)
        after_500 = trace.consecutive_failures
        invoked = adapter.invoked
        halted = _call(gateway, h)
    assert flex_kind == "raised"
    assert after_flex == 2
    assert kind == "raised" and raised is errors[2]
    assert after_500 == 3
    assert halted[0] == "raised" and isinstance(halted[1], CircuitBreakerOpen)
    assert adapter.invoked == invoked == 5


@pytest.mark.criterion("C6")
@given(start=st.integers(min_value=0, max_value=4), calls=st.integers(min_value=1, max_value=4))
def test_property_count_is_the_start(make_gateway, script_adapter, helpers, env_vars,
                                     start, calls):
    # oracle: requirement 6 — any number of exhausted Flex calls leaves the count at its start
    h = helpers
    adapter = script_adapter([h.flex() for _ in range(2 * calls)], final="x")
    gateway = make_gateway(adapter, run_budget=RunBudget(max_consecutive_failures=5))
    with env_vars(**FLEX_SHORT), trace_query("c6-prop") as trace:
        trace.consecutive_failures = start
        kinds = [_call(gateway, h)[0] for _ in range(calls)]
        assert trace.consecutive_failures == start
    assert kinds == ["raised"] * calls
    assert adapter.invoked == 2 * calls
