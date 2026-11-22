"""Unit tests for WS-4 / B2 — cached-prompt-token capture, pricing, and threading."""
import json
import logging
import types

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.anthropic_adapter import AnthropicAdapter
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.gateway.adapters.openai_adapter import OpenAIAdapter
from src.observability.trace_context import trace_query

# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------

class TestCostTrackerCached:
    def test_cached_tokens_billed_cheaper(self):
        ct = CostTracker()
        # gpt-5-mini: input 0.00025, cached_input 0.000025 per 1K.
        r = ct.record_usage("gpt-5-mini", prompt_tokens=10000, completion_tokens=500,
                            cached_prompt_tokens=8000)
        expected = (2000 / 1000) * 0.00025 + (8000 / 1000) * 0.000025 + (500 / 1000) * 0.002
        assert r.estimated_cost_usd == pytest.approx(expected)
        assert r.cached_prompt_tokens == 8000

    def test_cheaper_than_uncached(self):
        ct = CostTracker()
        cached = ct.record_usage("gpt-5-mini", 10000, 500, cached_prompt_tokens=10000)
        full = ct.record_usage("gpt-5-mini", 10000, 500)
        assert cached.estimated_cost_usd < full.estimated_cost_usd

    def test_summary_rolls_up_cached(self):
        ct = CostTracker()
        ct.record_usage("gpt-5-mini", 1000, 100, cached_prompt_tokens=600)
        ct.record_usage("gpt-5-mini", 1000, 100, cached_prompt_tokens=400)
        assert ct.get_summary()["total_cached_prompt_tokens"] == 1000

    def test_missing_cached_rate_falls_back_to_input(self):
        # meta-llama has no cached_input key -> cached billed at full input (0.0 here).
        ct = CostTracker()
        r = ct.record_usage("gpt-5", 1000, 0, cached_prompt_tokens=1000)
        # all-cached at 0.1x input: 1000/1000 * 0.000125
        assert r.estimated_cost_usd == pytest.approx(0.000125)


# ---------------------------------------------------------------------------
# Gateway threading
# ---------------------------------------------------------------------------

class _CachedAdapter(ProviderAdapter):
    provider = "openai"

    def __init__(self):
        self._parsed = ParsedResponse(
            content="ok", finish_reason="stop",
            prompt_tokens=1000, completion_tokens=200, cached_prompt_tokens=800)

    def get_client(self):
        return object()

    def build_params(self, **kwargs):
        return kwargs

    def invoke(self, client, params):
        return "raw"

    def parse(self, raw, *, stage):
        return self._parsed


@pytest.fixture
def events():
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)


class TestGatewayThreadsCached:
    def test_cached_tokens_reach_result_event_and_trace(self, events):
        gw = LLMGateway(stage="synthesizer", cost_tracker=CostTracker(),
                        sleep_fn=lambda _s: None, adapters={"openai": _CachedAdapter()})
        with trace_query("q") as tc:
            result = gw.complete(messages=[{"role": "user", "content": "hi"}], model="gpt-5-mini")
            assert result.cached_prompt_tokens == 800
            assert tc.total_cached_prompt_tokens == 800

        call = next(e for e in events if e["kind"] == "llm_call")
        assert call["cached_prompt_tokens"] == 800
        summary = next(e for e in events if e["kind"] == "query_summary")
        assert summary["total_cached_prompt_tokens"] == 800


# ---------------------------------------------------------------------------
# OpenAI adapter parse
# ---------------------------------------------------------------------------

def _ns(**kw):
    return types.SimpleNamespace(**kw)


class TestOpenAIParseCached:
    def test_captures_cached_tokens(self):
        raw = _ns(
            choices=[_ns(finish_reason="stop", message=_ns(content="answer"))],
            usage=_ns(
                prompt_tokens=1000, completion_tokens=200,
                prompt_tokens_details=_ns(cached_tokens=768),
                completion_tokens_details=_ns(reasoning_tokens=50),
            ),
        )
        parsed = OpenAIAdapter().parse(raw, stage="synthesizer")
        assert parsed.prompt_tokens == 1000
        assert parsed.cached_prompt_tokens == 768
        assert parsed.reasoning_tokens == 50

    def test_no_cached_details_is_zero(self):
        raw = _ns(
            choices=[_ns(finish_reason="stop", message=_ns(content="answer"))],
            usage=_ns(prompt_tokens=1000, completion_tokens=200),
        )
        parsed = OpenAIAdapter().parse(raw, stage="synthesizer")
        assert parsed.cached_prompt_tokens == 0


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------

class TestAnthropicCaching:
    def test_build_params_marks_system_as_cache_breakpoint(self):
        params = AnthropicAdapter().build_params(
            messages=[
                {"role": "system", "content": [{"type": "text", "text": "STATIC PREFIX"}]},
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            ],
            model="claude-opus-4-8", max_completion_tokens=100,
        )
        assert isinstance(params["system"], list)
        assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "STATIC PREFIX" in params["system"][0]["text"]

    def test_parse_normalizes_cache_read_into_prompt_tokens(self):
        raw = _ns(
            content=[_ns(type="text", text="answer")],
            stop_reason="end_turn",
            usage=_ns(input_tokens=100, output_tokens=50,
                      cache_read_input_tokens=900, cache_creation_input_tokens=0),
        )
        parsed = AnthropicAdapter().parse(raw, stage="synthesizer")
        # prompt_tokens folds cache tokens in (OpenAI semantics); cached exposed.
        assert parsed.prompt_tokens == 1000
        assert parsed.cached_prompt_tokens == 900
