"""C4: refusals are final; transient errors are classified alike (requirements 4 and 5).

The gateway is real; its adapter is the fake ``RecordingAdapter`` (conftest.py). The
transient table runs against both real adapters' ``is_transient``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters import AnthropicAdapter, OpenAIAdapter
from src.llm.gateway.adapters.base import LLMRefusalError
from src.observability.trace_context import trace_query

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

# Requirement 5's table.
TRANSIENT_STATUSES = (408, 409, 425, 429, 529, 500, 503)
PERMANENT_STATUSES = (400, 401, 403, 404, 422)


def _adapters():
    return [AnthropicAdapter(api_key="sk-ant-spec-offline"),
            OpenAIAdapter(api_key="sk-spec-offline")]


def _status_error(status: int, name: str = "APIStatusError") -> Exception:
    """An SDK-style HTTP error: a class *name* carrying ``status_code``."""
    exc = type(name, (Exception,), {})(f"HTTP {status}")
    exc.status_code = status
    return exc


def _named_error(name: str) -> Exception:
    return type(name, (Exception,), {})("network")


def _llm_calls(trace) -> list[dict]:
    return [e for e in trace.events if e.get("kind") == "llm_call"]


def _retries(trace) -> list[dict]:
    return [e for e in trace.events if e.get("kind") == "retry"]


@pytest.mark.criterion("C4")
def test_refusal_is_called_once_recorded_and_reraised(make_gateway, recording_adapter):
    # oracle: requirement 4 — no retry even with max_attempts=3, one llm_call event with
    # outcome "refusal", the same error re-raised
    refusal = LLMRefusalError("declined (category: cyber)")
    adapter = recording_adapter(errors=[refusal, None, None])
    sleeps: list[float] = []
    gateway = make_gateway(adapter, max_attempts=3, sleeps=sleeps)
    with trace_query("c4-refusal") as trace:
        with pytest.raises(LLMRefusalError) as caught:
            gateway.complete(messages=MESSAGES, model="gpt-5.6-sol", stage="spec")
    assert caught.value is refusal
    assert adapter.invoked == 1
    assert adapter.parsed == 1
    assert sleeps == []
    assert _retries(trace) == []
    events = _llm_calls(trace)
    assert len(events) == 1
    assert events[0]["outcome"] == "refusal"


@pytest.mark.criterion("C4")
def test_transient_table_for_both_adapters():
    # oracle: requirement 5's table — 408, 409, 425, 429, 529 and >= 500 transient,
    # connection and timeout errors transient (SDK-named and Python's own), other 4xx not
    transient = [_status_error(s) for s in TRANSIENT_STATUSES] + [
        _named_error("APIConnectionError"),
        _named_error("APITimeoutError"),
        TimeoutError("timed out"),
        ConnectionResetError("reset by peer"),
        ConnectionRefusedError("refused"),
    ]
    permanent = [_status_error(s) for s in PERMANENT_STATUSES] + [
        _status_error(400, "BadRequestError"),
        _status_error(401, "AuthenticationError"),
        _status_error(422, "UnprocessableEntityError"),
        LLMRefusalError("declined"),
    ]
    for adapter in _adapters():
        for exc in transient:
            assert adapter.is_transient(exc) is True, (adapter.provider, repr(exc))
        for exc in permanent:
            assert adapter.is_transient(exc) is False, (adapter.provider, repr(exc))


_CONNECTION_KINDS = ("APIConnectionError", "APITimeoutError", "TimeoutError",
                     "ConnectionResetError", "ConnectionRefusedError", "ConnectionAbortedError")
_BUILTINS = {"TimeoutError": TimeoutError, "ConnectionResetError": ConnectionResetError,
             "ConnectionRefusedError": ConnectionRefusedError,
             "ConnectionAbortedError": ConnectionAbortedError}


@pytest.mark.criterion("C4")
@given(
    status=st.one_of(st.integers(min_value=400, max_value=599), st.none()),
    kind=st.sampled_from(_CONNECTION_KINDS),
)
def test_both_adapters_classify_any_error_alike(status, kind):
    # oracle: requirement 5, closed form — with a status: transient iff status is 408, 409,
    # 425, 429 or >= 500; without one, every connection/timeout error is transient
    if status is not None:
        exc = _status_error(status)
        expected = status in (408, 409, 425, 429) or status >= 500
    else:
        exc = _BUILTINS[kind]("x") if kind in _BUILTINS else _named_error(kind)
        expected = True
    verdicts = [adapter.is_transient(exc) for adapter in _adapters()]
    assert verdicts == [expected, expected]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@pytest.mark.parametrize("max_attempts", [1, 2, 3, 5])
def test_transient_is_retried_but_refusal_is_not(make_gateway, recording_adapter, max_attempts):
    # oracle: requirements 4 and 5 — swapping a transient error for a refusal in the same
    # adapter turns max_attempts calls (all failing) into exactly one call with outcome
    # "refusal"
    transient = recording_adapter(errors=[_status_error(503)] * max_attempts)
    with trace_query("c4-transient") as trace:
        with pytest.raises(Exception) as caught:
            make_gateway(transient, max_attempts=max_attempts).complete(
                messages=MESSAGES, model="gpt-5.6-sol", stage="spec")
    assert getattr(caught.value, "status_code", None) == 503
    assert transient.invoked == max_attempts
    assert len(_retries(trace)) == max_attempts - 1
    assert [e["outcome"] for e in _llm_calls(trace)] == ["error"]

    refusing = recording_adapter(errors=[LLMRefusalError("declined")] * max_attempts)
    with trace_query("c4-refusal-mm") as trace:
        with pytest.raises(LLMRefusalError):
            make_gateway(refusing, max_attempts=max_attempts).complete(
                messages=MESSAGES, model="gpt-5.6-sol", stage="spec")
    assert refusing.invoked == 1
    assert _retries(trace) == []
    assert [e["outcome"] for e in _llm_calls(trace)] == ["refusal"]
