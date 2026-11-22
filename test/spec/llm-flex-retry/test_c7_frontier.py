"""C7: a Flex 429 mid-loop continues the frontier agent at the same step (requirement 7).

The real ``FrontierSecToolsSystem`` over a real gateway (``max_attempts=3``) and a real
``SecToolbox`` whose transport refuses every request; turn 1 calls an unknown tool, which the
toolbox answers without a request. ``AgentAdapter`` plays the provider (conftest.py).
"""

from __future__ import annotations

import pytest

from src.eval.core.checkpoint.run import error_origin

FLEX_DEFAULT = {"XBRLRAG_OPENAI_SERVICE_TIER": "flex", "XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS": None,
                "XBRLRAG_FLEX_RETRY_BASE_S": None, "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S": None}
TOOL_TURN = [("no_such_tool", {"q": "AAA"})]


def _answer(arm, case, h):
    return h.outcome(lambda: arm.answer(case))


@pytest.mark.criterion("C7")
def test_flex_429s_at_turn_two_resume_the_same_step(make_arm, make_case, helpers, env_vars,
                                                    reply):
    # oracle: requirement 7 — turn 2's five Flex 429s are retried inside the gateway call with
    # the same messages; the case is answered (value 120) in 2 steps and 7 adapter calls; the
    # turn-2 messages extend turn 1's with its assistant and tool messages
    h = helpers
    sleeps: list[float] = []
    script = [TOOL_TURN] + [h.flex() for _ in range(5)]
    arm, adapter, sec = make_arm(script, final=reply(120), sleeps=sleeps)
    with env_vars(**FLEX_DEFAULT):
        kind, answer = _answer(arm, make_case(), h)
    assert kind == "ok", answer
    assert answer.error is None, answer.error
    assert answer.agent["stop"] == "answered"
    assert answer.agent["steps"] == 2
    assert answer.agent["tool_calls"] == 1
    assert answer.numeric_values == pytest.approx([120.0], abs=1e-9)  # the scripted JSON
    assert adapter.invoked == 7
    turn2 = [p["messages"] for p in adapter.params[1:]]
    assert all(m == turn2[0] for m in turn2)
    first = adapter.params[0]["messages"]
    assert turn2[0][:len(first)] == first
    added_roles = [m.get("role") for m in turn2[0][len(first):]]
    assert added_roles[:2] == ["assistant", "tool"]
    assert sleeps == pytest.approx([5.0, 10.0, 20.0, 40.0, 80.0], abs=1e-9)  # exact values
    assert sec.urls == []


@pytest.mark.criterion("C7")
def test_exhausted_storm_is_a_transport_error(make_arm, make_case, helpers, env_vars, reply):
    # oracle: requirement 7 with eval-run-checkpoint's error_origin — every turn-2 attempt a
    # Flex 429: the default policy makes 15 attempts (1 + 15 adapter calls in all), then the
    # case ends with an error tagged "transport"
    h = helpers
    sleeps: list[float] = []
    script = [TOOL_TURN] + [h.flex() for _ in range(15)]
    arm, adapter, _sec = make_arm(script, final=reply(120), sleeps=sleeps)
    with env_vars(**FLEX_DEFAULT):
        kind, answer = _answer(arm, make_case(), h)
    assert kind == "ok", answer
    assert adapter.invoked == 16
    assert answer.error is not None
    assert error_origin(answer.error) == "transport"
    assert answer.agent["stop"] == "error"
    assert len(sleeps) == 14


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@pytest.mark.parametrize("k", [3, 4, 6, 9])
def test_retries_do_not_change_the_case(make_arm, make_case, helpers, env_vars, reply, k):
    # oracle: requirement 7 — k Flex 429s at turn 2 leave the answer, steps, tool calls and the
    # final request's messages exactly as with none
    h = helpers
    runs = []
    for n in (0, k):
        script = [TOOL_TURN] + [h.flex() for _ in range(n)]
        arm, adapter, _sec = make_arm(script, final=reply(120))
        with env_vars(**FLEX_DEFAULT):
            kind, answer = _answer(arm, make_case(), h)
        assert kind == "ok", answer
        runs.append((answer.error, answer.numeric_values, answer.agent["steps"],
                     answer.agent["tool_calls"], adapter.params[-1]["messages"],
                     adapter.invoked))
    base, retried = runs
    assert retried[:5] == base[:5]
    assert base[0] is None
    assert retried[5] == base[5] + k
