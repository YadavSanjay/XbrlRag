"""C5: the gateway retries a Flex 429 under the Flex policy (requirement 5).

A real ``LLMGateway`` (real ``CostTracker``, real trace) drives ``ScriptAdapter``, which raises
the SDKs' own errors per attempt (conftest.py). Expected sleeps are requirement 4's closed form
worked by hand in each test.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway import resolve_model_ref
from src.llm.gateway.tools import ToolSpec
from src.observability.trace_context import trace_query

FLEX_TIER = {"XBRLRAG_OPENAI_SERVICE_TIER": "flex"}
NO_POLICY = {"XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS": None, "XBRLRAG_FLEX_RETRY_BASE_S": None,
             "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S": None}


def _complete(gateway, h, model=None):
    return lambda: gateway.complete(messages=h.messages, model=model or h.openai_model,
                                    stage="spec")


@pytest.mark.criterion("C5")
def test_five_flex_429s_then_a_reply(make_gateway, script_adapter, helpers, env_vars):
    # oracle: requirement 5 — Flex 429s do not use the gateway's 3 attempts; default policy
    # waits 5, 10, 20, 40, 80 (5 * 2**(k-1)); the same params are sent each time
    h = helpers
    adapter = script_adapter([h.flex() for _ in range(5)], final="the answer")
    sleeps: list[float] = []
    gateway = make_gateway(adapter, max_attempts=3, sleeps=sleeps)
    with env_vars(**FLEX_TIER, **NO_POLICY), trace_query("c5") as trace:
        kind, result = h.outcome(_complete(gateway, h))
    assert kind == "ok", result
    assert result.content == "the answer"
    assert result.attempts == 6
    assert result.retries == 5
    assert sleeps == pytest.approx([5.0, 10.0, 20.0, 40.0, 80.0], abs=1e-9)  # exact values
    assert adapter.invoked == 6
    assert all(p == adapter.params[0] for p in adapter.params)
    retries = h.events(trace, "retry")
    assert [e.get("error_class") for e in retries] == ["flex_unavailable"] * 5
    assert [e.get("wait_ms") for e in retries] == pytest.approx(
        [5000.0, 10000.0, 20000.0, 40000.0, 80000.0], abs=0.05)  # rounded to 0.1 ms
    assert [e.get("attempt") for e in retries] == [1, 2, 3, 4, 5]
    assert all(e.get("max_attempts") == 15 for e in retries)
    (ok,) = h.events(trace, "llm_call")
    assert ok.get("outcome") == "ok"
    assert ok.get("attempts") == 6


@pytest.mark.criterion("C5")
def test_default_policy_limits(make_gateway, script_adapter, helpers, env_vars):
    # oracle: requirement 3/5 — 14 Flex 429s are retried (sleeps 5..160 then 8 x 300); the
    # 15th is raised itself after 15 attempts and 14 sleeps totalling 2715 s
    h = helpers
    expected = [5.0, 10.0, 20.0, 40.0, 80.0, 160.0] + [300.0] * 8
    sleeps: list[float] = []
    adapter = script_adapter([h.flex() for _ in range(14)], final="late")
    gateway = make_gateway(adapter, max_attempts=3, sleeps=sleeps)
    with env_vars(**FLEX_TIER, **NO_POLICY):
        kind, result = h.outcome(_complete(gateway, h))
    assert kind == "ok", result
    assert result.content == "late"
    assert sleeps == pytest.approx(expected, abs=1e-9)  # exact values

    storm = [h.flex() for _ in range(15)]
    sleeps2: list[float] = []
    adapter2 = script_adapter(list(storm), final="never")
    gateway2 = make_gateway(adapter2, max_attempts=3, sleeps=sleeps2)
    with env_vars(**FLEX_TIER, **NO_POLICY):
        kind2, raised = h.outcome(_complete(gateway2, h))
    assert kind2 == "raised"
    assert raised is storm[14]
    assert adapter2.invoked == 15
    assert sleeps2 == pytest.approx(expected, abs=1e-9)  # exact values
    assert sum(sleeps2) == pytest.approx(2715.0, abs=1e-9)  # hand sum 315 + 2400


@pytest.mark.criterion("C5")
def test_policy_from_the_environment(make_gateway, script_adapter, helpers, env_vars):
    # oracle: requirement 5 — policy (4, 1, 3): four Flex 429s exhaust it, sleeps 1, 2, 3
    # (min(3, 1 * 2**2) = 3); the final llm_call event is error / flex_unavailable
    h = helpers
    storm = [h.flex() for _ in range(4)]
    sleeps: list[float] = []
    adapter = script_adapter(list(storm), final="never")
    gateway = make_gateway(adapter, max_attempts=3, sleeps=sleeps)
    with env_vars(**FLEX_TIER, XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS="4",
                  XBRLRAG_FLEX_RETRY_BASE_S="1", XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S="3"), \
            trace_query("c5-env") as trace:
        kind, raised = h.outcome(_complete(gateway, h))
    assert kind == "raised"
    assert raised is storm[3]
    assert adapter.invoked == 4
    assert sleeps == pytest.approx([1.0, 2.0, 3.0], abs=1e-9)  # exact values
    (event,) = h.events(trace, "llm_call")
    assert event.get("outcome") == "error"
    assert event.get("error_class") == "flex_unavailable"
    assert event.get("attempts") == 4


@pytest.mark.criterion("C5")
def test_separate_counts_for_flex_and_other_errors(make_gateway, script_adapter, helpers,
                                                   env_vars, sdk_errors):
    # oracle: requirement 5 — [flex, flex, 500, flex, reply] at max_attempts=2: the 500 is the
    # first other-transient failure (wait 0.5), the Flex waits follow the Flex count 1, 2, 3
    h = helpers
    script = [h.flex(), h.flex(), sdk_errors.openai_status(500), h.flex()]
    sleeps: list[float] = []
    adapter = script_adapter(script, final="mixed")
    gateway = make_gateway(adapter, max_attempts=2, sleeps=sleeps)
    with env_vars(**FLEX_TIER, **NO_POLICY):
        kind, result = h.outcome(_complete(gateway, h))
    assert kind == "ok", result
    assert result.content == "mixed"
    assert result.attempts == 5
    assert sleeps == pytest.approx([5.0, 10.0, 0.5, 20.0], abs=1e-9)  # exact values


@pytest.mark.criterion("C5")
def test_flex_honours_retry_after_up_to_the_cap(make_gateway, script_adapter, helpers,
                                               env_vars):
    # oracle: requirement 5 with 4 — Retry-After 42 beats backoff 5; 900 is capped at 300
    h = helpers
    script = [h.flex(headers={"retry-after": "42"}), h.flex(headers={"retry-after": "900"})]
    sleeps: list[float] = []
    adapter = script_adapter(script, final="after")
    gateway = make_gateway(adapter, max_attempts=3, sleeps=sleeps)
    with env_vars(**FLEX_TIER, **NO_POLICY):
        kind, result = h.outcome(_complete(gateway, h))
    assert kind == "ok", result
    assert sleeps == pytest.approx([42.0, 300.0], abs=1e-9)  # exact values


@pytest.mark.criterion("C5")
def test_which_429s_get_the_flex_policy(make_gateway, script_adapter, helpers, env_vars,
                                       sdk_errors):
    # oracle: requirement 5 with 1 — with no tier a resource_unavailable body still gets the
    # Flex policy (5 retries pass a 3-attempt gateway); with the tier set, a Claude call's
    # Anthropic 429 and an OpenAI insufficient_quota 429 keep the 3-attempt policy
    h = helpers
    sleeps: list[float] = []
    adapter = script_adapter([h.flex() for _ in range(5)], final="no tier")
    with env_vars(XBRLRAG_OPENAI_SERVICE_TIER=None, **NO_POLICY):
        kind, result = h.outcome(_complete(make_gateway(adapter, sleeps=sleeps), h))
    assert kind == "ok", result
    assert sleeps == pytest.approx([5.0, 10.0, 20.0, 40.0, 80.0], abs=1e-9)  # exact values

    for model, make_error in ((h.claude_model, sdk_errors.anthropic_429),
                              (h.openai_model, lambda: sdk_errors.openai_429(
                                  code="insufficient_quota", type_="insufficient_quota",
                                  message="You exceeded your current quota"))):
        errors = [make_error() for _ in range(3)]
        other_sleeps: list[float] = []
        other = script_adapter(list(errors), final="never")
        with env_vars(**FLEX_TIER, **NO_POLICY):
            kind, raised = h.outcome(_complete(make_gateway(other, sleeps=other_sleeps), h,
                                               model=model))
        assert kind == "raised"
        assert raised is errors[2]
        assert other.invoked == 3
        assert other_sleeps == pytest.approx([0.5, 1.0], abs=1e-9)  # today's waits


@pytest.mark.criterion("C5")
def test_complete_tools_is_retried_the_same_way(make_gateway, script_adapter, helpers,
                                               env_vars):
    # oracle: requirement 5 — the loop is shared, so complete_tools gets the same retries
    h = helpers
    adapter = script_adapter([h.flex() for _ in range(4)], final="tools reply")
    sleeps: list[float] = []
    gateway = make_gateway(adapter, max_attempts=3, sleeps=sleeps)
    tool = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
    with env_vars(**FLEX_TIER, **NO_POLICY):
        kind, result = h.outcome(lambda: gateway.complete_tools(
            messages=h.messages, tools=[tool], model_ref=resolve_model_ref(h.openai_model),
            stage="spec"))
    assert kind == "ok", result
    assert result.content == "tools reply"
    assert result.attempts == 5
    assert sleeps == pytest.approx([5.0, 10.0, 20.0, 40.0], abs=1e-9)  # exact values


@pytest.mark.criterion("C5")
@given(attempts=st.integers(min_value=2, max_value=12),
       base=st.sampled_from([0.25, 1.0, 2.0, 5.0]),
       cap_factor=st.sampled_from([1, 2, 8, 64]),
       data=st.data())
def test_property_k_flex_429s_then_a_reply(make_gateway, script_adapter, helpers, env_vars,
                                          attempts, base, cap_factor, data):
    # oracle: requirement 5 with 4 — k < attempts Flex 429s then a reply give the reply,
    # attempts == k + 1 and sleeps min(cap, base * 2**(i-1)) for i = 1..k
    h = helpers
    cap = base * cap_factor
    k = data.draw(st.integers(min_value=0, max_value=attempts - 1))
    sleeps: list[float] = []
    adapter = script_adapter([h.flex() for _ in range(k)], final="p")
    gateway = make_gateway(adapter, max_attempts=1, sleeps=sleeps)
    with env_vars(**FLEX_TIER, XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS=str(attempts),
                  XBRLRAG_FLEX_RETRY_BASE_S=repr(base),
                  XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S=repr(cap)):
        kind, result = h.outcome(_complete(gateway, h))
    assert kind == "ok", result
    assert result.content == "p"
    assert result.attempts == k + 1
    assert sleeps == pytest.approx([min(cap, base * 2 ** (i - 1)) for i in range(1, k + 1)],
                                   abs=1e-9)  # exact binary values
