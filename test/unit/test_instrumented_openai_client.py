"""Unit tests for the instrumented OpenAI chat-completions gateway."""
import json
import logging

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.instrumented_openai_client import (
    InstrumentedChatClient,
    LLMCallResult,
    _is_transient,
)
from src.observability.trace_context import trace_query


@pytest.fixture
def events():
    """Collect ``xbrlrag.events`` records directly (the logger has propagate=False)."""
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


# ----------------------------------------------------------------------
# Fakes mimicking the openai SDK response/exception surface
# ----------------------------------------------------------------------

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeUsageDetails:
    def __init__(self, reasoning_tokens):
        self.reasoning_tokens = reasoning_tokens


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens, reasoning_tokens=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        if reasoning_tokens is not None:
            self.completion_tokens_details = _FakeUsageDetails(reasoning_tokens)


class _FakeResponse:
    def __init__(self, content, prompt_tokens=100, completion_tokens=20, finish_reason="stop",
                 reasoning_tokens=None):
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens, reasoning_tokens)


class _FakeCompletions:
    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self.calls = 0

    def create(self, **kwargs):
        behavior = self._behaviors[self.calls]
        self.calls += 1
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAI:
    def __init__(self, behaviors):
        self.chat = _FakeChat(_FakeCompletions(behaviors))


# Exception classes whose *names* the transient classifier recognizes.
class RateLimitError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class AuthenticationError(Exception):
    pass


def _client(behaviors, **kwargs):
    c = InstrumentedChatClient("sk-test", cost_tracker=CostTracker(), sleep_fn=lambda _s: None, **kwargs)
    c._client = _FakeOpenAI(behaviors)  # inject fake, bypass lazy real-client creation
    return c


MSGS = [{"role": "user", "content": "hi"}]


# ----------------------------------------------------------------------
# Transient classification
# ----------------------------------------------------------------------

def test_is_transient_by_name():
    assert _is_transient(RateLimitError("429"))
    assert _is_transient(APITimeoutError("timeout"))
    assert not _is_transient(AuthenticationError("bad key"))
    assert not _is_transient(ValueError("nope"))


def test_is_transient_by_status_code():
    exc = Exception("server error")
    exc.status_code = 503
    assert _is_transient(exc)
    exc2 = Exception("bad request")
    exc2.status_code = 400
    assert not _is_transient(exc2)


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------

def test_complete_returns_result_and_tracks_cost():
    client = _client([_FakeResponse("hello", prompt_tokens=1000, completion_tokens=500)])
    result = client.complete(messages=MSGS, model="gpt-5-mini", stage="synthesizer")

    assert isinstance(result, LLMCallResult)
    assert result.content == "hello"
    assert result.prompt_tokens == 1000
    assert result.completion_tokens == 500
    assert result.retries == 0
    assert result.outcome == "ok"
    # gpt-5-mini priced at 0.00025 in / 0.002 out per 1K tokens
    expected = (1000 / 1000.0) * 0.00025 + (500 / 1000.0) * 0.002
    assert abs(result.cost_usd - expected) < 1e-9
    assert client.cost_tracker.get_summary()["total_calls"] == 1


def test_complete_records_into_active_trace():
    client = _client([_FakeResponse("ok", prompt_tokens=200, completion_tokens=50)])
    with trace_query("q") as tc:
        client.complete(messages=MSGS, model="gpt-5-nano", stage="intent_router")
        assert tc.llm_calls == 1
        assert tc.total_prompt_tokens == 200
        assert tc.total_completion_tokens == 50
        assert tc.total_cost_usd > 0.0


def test_llm_call_event_emitted(events):
    client = _client([_FakeResponse("ok")])
    with trace_query("q"):
        client.complete(messages=MSGS, model="gpt-5-mini", stage="synthesizer")

    calls = [e for e in events if e["kind"] == "llm_call"]
    assert len(calls) == 1
    assert calls[0]["outcome"] == "ok"
    assert calls[0]["stage"] == "synthesizer"
    assert calls[0]["model"] == "gpt-5-mini"
    assert "latency_ms" in calls[0]


def test_prompt_version_recorded_on_event_and_result(events):
    client = _client([_FakeResponse("ok")])
    with trace_query("q"):
        result = client.complete(
            messages=MSGS, model="gpt-5-mini", stage="synthesizer",
            prompt_templates=["sys", "usr"], prompt_version="abc123def456",
        )
    assert result.prompt_version == "abc123def456"
    assert result.prompt_templates == ["sys", "usr"]
    call = next(e for e in events if e["kind"] == "llm_call")
    assert call["prompt_version"] == "abc123def456"
    assert call["prompt_templates"] == ["sys", "usr"]


def test_reasoning_tokens_and_effort_recorded_on_event(events):
    client = _client([_FakeResponse("ok", completion_tokens=200, reasoning_tokens=180)])
    with trace_query("q"):
        client.complete(
            messages=MSGS, model="gpt-5-mini", stage="synthesizer", reasoning_effort="low",
        )
    call = next(e for e in events if e["kind"] == "llm_call")
    assert call["reasoning_tokens"] == 180
    assert call["reasoning_effort"] == "low"


def test_reasoning_tokens_default_zero_without_details(events):
    client = _client([_FakeResponse("ok")])  # no completion_tokens_details
    with trace_query("q"):
        client.complete(messages=MSGS, model="gpt-5-mini", stage="synthesizer")
    call = next(e for e in events if e["kind"] == "llm_call")
    assert call["reasoning_tokens"] == 0
    assert call["reasoning_effort"] is None


# ----------------------------------------------------------------------
# Retry behaviour
# ----------------------------------------------------------------------

def test_transient_error_is_retried_then_succeeds():
    client = _client([RateLimitError("429"), _FakeResponse("recovered")], max_attempts=3)
    result = client.complete(messages=MSGS, model="gpt-5-mini")
    assert result.content == "recovered"
    assert result.attempts == 2
    assert result.retries == 1


def test_retry_event_emitted(events):
    client = _client([APITimeoutError("t"), _FakeResponse("ok")], max_attempts=3)
    with trace_query("q"):
        client.complete(messages=MSGS, model="gpt-5-mini")

    retries = [e for e in events if e["kind"] == "retry"]
    assert len(retries) == 1
    assert retries[0]["attempt"] == 1
    assert retries[0]["error_class"] == "transient"


def test_permanent_error_not_retried():
    client = _client([AuthenticationError("bad key"), _FakeResponse("never")], max_attempts=3)
    with pytest.raises(AuthenticationError):
        client.complete(messages=MSGS, model="gpt-5-mini")
    # only the first behavior should have been consumed
    assert client._client.chat.completions.calls == 1


def test_transient_error_exhausts_attempts_and_raises():
    client = _client([RateLimitError("1"), RateLimitError("2")], max_attempts=2)
    with pytest.raises(RateLimitError):
        client.complete(messages=MSGS, model="gpt-5-mini")
    assert client._client.chat.completions.calls == 2


def test_error_event_emitted_on_failure(events):
    client = _client([AuthenticationError("bad key")], max_attempts=2)
    with trace_query("q"):
        with pytest.raises(AuthenticationError):
            client.complete(messages=MSGS, model="gpt-5-mini")

    err_calls = [e for e in events if e["kind"] == "llm_call" and e["outcome"] == "error"]
    assert len(err_calls) == 1
    assert err_calls[0]["error_class"] == "permanent"


# ----------------------------------------------------------------------
# Response validation
# ----------------------------------------------------------------------

def test_empty_content_raises():
    client = _client([_FakeResponse("   ")])
    with pytest.raises(ValueError):
        client.complete(messages=MSGS, model="gpt-5-mini")


def test_content_filter_raises():
    client = _client([_FakeResponse("blocked", finish_reason="content_filter")])
    with pytest.raises(ValueError):
        client.complete(messages=MSGS, model="gpt-5-mini")


def test_missing_api_key_raises():
    with pytest.raises(ValueError):
        InstrumentedChatClient("")
