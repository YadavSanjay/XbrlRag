"""C4: the run's stop signal around Flex waits, and sliced waits (requirements 5, 6, 8).

A real ``LLMGateway`` inside a real ``RunStopScope`` over a real ``threading.Event``; a sleep
hook sets the event as the runner's main thread would. Expected slices are worked by hand
(requirement 6: 10 s each, the last the remainder).
"""

from __future__ import annotations

import threading

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.run_budget import RunStopped, RunStopScope
from src.observability.trace_context import trace_query


@pytest.mark.criterion("C4")
def test_stop_already_set_halts_before_the_first_wait(h, env_vars):
    # oracle: requirement 5.1 — the stop is checked before the wait: no sleep, one call,
    # a budget_halt event, no retry event, the breaker untouched
    stop = threading.Event()
    stop.set()
    adapter = h.ScriptAdapter([h.flex() for _ in range(3)], final="unreached")
    sleeper = h.Sleeper()
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env()), trace_query("c4-set") as trace, RunStopScope(stop):
        trace.consecutive_failures = 1
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, RunStopped), raised
    assert sleeper.sleeps == []
    assert adapter.invoked == 1
    assert [e.get("reason") for e in h.events(trace, "budget_halt")] == ["RunStopped"]
    assert h.events(trace, "retry") == []
    assert [e for e in h.events(trace, "llm_call") if e.get("outcome") == "error"] == []
    assert trace.consecutive_failures == 1


@pytest.mark.criterion("C4")
def test_stop_set_during_a_short_wait(h, env_vars):
    # oracle: requirement 6 — waits of 5 and 10 s are single slices; the stop set during the
    # 10 s wait is seen after it, before the resend: two calls, sleeps [5, 10]
    stop = threading.Event()
    adapter = h.ScriptAdapter([h.flex() for _ in range(4)], final="unreached")
    sleeper = h.Sleeper({2: stop.set})
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env()), trace_query("c4-short"), RunStopScope(stop):
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, RunStopped), raised
    assert sleeper.sleeps == pytest.approx([5.0, 10.0], abs=1e-9)  # exact values
    assert adapter.invoked == 2


@pytest.mark.criterion("C4")
def test_stop_set_during_a_slice_of_a_long_wait(h, env_vars):
    # oracle: requirement 6 — base 25: the first wait of 25 s is sliced 10, 10, 5; the stop
    # set during the second slice is seen before the third: sleeps [10, 10], one call
    stop = threading.Event()
    adapter = h.ScriptAdapter([h.flex() for _ in range(3)], final="unreached")
    sleeper = h.Sleeper({2: stop.set})
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env(15, 25, 300)), trace_query("c4-long"), RunStopScope(stop):
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, RunStopped), raised
    assert sleeper.sleeps == pytest.approx([10.0, 10.0], abs=1e-9)  # exact values
    assert adapter.invoked == 1


@pytest.mark.criterion("C4")
def test_slices_only_inside_a_scope(h, env_vars):
    # oracle: requirement 6 — never stopped: two 25 s waits sliced [10, 10, 5] each, the
    # retry events keep the whole wait; with no scope the same storm sleeps [25, 25]
    stop = threading.Event()
    adapter = h.ScriptAdapter([h.flex(), h.flex()], final="the answer")
    sleeper = h.Sleeper()
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env(3, 25, 25)), trace_query("c4-slices") as trace, \
            RunStopScope(stop):
        kind, result = h.call(gateway)
    assert kind == "ok", result
    assert result.content == "the answer"
    assert sleeper.sleeps == pytest.approx([10.0, 10.0, 5.0, 10.0, 10.0, 5.0], abs=1e-9)
    assert [e.get("wait_ms") for e in h.events(trace, "retry")] == pytest.approx(
        [25000.0, 25000.0], abs=0.05)  # rounded to 0.1 ms
    assert adapter.invoked == 3

    plain = h.ScriptAdapter([h.flex(), h.flex()], final="the answer")
    plain_sleeper = h.Sleeper()
    with env_vars(**h.policy_env(3, 25, 25)), trace_query("c4-plain"):
        kind2, result2 = h.call(h.make_gateway(plain, plain_sleeper))
    assert kind2 == "ok", result2
    assert plain_sleeper.sleeps == pytest.approx([25.0, 25.0], abs=1e-9)  # exact values


@pytest.mark.criterion("C4")
@given(base=st.sampled_from([3.0, 7.0, 12.5, 25.0, 40.0]), data=st.data())
def test_property_stop_seen_within_one_slice(h, env_vars, base, data):
    # oracle: requirement 6 — the stop set during slice i of the k-th wait: the sleeps are the
    # slices before it and i slices of that wait, each at most 10 s; k calls, no resend
    waits = [min(300.0, base * 2 ** (n - 1)) for n in range(1, 15)]
    k = data.draw(st.integers(min_value=1, max_value=4), label="wait")
    pieces = h.slices(waits[k - 1])
    i = data.draw(st.integers(min_value=1, max_value=len(pieces)), label="slice")
    before = [s for w in waits[:k - 1] for s in h.slices(w)]
    stop = threading.Event()
    adapter = h.ScriptAdapter([h.flex() for _ in range(14)], final="unreached")
    sleeper = h.Sleeper({len(before) + i: stop.set})
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env(15, base, 300)), trace_query("c4-prop"), RunStopScope(stop):
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, RunStopped), raised
    assert sleeper.sleeps == pytest.approx(before + pieces[:i], abs=1e-9)  # slices by hand
    assert all(s <= 10.0 + 1e-9 for s in sleeper.sleeps)
    assert adapter.invoked == k


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(base=st.sampled_from([3.0, 7.0, 12.5, 25.0, 40.0]), n=st.integers(0, 5))
def test_metamorphic_slices_sum_to_the_wait(h, env_vars, base, n):
    # oracle: requirement 6 — never stopped, the slices of each wait sum to the unsliced wait,
    # and the reply is the same as with no scope
    waits = [min(300.0, base * 2 ** (m - 1)) for m in range(1, n + 1)]
    stop = threading.Event()
    adapter = h.ScriptAdapter([h.flex() for _ in range(n)], final="same")
    sleeper = h.Sleeper()
    with env_vars(**h.policy_env(15, base, 300)), trace_query("c4-meta"), RunStopScope(stop):
        kind, result = h.call(h.make_gateway(adapter, sleeper))
    assert kind == "ok", result
    assert result.content == "same"
    assert all(s <= 10.0 + 1e-9 for s in sleeper.sleeps)
    assert sum(sleeper.sleeps) == pytest.approx(sum(waits), abs=1e-6)  # float sums of slices
    assert sleeper.sleeps == pytest.approx([s for w in waits for s in h.slices(w)], abs=1e-9)
    assert adapter.invoked == n + 1
