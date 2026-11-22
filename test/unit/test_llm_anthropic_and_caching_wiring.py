"""Unit tests for llm-anthropic-and-caching behaviour the locked spec tests do not cover.

Spec: docs/specs/llm-anthropic-and-caching.md. Covers the transient classifier's edge
cases, the OpenAI SDK keyword shim, Responses edge cases, the long-context prompt split,
``StageLLM``'s multi-message call, and how refusals surface through the gateway, the
synthesizer and the pipeline. Fakes sit only at the provider boundary (adapters / SDK
clients); the gateway, adapters, stages and arm are real.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from prompts.prompts import PromptTemplate
from src.eval.core.system import SystemAnswer
from src.eval.systems._stage_llm import StageLLM, UserPart
from src.eval.systems.long_context import CACHE_POINT_MARKER, split_user_template
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters import AnthropicAdapter, OpenAIAdapter
from src.llm.gateway.adapters.base import (
    LLMRefusalError,
    ParsedResponse,
    ProviderAdapter,
    classify_transient,
)
from src.llm.run_budget import RunBudget
from src.observability.trace_context import trace_query

_PROVIDERS = ("openai", "openai_compat", "anthropic", "huggingface")


class _Adapter(ProviderAdapter):
    """A fake provider: records params; ``parse`` raises the queued errors, then answers."""

    def __init__(self, errors: list[BaseException] | None = None, reply: str = "{}") -> None:
        self.provider = "fake"
        self.errors = list(errors or [])
        self.reply = reply
        self.params: list[dict] = []

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return dict(kwargs)

    def invoke(self, client: Any, params: dict) -> Any:
        self.params.append(params)
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        if self.errors:
            raise self.errors.pop(0)
        return ParsedResponse(content=self.reply, finish_reason="stop",
                              prompt_tokens=10, completion_tokens=2)


def _gateway(adapter: ProviderAdapter, max_attempts: int = 3) -> LLMGateway:
    return LLMGateway(stage="unit", cost_tracker=CostTracker(), run_budget=RunBudget(),
                      max_attempts=max_attempts, sleep_fn=lambda _s: None,
                      adapters={p: adapter for p in _PROVIDERS})


# ---------------------------------------------------------------------------- transient


def _error(name: str, *, status: int | None = None, body: Any = None) -> Exception:
    exc = type(name, (Exception,), {})("boom")
    if status is not None:
        exc.status_code = status
    exc.body = body
    return exc


def test_status_decides_over_a_transient_sounding_name():
    # "APIError" is a transient name, but a 400 status is a caller error.
    assert classify_transient(_error("APIError", status=400)) is False
    assert classify_transient(_error("APIError")) is True


def test_error_event_inside_a_streamed_200_is_classified_by_its_body():
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    invalid = {"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}
    assert classify_transient(_error("APIStatusError", status=200, body=overloaded)) is True
    assert classify_transient(_error("APIStatusError", status=200, body=invalid)) is False


# ---------------------------------------------------------------------------- OpenAI


class _TypedResponses:
    """An SDK-like ``responses.create`` with a fixed keyword signature (no **kwargs)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, *, model: str, input: list, max_output_tokens: int | None = None,
               extra_body: dict | None = None) -> str:
        self.calls.append({"model": model, "input": input,
                           "max_output_tokens": max_output_tokens, "extra_body": extra_body})
        return "raw"


def test_invoke_sends_fields_the_sdk_does_not_declare_in_extra_body():
    responses = _TypedResponses()
    client = SimpleNamespace(responses=responses)
    params = {"model": "gpt-5.6-sol", "input": [], "max_output_tokens": 10,
              "prompt_cache_options": {"mode": "explicit"}, "extra_body": {"a": 1}}
    assert OpenAIAdapter(api_key="sk-unit").invoke(client, params) == "raw"
    assert responses.calls == [{
        "model": "gpt-5.6-sol", "input": [], "max_output_tokens": 10,
        "extra_body": {"a": 1, "prompt_cache_options": {"mode": "explicit"}},
    }]


def _responses_reply(**overrides: Any) -> SimpleNamespace:
    reply = SimpleNamespace(
        object="response", output_text="ok", status="completed", incomplete_details=None,
        usage=SimpleNamespace(input_tokens=100, output_tokens=5,
                              input_tokens_details=SimpleNamespace(cached_tokens=0),
                              output_tokens_details=None),
    )
    for key, value in overrides.items():
        setattr(reply, key, value)
    return reply


def test_responses_content_filter_is_a_value_error():
    raw = _responses_reply(status="incomplete",
                           incomplete_details=SimpleNamespace(reason="content_filter"))
    with pytest.raises(ValueError, match="content filter"):
        OpenAIAdapter(api_key="sk-unit").parse(raw, stage="unit")


def test_responses_cache_counts_never_exceed_the_prompt():
    usage = SimpleNamespace(input_tokens=100, output_tokens=5,
                            input_tokens_details=SimpleNamespace(cached_tokens=90,
                                                                 cache_write_tokens=50))
    parsed = OpenAIAdapter(api_key="sk-unit").parse(_responses_reply(usage=usage), stage="u")
    assert (parsed.cached_prompt_tokens, parsed.cache_write_tokens) == (90, 10)


def test_chat_reply_still_parses_after_the_responses_branch():
    raw = SimpleNamespace(
        object="chat.completion",
        choices=[SimpleNamespace(finish_reason="stop",
                                 message=SimpleNamespace(content="hello"))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1,
                              completion_tokens_details=None, prompt_tokens_details=None),
    )
    parsed = OpenAIAdapter(api_key="sk-unit").parse(raw, stage="unit")
    assert (parsed.content, parsed.prompt_tokens) == ("hello", 3)


# ---------------------------------------------------------------------------- Anthropic


def test_refusal_without_a_category_still_names_the_refusal():
    raw = SimpleNamespace(stop_reason="refusal", stop_details=None, content=[], usage=None)
    with pytest.raises(LLMRefusalError, match="refusal"):
        AnthropicAdapter(api_key="sk-ant-unit").parse(raw, stage="unit")


def test_anthropic_list_content_is_flattened_to_text():
    params = AnthropicAdapter(api_key="sk-ant-unit").build_params(
        messages=[{"role": "user", "content": [{"type": "text", "text": "a"},
                                               {"type": "text", "text": "b"}],
                   "cache_point": True}],
        model="claude-zz-unconfigured-9",
    )
    assert params["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "a\nb", "cache_control": {"type": "ephemeral"}}]}]


# ---------------------------------------------------------------------------- gateway


def test_refusal_does_not_advance_the_circuit_breaker():
    adapter = _Adapter(errors=[LLMRefusalError("declined")])
    with trace_query("unit") as trace:
        with pytest.raises(LLMRefusalError):
            _gateway(adapter).complete(messages=[{"role": "user", "content": "q"}],
                                       model="gpt-5-mini")
        assert trace.consecutive_failures == 0


# ---------------------------------------------------------------------------- long context


def test_split_user_template_requires_one_marker_line():
    with pytest.raises(ValueError):
        split_user_template(PromptTemplate("FILING:\n{filing}\nQUESTION:\n{question}"))
    with pytest.raises(ValueError):
        split_user_template(PromptTemplate(
            f"{{filing}}\n{CACHE_POINT_MARKER}\nx\n{CACHE_POINT_MARKER}\n{{question}}"))
    with pytest.raises(ValueError):  # the question on the cached side
        split_user_template(PromptTemplate(f"{{question}}\n{CACHE_POINT_MARKER}\n{{filing}}"))


def test_stage_llm_sends_user_parts_as_separate_messages():
    adapter = _Adapter()
    llm = StageLLM("long_context", _gateway(adapter), model="gpt-5.6-sol")
    with trace_query("unit"):
        llm.complete([UserPart("FILING", cache_point=True), UserPart("QUESTION")])
        llm.complete("ONE MESSAGE")
    parts_call, string_call = (p["messages"] for p in adapter.params)
    assert [m["role"] for m in parts_call] == ["system", "user", "user"]
    assert parts_call[1] == {"role": "user", "content": "FILING", "cache_point": True}
    assert parts_call[2] == {"role": "user", "content": "QUESTION"}
    assert string_call[1:] == [{"role": "user", "content": "ONE MESSAGE"}]


def test_arm_refusal_is_graded_as_a_refusal_not_an_error():
    from src.eval.systems._stage_llm import answer_in_trace

    llm = StageLLM("long_context",
                   _gateway(_Adapter(errors=[LLMRefusalError("declined (refusal)")])),
                   model="gpt-5.6-sol")
    answer = answer_in_trace("q", lambda: SystemAnswer(text=llm.complete("q")))
    assert answer.refused is True
    assert answer.error is None
    assert answer.query_id


# ---------------------------------------------------------------------------- stages


class _FakeExecutionResult:
    steps: list = []

    def to_dict(self) -> dict:
        return {"steps": []}


def test_synthesizer_refusal_is_declined_without_escalation(monkeypatch):
    from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer

    monkeypatch.setenv("OPENAI_API_KEY", "sk-unit")
    synth = XbrlRagSynthesizer(api_key="sk-unit")
    adapter = _Adapter(errors=[LLMRefusalError("declined (refusal category: cyber)")],
                       reply=json.dumps({"answer_summary": "should not be reached"}))
    synth._llm_client = _gateway(adapter)
    with trace_query("q"):
        resp = synth.generate_answer("q", {"steps": []}, _FakeExecutionResult())
    assert len(adapter.params) == 1  # no retry, no escalation rung
    assert resp.metadata["fully_answered"] is False
    assert resp.metadata["synthesis_error"]["type"] == "refusal"
    assert resp.unanswered_parts == [{"part": "q", "reason": "synthesizer_refusal"}]
    assert "declined" in resp.answer_summary


def test_pipeline_turns_a_refusal_into_a_declined_answer():
    from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline

    class _Refusing(XbrlRetrievalPipeline):
        def __init__(self) -> None:  # no backends: only generate()'s handling is tested
            self.query_cache = SimpleNamespace(enabled=False)

        def _generate_uncached(self, query: str):
            raise LLMRefusalError("the model declined to answer (refusal category: bio)")

    resp = _Refusing().generate("q")
    assert resp.metadata["refused"] is True
    assert resp.metadata["fully_answered"] is False
    assert resp.execution_result["status"] == "ERROR"  # never cached
    assert "declined" in resp.unanswered_parts[0]["reason"]
