"""C1: one tool-calling turn through the real gateway."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway, LLMRefusalError, OpenAIAdapter, resolve_model_ref
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.gateway.tools import (
    ToolCall,
    ToolSpec,
    ToolsUnsupportedError,
    tool_result_message,
)
from src.llm.run_budget import RunBudget, RunHalt
from src.observability.trace_context import trace_query

REF = resolve_model_ref("gpt-5.6-sol")
USER = [{"role": "user", "content": "go"}]


def _tool() -> ToolSpec:
    return ToolSpec("calculate", "Does arithmetic.", {"type": "object", "properties": {}})


class OneTurn(ProviderAdapter):
    """A fake provider: one call, then text, with a queue of errors first."""

    def __init__(self, errors: list[BaseException] | None = None, prompt=1000, completion=500):
        self.provider = "fake"
        self.errors = list(errors or [])
        self.prompt = prompt
        self.completion = completion
        self.invokes = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        raise AssertionError("complete_tools must use build_tool_params")

    def build_tool_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.invokes += 1
        if self.errors:
            raise self.errors.pop(0)
        return "reply"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        raise AssertionError("complete_tools must use parse_tool_turn")

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        call = ToolCall("c1", "calculate", {"expression": "1+1"})
        message = {"role": "assistant", "content": "", "provider_state": {"x": 1},
                   "tool_calls": [{"id": "c1", "name": "calculate",
                                   "arguments": {"expression": "1+1"}}]}
        return ParsedResponse(
            content="", finish_reason="tool_calls", prompt_tokens=self.prompt,
            completion_tokens=self.completion, tool_calls=[call], assistant_message=message,
        )


class Refuses(OneTurn):
    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        raise LLMRefusalError("declined")


class NoTools(ProviderAdapter):
    """A provider adapter that has no tool support of its own."""

    provider = "fake"

    def __init__(self) -> None:
        self.invokes = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.invokes += 1
        return "reply"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content="x", finish_reason="stop", prompt_tokens=1,
                              completion_tokens=1)


def _call(gateway: LLMGateway, **kwargs: Any):
    return gateway.complete_tools(messages=USER, tools=[_tool()], model_ref=REF, **kwargs)


@pytest.mark.criterion("C1")
def test_values_keep_their_attributes():
    # oracle: requirement 1 — plain values, attributes of the same names
    spec = ToolSpec("a", "b", {"type": "object"})
    call = ToolCall("id", "a", None, "bad json")
    assert (spec.name, spec.description, spec.parameters) == ("a", "b", {"type": "object"})
    assert (call.call_id, call.name, call.arguments, call.arguments_error) == (
        "id", "a", None, "bad json")
    assert ToolCall("id", "a", {"x": 1}).arguments_error is None
    assert tool_result_message("id", "text") == {
        "role": "tool", "tool_call_id": "id", "content": "text"}


@pytest.mark.criterion("C1")
def test_a_turn_returns_its_calls_and_message(gateway_on):
    # oracle: requirement 3 — a turn with calls and no text is a success
    result = _call(gateway_on(OneTurn()))
    assert [c.name for c in result.tool_calls] == ["calculate"]
    assert result.tool_calls[0].arguments == {"expression": "1+1"}
    assert result.content == ""
    assert result.assistant_message["provider_state"] == {"x": 1}
    assert result.assistant_message["role"] == "assistant"


@pytest.mark.criterion("C1")
def test_a_turn_is_costed_and_traced(gateway_on, turn_cost):
    # oracle: requirement 3 — the closed form at MODEL_PRICING rates, on the result and the trace
    with trace_query("q") as trace:
        result = _call(gateway_on(OneTurn()))
    expected = turn_cost(1000, 500)
    # tolerance: float sum of two rate products
    assert result.cost_usd == pytest.approx(expected, rel=1e-9)
    assert trace.total_cost_usd == pytest.approx(expected, rel=1e-9)
    assert trace.llm_calls == 1


@pytest.mark.criterion("C1")
def test_the_event_counts_the_calls(gateway_on):
    # oracle: requirement 3 — the llm_call event adds tool_calls
    with trace_query("q") as trace:
        _call(gateway_on(OneTurn()), stage="frontier_sec_tools", prompt_version="v1")
    events = [e for e in trace.events if e["kind"] == "llm_call"]
    assert len(events) == 1
    assert events[0]["tool_calls"] == 1
    assert events[0]["outcome"] == "ok"
    assert events[0]["stage"] == "frontier_sec_tools"
    assert events[0]["prompt_version"] == "v1"


@pytest.mark.criterion("C1")
def test_a_transient_error_is_retried(gateway_on):
    # oracle: requirement 3 — retries on transient errors only, as complete does
    adapter = OneTurn(errors=[TimeoutError("slow")])
    result = _call(gateway_on(adapter, max_attempts=3))
    assert adapter.invokes == 2
    assert result.retries == 1


@pytest.mark.criterion("C1")
def test_a_permanent_error_is_not_retried(gateway_on):
    # oracle: requirement 3
    adapter = OneTurn(errors=[KeyError("bad")])
    with pytest.raises(KeyError):
        _call(gateway_on(adapter, max_attempts=3))
    assert adapter.invokes == 1


@pytest.mark.criterion("C1")
def test_a_refusal_is_final(gateway_on):
    # oracle: requirement 3 — refusals stay final
    adapter = Refuses()
    with pytest.raises(LLMRefusalError):
        _call(gateway_on(adapter, max_attempts=3))
    assert adapter.invokes == 1


@pytest.mark.criterion("C1")
def test_the_run_budget_stops_before_spending(gateway_on):
    # oracle: requirement 3 and the repo's RAG rules — a budget of no calls makes no call
    adapter = OneTurn()
    gateway = gateway_on(adapter, RunBudget(max_llm_calls=0))
    with trace_query("q"), pytest.raises(RunHalt):
        _call(gateway)
    assert adapter.invokes == 0


@pytest.mark.criterion("C1")
def test_too_many_cache_points_raise_before_any_call(gateway_on):
    # oracle: requirement 3 — the same order as complete; four is over MAX_CACHE_POINTS
    adapter = OneTurn()
    marked = [{"role": "user", "content": str(i), "cache_point": True} for i in range(4)]
    with pytest.raises(ValueError, match="cache point"):
        gateway_on(adapter).complete_tools(messages=marked, tools=[_tool()], model_ref=REF)
    assert adapter.invokes == 0


@pytest.mark.criterion("C1")
def test_a_reply_with_neither_text_nor_call_raises(gateway_on):
    # oracle: requirement 3 — an empty reply is unusable, as in complete
    adapter = OpenAIAdapter(api_key="sk-spec")
    empty = SimpleNamespace(
        object="response", status="completed", output=[], output_text="", model="gpt-5.6-sol",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, input_tokens_details=None,
                              output_tokens_details=None),
    )
    adapter._client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **_kwargs: empty)
    )
    gateway = gateway_on(adapter)
    with pytest.raises(ValueError, match="empty"):
        _call(gateway)


@pytest.mark.criterion("C1")
def test_a_provider_without_tools_is_refused_before_a_call():
    # oracle: requirement 4 — the base adapter's own methods raise; nothing is sent
    adapter = NoTools()
    gateway = LLMGateway(stage="x", cost_tracker=CostTracker(), adapters={"openai": adapter})
    with pytest.raises(ToolsUnsupportedError):
        _call(gateway)
    assert adapter.invokes == 0
    with pytest.raises(ToolsUnsupportedError):
        adapter.parse_tool_turn("raw", stage="x")
    assert issubclass(ToolsUnsupportedError, ValueError)


@pytest.mark.criterion("C1")
@given(prompt=st.integers(1, 136_000), completion=st.integers(1, 10**5))
def test_doubling_tokens_doubles_the_cost(gateway_on, prompt, completion):
    # oracle: cost is linear in tokens at fixed rates. Re-lock: requirement 36 adds a tier above
    # 272K, so both prompts stay at or below it (2 * 136,000); the tier has its own test.
    single = _call(gateway_on(OneTurn(prompt=prompt, completion=completion)))
    double = _call(gateway_on(OneTurn(prompt=2 * prompt, completion=2 * completion)))
    # tolerance: float products of the same rates
    assert double.cost_usd == pytest.approx(2 * single.cost_usd, rel=1e-9)
    assert single.cost_usd > 0
