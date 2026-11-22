"""Unit tests for the provider-neutral LLMGateway and its adapters."""
import json
import logging

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters import AnthropicAdapter
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
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
# A fake adapter so gateway-level behaviour is testable without any SDK.
# ----------------------------------------------------------------------

class _FakeAdapter(ProviderAdapter):
    def __init__(self, provider, *, parsed=None, behaviors=None):
        self.provider = provider
        self._parsed = parsed or ParsedResponse(
            content="ok", finish_reason="stop", prompt_tokens=1000, completion_tokens=500,
        )
        self._behaviors = list(behaviors or [None])
        self.calls = 0

    def get_client(self):
        return object()

    def build_params(self, **kwargs):
        return kwargs

    def invoke(self, client, params):
        behavior = self._behaviors[min(self.calls, len(self._behaviors) - 1)]
        self.calls += 1
        if isinstance(behavior, Exception):
            raise behavior
        return "raw"

    def parse(self, raw, *, stage):
        return self._parsed


class RateLimitError(Exception):
    pass


def _gateway(provider, **kw):
    adapter = kw.pop("adapter", None) or _FakeAdapter(provider)
    gw = LLMGateway(stage="synthesizer", cost_tracker=CostTracker(),
                    sleep_fn=lambda _s: None, adapters={provider: adapter}, **kw)
    return gw, adapter


MSGS = [{"role": "user", "content": "hi"}]


def test_openai_ref_stamps_provider_series_tier(events):
    gw, _ = _gateway("openai")
    with trace_query("q"):
        result = gw.complete(messages=MSGS, model="gpt-5-mini", stage="synthesizer")

    assert result.provider == "openai"
    assert result.model_series == "gpt-5"
    assert result.model_tier == "medium"
    assert result.content == "ok"

    call = next(e for e in events if e["kind"] == "llm_call")
    assert call["provider"] == "openai"
    assert call["model_series"] == "gpt-5"
    assert call["model_tier"] == "medium"
    assert call["model"] == "gpt-5-mini"


def test_cost_tracked_from_ref_model():
    gw, _ = _gateway("openai")
    result = gw.complete(messages=MSGS, model="gpt-5-mini")
    # gpt-5-mini priced 0.00025 in / 0.002 out per 1K tokens
    expected = (1000 / 1000.0) * 0.00025 + (500 / 1000.0) * 0.002
    assert abs(result.cost_usd - expected) < 1e-9


def test_provider_selected_from_model_ref():
    # An anthropic-resolving model must route to the anthropic adapter.
    anthro = _FakeAdapter("anthropic", parsed=ParsedResponse(
        content="claude!", finish_reason="end_turn", prompt_tokens=10, completion_tokens=5))
    gw = LLMGateway(stage="synthesizer", cost_tracker=CostTracker(),
                    sleep_fn=lambda _s: None, adapters={"anthropic": anthro})
    result = gw.complete(messages=MSGS, model="claude-opus-4-8")
    assert result.provider == "anthropic"
    assert result.model_series == "claude-4"
    assert result.model_tier == "frontier"
    assert result.content == "claude!"
    assert anthro.calls == 1


def test_transient_retry_then_success():
    adapter = _FakeAdapter("openai", behaviors=[RateLimitError("429"), None])
    gw = LLMGateway(stage="synthesizer", cost_tracker=CostTracker(),
                    sleep_fn=lambda _s: None, adapters={"openai": adapter}, max_attempts=3)
    result = gw.complete(messages=MSGS, model="gpt-5-mini")
    assert result.attempts == 2
    assert result.retries == 1


def test_error_event_carries_provider(events):
    adapter = _FakeAdapter("openai", behaviors=[ValueError("permanent")])
    gw = LLMGateway(stage="synthesizer", cost_tracker=CostTracker(),
                    sleep_fn=lambda _s: None, adapters={"openai": adapter}, max_attempts=2)
    with trace_query("q"):
        with pytest.raises(ValueError):
            gw.complete(messages=MSGS, model="gpt-5-mini")
    err = next(e for e in events if e["kind"] == "llm_call" and e["outcome"] == "error")
    assert err["provider"] == "openai"
    assert err["error_class"] == "permanent"


def test_unregistered_provider_raises():
    gw = LLMGateway(stage="synthesizer")
    # No anthropic key present in CI, but the failure we want is adapter build,
    # which happens before any network call — force an unknown provider instead.
    from src.llm.gateway.model_ref import ModelRef
    ref = ModelRef(model="mystery", provider="somewho", series="mystery")
    with pytest.raises(ValueError):
        gw.complete(messages=MSGS, model_ref=ref)


# ----------------------------------------------------------------------
# AnthropicAdapter translation (no SDK / network).
# ----------------------------------------------------------------------

class _AnthroBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _AnthroUsage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _AnthroResponse:
    def __init__(self, text, stop_reason="end_turn", i=12, o=6):
        self.content = [_AnthroBlock(text)]
        self.stop_reason = stop_reason
        self.usage = _AnthroUsage(i, o)


def test_anthropic_build_params_lifts_system_and_maps_max_tokens():
    adapter = AnthropicAdapter(api_key="sk-test")
    msgs = [
        {"role": "system", "content": [{"type": "text", "text": "SYS"}]},
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
    ]
    params = adapter.build_params(
        messages=msgs, model="claude-opus-4-8",
        max_completion_tokens=2000, temperature=0.2,
        response_format={"type": "json_object"}, reasoning_effort="low",
    )
    # B2: system prefix is emitted as a cache-breakpoint block, not a bare string.
    assert params["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    # An unconfigured model keeps the caller's budget.
    assert params["max_tokens"] == 2000
    assert params["messages"] == [{"role": "user", "content": "hello"}]
    # llm-anthropic-and-caching: temperature is never sent (Claude 5.5 rejects sampling
    # parameters), and OpenAI-only knobs are dropped, not forwarded.
    assert "temperature" not in params
    assert "response_format" not in params
    assert "reasoning_effort" not in params


def test_anthropic_parse_extracts_text_and_usage():
    adapter = AnthropicAdapter(api_key="sk-test")
    parsed = adapter.parse(_AnthroResponse("the answer"), stage="synthesizer")
    assert parsed.content == "the answer"
    assert parsed.finish_reason == "end_turn"
    assert parsed.prompt_tokens == 12
    assert parsed.completion_tokens == 6


def test_anthropic_parse_empty_content_raises():
    adapter = AnthropicAdapter(api_key="sk-test")
    with pytest.raises(ValueError):
        adapter.parse(_AnthroResponse("   "), stage="synthesizer")


# ----------------------------------------------------------------------
# Neutral response-format mapping.
# ----------------------------------------------------------------------

def test_openai_maps_json_sentinel_to_json_object():
    from src.llm.gateway.adapters import OpenAIAdapter
    params = OpenAIAdapter(api_key="sk-test").build_params(
        messages=MSGS, model="gpt-5-mini", response_format="json")
    assert params["response_format"] == {"type": "json_object"}


def test_openai_passes_dict_response_format_through():
    from src.llm.gateway.adapters import OpenAIAdapter
    rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    params = OpenAIAdapter(api_key="sk-test").build_params(
        messages=MSGS, model="gpt-5-mini", response_format=rf)
    assert params["response_format"] == rf


def test_openai_no_response_format_when_none():
    from src.llm.gateway.adapters import OpenAIAdapter
    params = OpenAIAdapter(api_key="sk-test").build_params(messages=MSGS, model="gpt-5-mini")
    assert "response_format" not in params


def test_anthropic_drops_json_response_format():
    params = AnthropicAdapter(api_key="sk-test").build_params(
        messages=MSGS, model="claude-opus-4-8", max_completion_tokens=100, response_format="json")
    assert "response_format" not in params
