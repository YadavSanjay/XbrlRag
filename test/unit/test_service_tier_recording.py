"""Service tier recording: requested and served tier per call, rolled up per run, in R1.

The provider's reply and the OpenAI client are faked (the I/O boundary); the adapters, the
gateway, the trace, the eval runner, the checkpoint payload and the R1 report are real.
Expected counts are the calls each test scripts, counted by hand.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.core import EvalCase, EvalRunner, RunnerConfig, SystemAnswer
from src.eval.core.checkpoint.base import NullRunStore
from src.eval.core.checkpoint.run import checkpoint_payload, run_checkpointed
from src.eval.core.r1_report import build_r1, render_markdown
from src.eval.core.report import CaseResult
from src.eval.systems._stage_llm import answer_in_trace
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway, LLMRefusalError
from src.llm.gateway.adapters.anthropic_adapter import AnthropicAdapter
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter, served_tier_of
from src.llm.gateway.adapters.openai_adapter import OpenAIAdapter
from src.llm.request_options import SERVICE_TIER_ENV
from src.llm.run_budget import RunBudget
from src.observability.service_tiers import UNREPORTED, UNSET, merge_counts
from src.observability.trace_context import get_audit_sink, set_audit_sink, trace_query

MESSAGES = [{"role": "user", "content": "Say ok."}]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv(SERVICE_TIER_ENV, raising=False)
    previous = get_audit_sink()
    set_audit_sink(None)
    yield
    set_audit_sink(previous)


# ---------------------------------------------------------------------------- fakes


def _chat_reply(service_tier: Any = None) -> SimpleNamespace:
    """An OpenAI Chat Completions reply; ``service_tier`` set unless None."""
    reply = SimpleNamespace(
        id="chatcmpl_unit", model="gpt-5-mini-2025-08-07",
        choices=[SimpleNamespace(index=0, finish_reason="stop",
                                 message=SimpleNamespace(role="assistant", content="ok"))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12,
                              prompt_tokens_details=None, completion_tokens_details=None),
    )
    if service_tier is not None:
        reply.service_tier = service_tier
    return reply


class _FakeOpenAIClient:
    """Chat Completions only: records each request's kwargs, replies with the next tier."""

    def __init__(self, tiers: list[str | None]) -> None:
        self.tiers = list(tiers)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **params: Any) -> SimpleNamespace:
        self.requests.append(params)
        return _chat_reply(self.tiers.pop(0))


def _openai_gateway(tiers: list[str | None]) -> tuple[LLMGateway, _FakeOpenAIClient]:
    adapter = OpenAIAdapter(api_key="unit")
    client = _FakeOpenAIClient(tiers)
    adapter._client = client  # the SDK client is the I/O boundary
    gateway = LLMGateway(stage="unit", cost_tracker=CostTracker(), rate_limiter=None,
                         run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                         adapters={"openai": adapter})
    return gateway, client


class _AnthropicLike(ProviderAdapter):
    """A non-OpenAI provider that reports a served tier, scripted per call."""

    provider = "anthropic"

    def __init__(self, steps: list[str]) -> None:
        self.steps = list(steps)

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {"model": kwargs["model"]}

    def invoke(self, client: Any, params: dict) -> Any:
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        step = self.steps.pop(0)
        usage = ParsedResponse(content="ok", finish_reason="stop", prompt_tokens=10,
                               completion_tokens=2, served_tier="standard")
        if step == "refusal":
            raise LLMRefusalError("declined", usage=usage, billed=True)
        if step == "error":
            raise ValueError("permanent")
        return usage


def _anthropic_gateway(steps: list[str]) -> LLMGateway:
    adapter = _AnthropicLike(steps)
    return LLMGateway(stage="unit", cost_tracker=CostTracker(), rate_limiter=None,
                      run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                      adapters={"anthropic": adapter})


# ---------------------------------------------------------------------------- adapters


@pytest.mark.parametrize(("value", "expected"), [
    ("flex", "flex"), ("default", "default"), (None, None), ("", None), ("  ", None),
    (3, None),
])
def test_openai_chat_parse_carries_the_reply_service_tier(value, expected):
    reply = _chat_reply()
    reply.service_tier = value
    assert OpenAIAdapter(api_key="unit").parse(reply, stage="unit").served_tier == expected


def test_openai_responses_parse_carries_the_reply_service_tier():
    reply = SimpleNamespace(
        id="resp_unit", object="response", output_text="ok", status="completed",
        incomplete_details=None, service_tier="flex",
        usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12,
                              input_tokens_details=None, output_tokens_details=None),
    )
    adapter = OpenAIAdapter(api_key="unit")
    assert adapter._parse_responses(reply, stage="unit").served_tier == "flex"


def test_anthropic_parse_reads_the_tier_from_usage():
    reply = SimpleNamespace(
        id="msg_unit", type="message", role="assistant", model="claude-x",
        content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
        stop_details=None,
        usage=SimpleNamespace(input_tokens=10, output_tokens=2, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0, service_tier="standard"),
    )
    assert AnthropicAdapter(api_key="unit").parse(reply, stage="unit").served_tier == "standard"


def test_served_tier_of_prefers_the_reply_over_usage():
    raw = SimpleNamespace(service_tier="flex", usage=SimpleNamespace(service_tier="standard"))
    assert served_tier_of(raw) == "flex"
    assert served_tier_of(SimpleNamespace()) is None


# ---------------------------------------------------------------------------- gateway + trace


def test_openai_calls_count_the_sent_tier_against_each_served_tier(monkeypatch):
    monkeypatch.setenv(SERVICE_TIER_ENV, "flex")
    gateway, client = _openai_gateway(["flex", "default", None])
    with trace_query("q") as trace:
        for _ in range(3):
            gateway.complete(messages=MESSAGES, model="gpt-5-mini", stage="synth")
    assert [r.get("service_tier") for r in client.requests] == ["flex", "flex", "flex"]
    assert trace.service_tiers == {
        "openai/synth": {"flex": {"flex": 1, "default": 1, UNREPORTED: 1}}}


def test_no_tier_sent_is_counted_as_unset():
    gateway, client = _openai_gateway(["default", "default"])
    with trace_query("q") as trace:
        gateway.complete(messages=MESSAGES, model="gpt-5-mini", stage="plan")
        gateway.complete(messages=MESSAGES, model="gpt-5-mini", stage="verify")
    assert "service_tier" not in client.requests[0]
    assert trace.service_tiers == {"openai/plan": {UNSET: {"default": 1}},
                                   "openai/verify": {UNSET: {"default": 1}}}


def test_a_non_openai_provider_never_counts_the_run_tier_as_requested(monkeypatch):
    # The run's Flex tier is an OpenAI-platform setting; the adapter never sends it here.
    monkeypatch.setenv(SERVICE_TIER_ENV, "flex")
    gateway = _anthropic_gateway(["ok", "refusal", "error"])
    with trace_query("q") as trace:
        gateway.complete(messages=MESSAGES, model="claude-sonnet-5-5", stage="judge")
        with pytest.raises(LLMRefusalError):
            gateway.complete(messages=MESSAGES, model="claude-sonnet-5-5", stage="judge")
        with pytest.raises(ValueError):
            gateway.complete(messages=MESSAGES, model="claude-sonnet-5-5", stage="judge")
    # The billed refusal reached the provider and counts; the failed call does not.
    assert trace.service_tiers == {"anthropic/judge": {UNSET: {"standard": 2}}}


def test_an_eval_answer_carries_its_trace_tier_counts(monkeypatch):
    monkeypatch.setenv(SERVICE_TIER_ENV, "flex")
    gateway, _ = _openai_gateway(["flex", "flex"])

    def produce() -> SystemAnswer:
        gateway.complete(messages=MESSAGES, model="gpt-5-mini", stage="long_context")
        gateway.complete(messages=MESSAGES, model="gpt-5-mini", stage="long_context")
        return SystemAnswer(text="ok")

    answer = answer_in_trace("q?", produce)
    assert answer.service_tiers == {"openai/long_context": {"flex": {"flex": 2}}}


# ---------------------------------------------------------------------------- run config


def _case(cid: str) -> EvalCase:
    return EvalCase(id=cid, question="q?", kind="lookup", expected_value=100.0, rel_tol=1e-4)


class _TierSystem:
    """Answers every case with fixed tier counts."""

    name = "tier_system"

    def __init__(self, counts: dict) -> None:
        self.counts = counts

    def answer(self, case: EvalCase) -> SystemAnswer:
        return SystemAnswer(numeric_values=[100.0], fact_ids=[1], cost_usd=0.01,
                            service_tiers=json.loads(json.dumps(self.counts)))


PER_CASE = {"openai/synth": {"flex": {"flex": 2, "default": 1}},
            "openai/plan": {"flex": {"flex": 1}}}


def test_runner_sums_case_counts_into_the_run_config():
    report = EvalRunner(RunnerConfig(max_workers=2)).run(
        [_case("a"), _case("b"), _case("c")], _TierSystem(PER_CASE))
    assert report.config["service_tier_calls"] == {
        "openai/plan": {"flex": {"flex": 3}},
        "openai/synth": {"flex": {"default": 3, "flex": 6}},
    }
    assert report.config["max_workers"] == 2


def test_checkpoint_payload_round_trips_the_counts():
    report = EvalRunner(RunnerConfig(max_workers=1)).run([_case("a")], _TierSystem(PER_CASE))
    result = report.results[0]
    payload = json.loads(json.dumps(checkpoint_payload(result)))
    assert payload["service_tiers"] == PER_CASE
    assert CaseResult.from_dict(payload, result.case).service_tiers == PER_CASE


def test_checkpointed_run_records_counts_and_concurrency():
    system = _TierSystem(PER_CASE)
    (report,) = run_checkpointed(
        [_case("a"), _case("b")], [system], store=NullRunStore(),
        identities=[{"system": system.name}], config=RunnerConfig(max_workers=3),
    )
    assert report.config["service_tier_calls"] == {
        "openai/plan": {"flex": {"flex": 2}},
        "openai/synth": {"flex": {"default": 2, "flex": 4}},
    }
    assert report.config["max_workers"] == 3
    assert report.config["segments"][0]["max_workers"] == 3


def test_merge_counts_skips_malformed_entries():
    merged = merge_counts([{"k": {"flex": {"flex": 1, "bad": "x", "flag": True}}},
                           {"k": "oops"}, "not a mapping"])  # type: ignore[list-item]
    assert merged == {"k": {"flex": {"flex": 1}}}


# ---------------------------------------------------------------------------- R1


def _row(cid: str, vc: bool) -> dict:
    return {"case_id": cid, "tags": ["split:dev", "shape:screen.top"], "value_correct": vc,
            "confident_wrong": False, "grounding_precision": 1.0, "cost_usd": 0.01,
            "latency_ms": 100.0, "error": None}


def _arm(label: str, config: dict) -> dict:
    return {"label": label, "run_id": f"eval-{label}", "system": label, "suite": "s",
            "split": "dev", "config": config, "cases": [_row("a", True), _row("b", False)]}


def _cells(md: str, label: str) -> list[str]:
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and cells and cells[0] == label:
            return cells[1:]
    raise AssertionError(f"no row {label!r} in:\n{md}")


def test_r1_prints_served_tiers_and_concurrency_beside_latency():
    x_config = {"service_tier": "flex", "max_workers": 8, "kind_concurrency": {"narrative": 1},
                "cache_groups": "filing", "scheduled_with": ["O"],
                "segments": [{"max_workers": 8, "service_tier": "flex"},
                             {"max_workers": 4, "service_tier": "flex"}],
                "service_tier_calls": {
                    "openai/synth": {"flex": {"flex": 5, "default": 1}},
                    "openai/plan": {"flex": {"flex": 2}}}}
    f_config = {"max_workers": 4, "service_tier_calls": {}}
    o_config: dict = {}
    r1 = build_r1([_arm("X", x_config), _arm("F", f_config), _arm("O", o_config)], "X", "F")
    md = render_markdown(r1)
    assert _cells(md, "LLM calls, requested -> served tier") == [
        "flex -> default 1, flex 7", "no LLM calls", "not recorded"]
    assert _cells(md, "service tier") == ["flex", "not recorded", "not recorded"]
    concurrency = _cells(md, "concurrency")
    assert concurrency[0] == ("max_workers 8, per kind narrative 1, cache groups filing, "
                              "scheduled with O; resumed in segments: max_workers 8 tier "
                              "flex, max_workers 4 tier flex")
    assert concurrency[1] == "max_workers 4"
    assert concurrency[2] == "not recorded"
    labels = [line.split("|")[1].strip() for line in md.splitlines() if line.startswith("| ")]
    latency = labels.index("latency p50 / p95 (ms)")
    assert labels[latency + 1: latency + 4] == [
        "service tier", "LLM calls, requested -> served tier", "concurrency"]
