"""C3: the run budget and breaker are re-checked around every Flex wait (requirements 5, 6, 8).

A real ``LLMGateway`` with a real ``RunBudget`` and trace. A hook on an adapter attempt or on
a sleep changes the real trace the way another stage or worker writing the same trace would.
The default Flex policy applies (waits 5, 10, 20, ...: llm-flex-retry requirement 4).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.run_budget import CircuitBreakerOpen, RunBudget, RunBudgetExceeded
from src.observability.trace_context import trace_query

DEFAULT_WAITS = [min(300.0, 5.0 * 2 ** (k - 1)) for k in range(1, 15)]  # 5, 10, ... 300


@pytest.mark.criterion("C3")
def test_breaker_tripped_during_the_wait(h, env_vars):
    # oracle: requirement 6 — after the wait, before the resend, the breaker is checked: it
    # tripped during the first 5 s wait, so CircuitBreakerOpen after one adapter call
    adapter = h.ScriptAdapter([h.flex() for _ in range(3)], final="unreached")
    with env_vars(**h.policy_env()), trace_query("c3-breaker") as trace:
        sleeper = h.Sleeper({1: lambda: setattr(trace, "consecutive_failures", 3)})
        gateway = h.make_gateway(adapter, sleeper,
                                 run_budget=RunBudget(max_consecutive_failures=3))
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, CircuitBreakerOpen), raised
    assert sleeper.sleeps == pytest.approx([5.0], abs=1e-9)  # exact value
    assert adapter.invoked == 1
    assert trace.consecutive_failures == 3  # the gateway neither advanced nor reset it
    halts = h.events(trace, "budget_halt")
    assert [e.get("reason") for e in halts] == ["CircuitBreakerOpen"]
    assert len(h.events(trace, "retry")) == 1
    assert [e for e in h.events(trace, "llm_call") if e.get("outcome") == "error"] == []


@pytest.mark.criterion("C3")
def test_cost_cap_passed_during_the_attempt(h, env_vars):
    # oracle: requirement 5 — before the wait the budget is checked: the cost passed the cap
    # while the first attempt was in flight, so RunBudgetExceeded with no sleep, no retry event
    adapter = h.ScriptAdapter([], final="unreached")
    with env_vars(**h.policy_env()), trace_query("c3-cost") as trace:
        def over_budget():
            trace.total_cost_usd = 1.0
            return h.flex()

        adapter.script = [over_budget, h.flex(), h.flex()]
        sleeper = h.Sleeper()
        gateway = h.make_gateway(adapter, sleeper, run_budget=RunBudget(max_cost_usd=0.5))
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, RunBudgetExceeded), raised
    assert sleeper.sleeps == []
    assert adapter.invoked == 1
    assert h.events(trace, "retry") == []
    assert [e.get("reason") for e in h.events(trace, "budget_halt")] == ["RunBudgetExceeded"]
    assert trace.llm_calls == 0
    assert trace.total_cost_usd == pytest.approx(1.0, abs=1e-12)  # only the hook's value


@pytest.mark.criterion("C3")
def test_call_cap_reached_during_the_third_wait(h, env_vars):
    # oracle: requirement 6 — the call cap reached during the third wait (20 s) halts after
    # it: three adapter calls, sleeps 5, 10, 20
    adapter = h.ScriptAdapter([h.flex() for _ in range(5)], final="unreached")
    with env_vars(**h.policy_env()), trace_query("c3-calls") as trace:
        sleeper = h.Sleeper({3: lambda: setattr(trace, "llm_calls", 4)})
        gateway = h.make_gateway(adapter, sleeper, run_budget=RunBudget(max_llm_calls=4))
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, RunBudgetExceeded), raised
    assert sleeper.sleeps == pytest.approx([5.0, 10.0, 20.0], abs=1e-9)  # exact values
    assert adapter.invoked == 3
    assert trace.llm_calls == 4


@pytest.mark.criterion("C3")
@given(j=st.integers(min_value=1, max_value=10), during_wait=st.booleans())
def test_property_breaker_trip_at_any_flex_attempt(h, env_vars, j, during_wait):
    # oracle: requirements 5 and 6 — a trip during attempt j halts before the j-th wait (j - 1
    # sleeps); a trip during the j-th wait halts after it (j sleeps); j adapter calls either way
    adapter = h.ScriptAdapter([], final="unreached")
    with env_vars(**h.policy_env()), trace_query("c3-prop") as trace:
        def trip() -> None:
            trace.consecutive_failures = 2

        def trip_then_flex():
            trip()
            return h.flex()

        script: list = [h.flex() for _ in range(14)]
        hooks = {}
        if during_wait:
            hooks[j] = trip
        else:
            script[j - 1] = trip_then_flex
        adapter.script = script
        sleeper = h.Sleeper(hooks)
        gateway = h.make_gateway(adapter, sleeper,
                                 run_budget=RunBudget(max_consecutive_failures=2))
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, CircuitBreakerOpen), raised
    assert adapter.invoked == j
    expected = DEFAULT_WAITS[:j] if during_wait else DEFAULT_WAITS[:j - 1]
    assert sleeper.sleeps == pytest.approx(expected, abs=1e-9)  # closed-form waits
    assert trace.consecutive_failures == 2
