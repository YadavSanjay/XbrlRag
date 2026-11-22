"""C8: other transient errors honour Retry-After and are otherwise unchanged (requirement 8).

No service tier is set. Today's waits are ``min(8.0, 0.5 * 2 ** (n - 1))`` (gateway.py).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.observability.trace_context import trace_query

NO_TIER = {"XBRLRAG_OPENAI_SERVICE_TIER": None}


def _rate(sdk_errors, headers=None):
    return sdk_errors.openai_429(code="rate_limit_exceeded", type_="requests",
                                 message="Rate limit reached for requests", headers=headers)


def _run(make_gateway, script_adapter, h, env_vars, script, *, max_attempts=3):
    sleeps: list[float] = []
    adapter = script_adapter(list(script), final="reply")
    gateway = make_gateway(adapter, max_attempts=max_attempts, sleeps=sleeps)
    with env_vars(**NO_TIER), trace_query("c8") as trace:
        trace.consecutive_failures = 1
        kind, value = h.outcome(lambda: gateway.complete(messages=h.messages,
                                                         model=h.openai_model, stage="spec"))
    return kind, value, sleeps, adapter, trace


@pytest.mark.criterion("C8")
def test_rate_limit_retry_after(make_gateway, script_adapter, helpers, env_vars, sdk_errors):
    # oracle: requirement 8 — Retry-After 3 beats backoff 0.5; 30 is capped at max_backoff 8;
    # without a header three 429s are raised after 3 attempts with today's 0.5, 1.0, and the
    # breaker goes 1 → 2
    h = helpers
    kind, value, sleeps, _a, _t = _run(make_gateway, script_adapter, h, env_vars,
                                       [_rate(sdk_errors, {"retry-after": "3"})])
    assert kind == "ok", value
    assert sleeps == pytest.approx([3.0], abs=1e-9)  # exact values

    kind, value, sleeps, _a, _t = _run(make_gateway, script_adapter, h, env_vars,
                                       [_rate(sdk_errors, {"retry-after": "30"})])
    assert kind == "ok", value
    assert sleeps == pytest.approx([8.0], abs=1e-9)  # exact values

    storm = [_rate(sdk_errors) for _ in range(3)]
    kind, raised, sleeps, adapter, trace = _run(make_gateway, script_adapter, h, env_vars,
                                                storm)
    assert kind == "raised" and raised is storm[2]
    assert adapter.invoked == 3
    assert sleeps == pytest.approx([0.5, 1.0], abs=1e-9)  # today's waits
    assert trace.consecutive_failures == 2
    (event,) = h.events(trace, "llm_call")
    assert event.get("error_class") == "transient"


@pytest.mark.criterion("C8")
def test_server_errors_and_timeouts_unchanged(make_gateway, script_adapter, helpers, env_vars,
                                             sdk_errors):
    # oracle: requirement 8 — a 500 and a timeout without headers wait 0.5 then 1.0 (today);
    # a 500 with retry-after-ms 2500 waits 2.5; a 400 is not retried
    h = helpers
    kind, value, sleeps, adapter, _t = _run(
        make_gateway, script_adapter, h, env_vars,
        [sdk_errors.openai_status(500), sdk_errors.openai_timeout()])
    assert kind == "ok", value
    assert adapter.invoked == 3
    assert sleeps == pytest.approx([0.5, 1.0], abs=1e-9)  # today's waits

    kind, value, sleeps, _a, _t = _run(
        make_gateway, script_adapter, h, env_vars,
        [sdk_errors.openai_status(500, headers={"retry-after-ms": "2500"})])
    assert kind == "ok", value
    assert sleeps == pytest.approx([2.5], abs=1e-9)  # exact values

    bad = sdk_errors.openai_status(400)
    kind, raised, sleeps, adapter, _t = _run(make_gateway, script_adapter, h, env_vars, [bad])
    assert kind == "raised" and raised is bad
    assert adapter.invoked == 1
    assert sleeps == []


@pytest.mark.criterion("C8")
@given(headers=st.lists(st.one_of(st.none(), st.integers(min_value=1, max_value=20)),
                        min_size=1, max_size=2),
       kinds=st.lists(st.sampled_from(["rate", "500", "timeout"]), min_size=2, max_size=2))
def test_property_waits(make_gateway, script_adapter, helpers, env_vars, sdk_errors,
                        headers, kinds):
    # oracle: requirement 8 with 4, by hand — the n-th other-transient error waits
    # min(8, max(0.5 * 2**(n-1), retry_after)), or the plain backoff without a header
    h = helpers
    script = []
    expected = []
    for n, ra in enumerate(headers, start=1):
        kind = kinds[n - 1]
        hdr = None if ra is None else {"retry-after": str(ra)}
        if kind == "rate" or (kind == "timeout" and hdr is not None):
            script.append(_rate(sdk_errors, hdr))
        elif kind == "500":
            script.append(sdk_errors.openai_status(500, headers=hdr))
        else:
            script.append(sdk_errors.openai_timeout())
        backoff = min(8.0, 0.5 * 2 ** (n - 1))
        expected.append(backoff if ra is None else min(8.0, max(backoff, float(ra))))
    kind, value, sleeps, _a, _t = _run(make_gateway, script_adapter, h, env_vars, script)
    assert kind == "ok", value
    assert sleeps == pytest.approx(expected, abs=1e-9)  # exact values
