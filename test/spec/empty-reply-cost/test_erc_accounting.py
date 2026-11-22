"""C1-C4: a billed reply with no content is recorded, and still raises."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.anthropic_adapter import AnthropicAdapter
from src.llm.gateway.adapters.deepseek_adapter import DeepSeekAdapter
from src.llm.gateway.adapters.openai_adapter import OpenAIAdapter
from src.llm.run_budget import RunBudget, RunBudgetExceeded
from src.observability.trace_context import get_audit_sink, set_audit_sink, trace_query

REPO_ROOT = Path(__file__).resolve().parents[3]
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
EMPTY = "LLM returned empty message content"
DEEPSEEK = "deepseek-flash"
ANTHROPIC = "claude-opus-5-5"
RESPONSES = "gpt-5.6-terra"
# tolerance: float summation only; a few terms, so 1e-12 relative is far above rounding error
REL = 1e-12
TOKENS = st.integers(min_value=1, max_value=50_000)


@pytest.fixture(scope="module", autouse=True)
def _no_audit_sink():
    previous = get_audit_sink()
    set_audit_sink(None)
    yield
    set_audit_sink(previous)


def _rate(model: str) -> dict:
    doc = yaml.safe_load((REPO_ROOT / "configs/llm/pricing.yaml").read_text(encoding="utf-8"))
    assert "tiers" not in doc[model]
    return doc[model]


def _cost(model: str, prompt: int, cached: int, completion: int) -> float:
    rates = _rate(model)
    return ((prompt - cached) * float(rates["input"]) + cached * float(rates["cached_input"])
            + completion * float(rates["output"])) / 1000


def _chat(prompt: int, cached: int, completion: int, content: Any = "") -> SimpleNamespace:
    return SimpleNamespace(
        model=DEEPSEEK,
        choices=[SimpleNamespace(
            finish_reason="length",
            message=SimpleNamespace(role="assistant", content=content),
        )],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=completion),
        ),
    )


def _anthropic(prompt: int, cached: int, completion: int) -> SimpleNamespace:
    # Anthropic reports cache reads apart from input_tokens; a reply that ran out of budget while
    # reasoning holds a thinking block and no text
    return SimpleNamespace(
        id="msg", type="message", role="assistant", model=ANTHROPIC,
        content=[SimpleNamespace(type="thinking", text="...")],
        stop_reason="max_tokens", stop_details=None,
        usage=SimpleNamespace(
            input_tokens=prompt - cached,
            output_tokens=completion,
            cache_read_input_tokens=cached,
            cache_creation_input_tokens=0,
        ),
    )


def _responses(prompt: int, cached: int, completion: int) -> SimpleNamespace:
    return SimpleNamespace(
        object="response", model=RESPONSES, status="incomplete", output_text="",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage=SimpleNamespace(
            input_tokens=prompt,
            output_tokens=completion,
            input_tokens_details=SimpleNamespace(cached_tokens=cached),
            output_tokens_details=SimpleNamespace(reasoning_tokens=completion),
        ),
    )


SHAPES = {
    DEEPSEEK: (DeepSeekAdapter, _chat),
    ANTHROPIC: (AnthropicAdapter, _anthropic),
    RESPONSES: (OpenAIAdapter, _responses),
}


def _wired(base: type, replies: list[Any]) -> Any:
    """A real adapter with only its network methods replaced."""

    class Wired(base):
        def get_client(self) -> Any:
            return object()

        def invoke(self, client: Any, params: dict) -> Any:
            self.invoked += 1
            return replies[min(self.invoked - 1, len(replies) - 1)]

    adapter = Wired(api_key="sk-spec")
    adapter.invoked = 0
    return adapter


def _gateway(adapter: Any, budget: RunBudget | None = None) -> LLMGateway:
    return LLMGateway(
        stage="spec",
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=budget if budget is not None else RunBudget(),
        max_attempts=3,
        sleep_fn=lambda _s: None,
        adapters={p: adapter for p in PROVIDERS},
    )


def _empty_call(model: str, reply: Any) -> tuple[Any, LLMGateway, Any, list[dict]]:
    adapter = _wired(SHAPES[model][0], [reply])
    gateway = _gateway(adapter)
    with trace_query("empty-reply") as trace:
        with pytest.raises(ValueError, match=EMPTY):
            gateway.complete(messages=MESSAGES, model=model, stage="spec")
    return adapter, gateway, trace, [e for e in trace.events if e.get("kind") == "llm_call"]


@pytest.mark.criterion("C1")
def test_the_empty_reply_is_priced():
    # oracle: the hand sum in the spec, 7 x 0.0003 + 1 x 0.000006 + 2 x 0.0012 = 0.004506
    _, gateway, trace, _ = _empty_call(DEEPSEEK, _chat(8000, 1000, 2000))
    assert trace.total_cost_usd == pytest.approx(0.004506, rel=REL)
    assert gateway.cost_tracker.get_summary()["total_cost_usd"] == pytest.approx(0.004506, rel=REL)


@pytest.mark.criterion("C1")
@given(prompt=TOKENS, share=st.floats(min_value=0, max_value=1), completion=TOKENS)
@settings(deadline=None)
def test_any_counts_follow_the_rates(prompt, share, completion):
    # oracle: the closed form over the committed rates, requirement 3
    cached = int(prompt * share)
    _, _, trace, _ = _empty_call(DEEPSEEK, _chat(prompt, cached, completion))
    expected = _cost(DEEPSEEK, prompt, cached, completion)
    assert trace.total_cost_usd == pytest.approx(expected, rel=REL)


@pytest.mark.criterion("C2")
def test_the_event_holds_the_tokens():
    # oracle: requirements 1, 2 and 4, values as given in the reply
    adapter, _, trace, events = _empty_call(DEEPSEEK, _chat(8000, 1000, 2000))
    assert adapter.invoked == 1
    assert len(events) == 1
    event = events[0]
    assert event.get("outcome") == "error"
    assert {k: event.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                                      "cached_prompt_tokens", "cache_write_tokens",
                                      "reasoning_tokens", "finish_reason")} == {
        "prompt_tokens": 8000, "completion_tokens": 2000, "total_tokens": 10000,
        "cached_prompt_tokens": 1000, "cache_write_tokens": 0,
        "reasoning_tokens": 2000, "finish_reason": "length",
    }
    # tolerance: the event rounds cost_usd to 6 decimals, as a successful call's does
    assert event.get("cost_usd") == pytest.approx(0.004506, abs=1e-6)
    assert trace.llm_calls == 1
    assert trace.consecutive_failures == 1


@pytest.mark.criterion("C2")
def test_a_reply_without_usage_counts_at_zero():
    # oracle: requirement 6; the call happened, and nothing says what it cost
    reply = _chat(1, 0, 1)
    reply.usage = None
    _, gateway, trace, events = _empty_call(DEEPSEEK, reply)
    assert trace.llm_calls == 1
    assert trace.total_cost_usd == pytest.approx(0.0, abs=1e-12)
    assert trace.total_prompt_tokens == 0
    assert gateway.cost_tracker.get_summary()["total_cost_usd"] == pytest.approx(0.0, abs=1e-12)
    assert events[0].get("outcome") == "error"


@pytest.mark.criterion("C2")
@given(prompt=TOKENS, completion=TOKENS)
@settings(deadline=None)
def test_any_counts_reach_the_event_and_trace(prompt, completion):
    # oracle: requirements 2 and 4, the reply's own counts
    _, _, trace, events = _empty_call(DEEPSEEK, _chat(prompt, 0, completion))
    assert events[0].get("prompt_tokens") == prompt
    assert events[0].get("completion_tokens") == completion
    assert trace.total_prompt_tokens == prompt
    assert trace.total_completion_tokens == completion


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("model", [ANTHROPIC, RESPONSES])
def test_the_other_shapes_are_recorded(model):
    # oracle: requirement 5 and the closed form over the committed rates
    adapter, _, trace, events = _empty_call(model, SHAPES[model][1](8000, 1000, 2000))
    assert adapter.invoked == 1
    assert trace.total_cost_usd == pytest.approx(_cost(model, 8000, 1000, 2000), rel=REL)
    assert events[0].get("prompt_tokens") == 8000
    assert events[0].get("cached_prompt_tokens") == 1000
    # oracle: each adapter's own finish reason for a reply cut off at the budget
    assert events[0].get("finish_reason") == {ANTHROPIC: "max_tokens", RESPONSES: "length"}[model]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("model", [DEEPSEEK, ANTHROPIC, RESPONSES])
def test_doubling_the_tokens_doubles_the_cost(model):
    # oracle: the rates are linear in tokens, whatever the shape
    build = SHAPES[model][1]
    _, _, once, _ = _empty_call(model, build(4000, 500, 1000))
    _, _, twice, _ = _empty_call(model, build(8000, 1000, 2000))
    assert once.total_cost_usd > 0
    assert twice.total_cost_usd == pytest.approx(2 * once.total_cost_usd, rel=REL)


def _second_call_reaches_provider(limit: float) -> tuple[bool, int]:
    """Runs an empty reply, then a second call; returns (halted, provider calls)."""
    good = _chat(100, 0, 10, content="{}")
    good.choices[0].finish_reason = "stop"
    adapter = _wired(DeepSeekAdapter, [_chat(8000, 1000, 2000), good])
    gateway = _gateway(adapter, RunBudget(max_cost_usd=limit))
    halted = False
    with trace_query("empty-then-next"):
        with pytest.raises(ValueError, match=EMPTY):
            gateway.complete(messages=MESSAGES, model=DEEPSEEK, stage="spec")
        try:
            gateway.complete(messages=MESSAGES, model=DEEPSEEK, stage="spec")
        except RunBudgetExceeded:
            halted = True
    return halted, adapter.invoked


@pytest.mark.criterion("C4")
def test_the_next_call_sees_the_spend():
    # oracle: requirement 4; 0.004506 was spent, so a 0.004 limit stops the second call
    assert _second_call_reaches_provider(0.004) == (True, 1)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_the_limit_decides_either_side_of_the_cost():
    # oracle: requirement 4; just over the spend the second call goes out, just under it halts
    assert _second_call_reaches_provider(0.0046) == (False, 2)
    assert _second_call_reaches_provider(0.0044) == (True, 1)
