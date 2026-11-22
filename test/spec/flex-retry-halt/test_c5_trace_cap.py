"""C5: a trace's cumulative Flex waiting is capped, and the cap halt is sticky (requirements
5.3, 6, 7, 8, 9).

A real ``LLMGateway`` and real traces; the waits are llm-flex-retry requirement 4's closed
form, the totals hand sums.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.run_budget import FlexWaitExceeded, RunHalt
from src.observability.trace_context import trace_query

DEFAULT_WAITS = [5.0, 10.0, 20.0, 40.0, 80.0, 160.0] + [300.0] * 8  # sums to 2715


@pytest.mark.criterion("C5")
def test_second_storm_in_a_trace_halts(h, env_vars):
    # oracle: requirements 5.3 and 7 — default policy and cap 3600: call 1 exhausts (14 waits,
    # 315 + 8 x 300 = 2715 s, the 429 itself raised); call 2 waits 5..160 and 300 (2715 + 615
    # = 3330) and its 8th 429 would take 3330 + 300 = 3630 > 3600, so FlexWaitExceeded; call 3
    # raises it before any attempt
    storm = [h.flex() for _ in range(15)]
    adapter = h.ScriptAdapter(storm + [h.flex() for _ in range(10)], final="late")
    sleeper = h.Sleeper()
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env()), trace_query("c5-cap") as trace:
        first = h.call(gateway)
        after_first = (adapter.invoked, list(sleeper.sleeps))
        second = h.call(gateway)
        after_second = (adapter.invoked, list(sleeper.sleeps))
        third = h.call(gateway)
    assert first[0] == "raised" and first[1] is storm[14]
    assert after_first[0] == 15
    assert after_first[1] == pytest.approx(DEFAULT_WAITS, abs=1e-9)  # exact values
    assert sum(after_first[1]) == pytest.approx(2715.0, abs=1e-9)  # hand sum
    assert second[0] == "raised" and isinstance(second[1], FlexWaitExceeded), second[1]
    assert after_second[0] == 15 + 8
    assert after_second[1][14:] == pytest.approx(DEFAULT_WAITS[:7], abs=1e-9)  # 5 .. 300
    assert sum(after_second[1]) == pytest.approx(3330.0, abs=1e-9)  # 2715 + 615
    assert third[0] == "raised" and isinstance(third[1], FlexWaitExceeded), third[1]
    assert adapter.invoked == 23
    assert len(sleeper.sleeps) == 21
    reasons = [e.get("reason") for e in h.events(trace, "budget_halt")]
    assert reasons == ["FlexWaitExceeded", "FlexWaitExceeded"]
    assert len(h.events(trace, "retry")) == 14 + 7
    errors = [e for e in h.events(trace, "llm_call") if e.get("outcome") == "error"]
    assert [e.get("error_class") for e in errors] == ["flex_unavailable"]  # call 1 only
    assert trace.consecutive_failures == 0
    assert trace.llm_calls == 0


@pytest.mark.criterion("C5")
def test_exactly_at_the_cap_is_allowed(h, env_vars):
    # oracle: requirement 5.3 — "above" the cap halts: cap 30, policy (5, 10, 10): three waits
    # of 10 reach exactly 30 and are taken; the next Flex 429 halts with no sleep
    adapter = h.ScriptAdapter([h.flex() for _ in range(3)], final="in time")
    sleeper = h.Sleeper()
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env(5, 10, 10, cap=30)), trace_query("c5-edge"):
        first = h.call(gateway)
        adapter.script = [h.flex(), h.flex()]
        second = h.call(gateway)
    assert first[0] == "ok", first[1]
    assert first[1].content == "in time"
    assert second[0] == "raised" and isinstance(second[1], FlexWaitExceeded), second[1]
    assert sleeper.sleeps == pytest.approx([10.0, 10.0, 10.0], abs=1e-9)  # exact values
    assert adapter.invoked == 5


@pytest.mark.criterion("C5")
def test_cap_is_per_trace(h, env_vars):
    # oracle: requirement 5.3 — the total is the trace's: the same gateway in a new trace
    # starts from 0, and a cap halt in one trace does not reach the next
    adapter = h.ScriptAdapter([], final="fine")
    sleeper = h.Sleeper()
    gateway = h.make_gateway(adapter, sleeper)
    outcomes = []
    with env_vars(**h.policy_env(5, 10, 10, cap=30)):
        for name in ("t1", "t2"):
            with trace_query(name):
                adapter.script = [h.flex() for _ in range(3)]
                outcomes.append(h.call(gateway))
                adapter.script = [h.flex()]
                outcomes.append(h.call(gateway))
    kinds = [k for k, _ in outcomes]
    assert kinds == ["ok", "raised", "ok", "raised"]
    assert isinstance(outcomes[1][1], FlexWaitExceeded)
    assert isinstance(outcomes[3][1], FlexWaitExceeded)
    assert sleeper.sleeps == pytest.approx([10.0] * 6, abs=1e-9)  # exact values
    assert adapter.invoked == 10  # per trace: 4 (three 429s and the reply) + 1 (halted)


@pytest.mark.criterion("C5")
def test_no_trace_no_cap(h, env_vars):
    # oracle: requirement 9 — without a trace there is no total and no cap: three exhausted
    # storms are each waited in full (3 x 2715 s), each raising its own last 429; the same
    # gateway then opens a trace, where a second storm halts at its 8th 429 (requirement 5.3)
    adapter = h.ScriptAdapter([], final="never")
    sleeper = h.Sleeper()
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env(cap=3600)):
        kinds = []
        for _ in range(3):
            storm = [h.flex() for _ in range(15)]
            adapter.script = list(storm)
            kind, raised = h.call(gateway)
            kinds.append(kind)
            assert raised is storm[14]
        untraced = (list(sleeper.sleeps), adapter.invoked)
        with trace_query("c5-traced"):
            adapter.script = [h.flex() for _ in range(15)]
            kinds.append(h.call(gateway)[0])
            adapter.script = [h.flex() for _ in range(15)]
            traced_kind, halted = h.call(gateway)
    assert kinds == ["raised"] * 4
    assert untraced[0] == pytest.approx(DEFAULT_WAITS * 3, abs=1e-9)  # exact values
    assert untraced[1] == 45
    assert traced_kind == "raised" and isinstance(halted, FlexWaitExceeded), halted
    assert adapter.invoked == 45 + 15 + 8


@pytest.mark.criterion("C5")
def test_retry_after_counts_as_taken(h, env_vars):
    # oracle: requirement 6 — the total adds the wait actually taken: Retry-After 42 makes
    # each wait 42 (max(5, 42) under the 300 cap); cap 100: 42 + 42 = 84, then 84 + 42 = 126
    # > 100 halts before the third wait
    adapter = h.ScriptAdapter([h.flex(headers={"retry-after": "42"}) for _ in range(4)],
                              final="unreached")
    sleeper = h.Sleeper()
    gateway = h.make_gateway(adapter, sleeper)
    with env_vars(**h.policy_env(cap=100)), trace_query("c5-ra"):
        kind, raised = h.call(gateway)
    assert kind == "raised" and isinstance(raised, FlexWaitExceeded), raised
    assert sleeper.sleeps == pytest.approx([42.0, 42.0], abs=1e-9)  # exact values
    assert adapter.invoked == 3


def _simulate(calls, attempts, base, maximum, cap):
    """Requirements 5-7 by hand: per call 'ok', 'exhausted' or 'cap'; the sleeps; the calls."""
    total, capped, invoked = 0.0, False, 0
    outcomes, sleeps = [], []
    for n in calls:
        if capped:
            outcomes.append("cap")
            continue
        count = 0
        while True:
            invoked += 1
            if count >= n:
                outcomes.append("ok")
                break
            count += 1
            if count >= attempts:
                outcomes.append("exhausted")
                break
            wait = min(maximum, base * 2 ** (count - 1))
            if total + wait > cap:
                outcomes.append("cap")
                capped = True
                break
            total += wait
            sleeps.append(wait)
    return outcomes, sleeps, invoked


@pytest.mark.criterion("C5")
@given(attempts=st.integers(min_value=2, max_value=6),
       base=st.sampled_from([1.0, 2.0, 5.0]),
       factor=st.sampled_from([1, 2, 4, 16]),
       cap_factor=st.sampled_from([1, 3, 7, 10, 40]),
       calls=st.lists(st.integers(min_value=0, max_value=7), min_size=1, max_size=5))
def test_property_matches_the_rules(h, env_vars, attempts, base, factor, cap_factor, calls):
    # oracle: requirements 5-7 simulated in _simulate (written from the requirement text)
    maximum, cap = base * factor, base * cap_factor
    expected, expected_sleeps, expected_invoked = _simulate(calls, attempts, base, maximum, cap)
    adapter = h.ScriptAdapter([], final="ok")
    sleeper = h.Sleeper()
    gateway = h.make_gateway(adapter, sleeper)
    got = []
    with env_vars(**h.policy_env(attempts, base, maximum, cap=cap)), trace_query("c5-prop"):
        for n in calls:
            adapter.script = [h.flex() for _ in range(n)]
            kind, value = h.call(gateway)
            if kind == "ok":
                got.append("ok")
            elif isinstance(value, FlexWaitExceeded):
                got.append("cap")
            elif type(value).__name__ == "RateLimitError" and not isinstance(value, RunHalt):
                got.append("exhausted")
            else:
                got.append(repr(value))
    assert got == expected
    assert sleeper.sleeps == pytest.approx(expected_sleeps, abs=1e-9)  # closed-form waits
    assert sum(sleeper.sleeps) <= cap + 1e-9
    assert adapter.invoked == expected_invoked
