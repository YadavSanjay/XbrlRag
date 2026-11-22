"""Fakes and builders for the served-model-id spec tests.

Spec: docs/specs/served-model-id.md. The fakes sit only at the I/O boundaries the spec
names:

- fake raw provider replies for ``OpenAIAdapter.parse`` / ``AnthropicAdapter.parse``
  (``SimpleNamespace`` objects shaped like the SDK responses, copied from
  test/spec/llm-anthropic-and-caching/conftest.py and
  test/spec/llm-refusals-and-store/conftest.py), with a ``model`` attribute that can be
  set, ``None`` or absent;
- ``ScriptedAdapter``: a fake ``ProviderAdapter`` behind a real ``LLMGateway`` (with a
  real ``CostTracker``) that answers, refuses or fails per call, as scripted;
- ``FakePipeline``: an injected pipeline for ``XbrlRagFullSystem`` that opens its own
  trace and calls a real gateway;
- ``FakeSystem``: a ``SystemUnderTest`` for ``EvalRunner`` returning configured answers.

The new fields do not exist before the implementation, so the fakes set them by
attribute assignment after construction and never pass them to a constructor.

Fixtures are session-scoped factories so hypothesis tests can use them. Everything is
synthetic; no network, no data/ and no real keys.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import LLMRefusalError, ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink, trace_query

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]

# Every provider key a model id could resolve to, so a test does not depend on which one
# the gateway infers.
PROVIDERS = ("openai", "openai_compat", "anthropic", "huggingface")

# A requested id with a committed pricing row, and a served id with none (spec Notes).
REQUESTED = "gpt-5-mini"
OTHER_REQUESTED = "gpt-5-nano"
UNPRICED_SERVED = "gpt-5-mini-2099-01-01"

MESSAGES = [
    {"role": "system", "content": "You are a test."},
    {"role": "user", "content": "Say ok."},
]

_ABSENT = object()


# ---------------------------------------------------------------------------- audit sink


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off: no trace these tests open is persisted."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture
def capturing_sink():
    """For ``XbrlRagFullSystem``: its own in-memory capturing sink, persisting nothing.

    ``XbrlRagFullSystem`` reads the completed trace from the sink it installs once per
    process; if an earlier test in the process installed it and the session fixture
    above then cleared it, the trace would not be captured. This puts the real
    capturing sink (with no delegate, so nothing is persisted) back for one test.
    """
    from src.eval.systems.xbrlrag_full import _CapturingSink

    previous = get_audit_sink()
    set_audit_sink(_CapturingSink(None))
    try:
        yield
    finally:
        set_audit_sink(previous)


# ---------------------------------------------------------------------------- committed files


@pytest.fixture(scope="session")
def pricing_row():
    """Factory: a model's committed row in configs/llm/pricing.yaml (USD per 1,000 tokens)."""
    doc = yaml.safe_load((REPO_ROOT / "configs" / "llm" / "pricing.yaml").read_text(
        encoding="utf-8"))

    def row(model: str) -> dict[str, Any] | None:
        value = doc.get(model)
        return dict(value) if value is not None else None

    return row


# ---------------------------------------------------------------------------- fake raw replies


def _set_model(ns: SimpleNamespace, model: Any) -> SimpleNamespace:
    if model is not _ABSENT:
        ns.model = model
    return ns


def _chat_reply(
    content: str = '{"ok": true}',
    *,
    model: Any = _ABSENT,
    finish_reason: str = "stop",
    prompt_tokens: int = 120,
    completion_tokens: int = 30,
    cached_tokens: int = 20,
    reasoning_tokens: int = 5,
) -> SimpleNamespace:
    """An OpenAI Chat Completions reply; *model* absent unless given."""
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    choice = SimpleNamespace(
        index=0,
        finish_reason=finish_reason,
        message=SimpleNamespace(role="assistant", content=content),
    )
    reply = SimpleNamespace(id="chatcmpl_spec", object="chat.completion",
                            choices=[choice], usage=usage)
    return _set_model(reply, model)


def _responses_reply(
    output_text: str = '{"ok": true}',
    *,
    model: Any = _ABSENT,
    input_tokens: int = 200,
    output_tokens: int = 40,
    cached_tokens: int = 50,
    reasoning_tokens: int = 7,
) -> SimpleNamespace:
    """An OpenAI Responses reply; *model* absent unless given."""
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    reply = SimpleNamespace(
        id="resp_spec",
        object="response",
        output_text=output_text,
        status="completed",
        incomplete_details=None,
        usage=usage,
    )
    return _set_model(reply, model)


def _anthropic_reply(
    text: str = '{"ok": true}',
    *,
    model: Any = _ABSENT,
    stop_reason: str = "end_turn",
    category: str | None = None,
    input_tokens: int = 300,
    output_tokens: int = 25,
    cache_read: int = 100,
    cache_creation: int = 40,
) -> SimpleNamespace:
    """An Anthropic Messages reply (a refusal when ``stop_reason="refusal"``)."""
    content = [SimpleNamespace(type="text", text=text)] if text else []
    stop_details = SimpleNamespace(category=category) if category is not None else None
    reply = SimpleNamespace(
        id="msg_spec",
        type="message",
        role="assistant",
        content=content,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        ),
    )
    return _set_model(reply, model)


@pytest.fixture(scope="session")
def absent():
    """The sentinel meaning "the raw reply has no ``model`` attribute"."""
    return _ABSENT


@pytest.fixture(scope="session")
def chat_reply():
    """Factory: a fake Chat Completions reply."""
    return _chat_reply


@pytest.fixture(scope="session")
def responses_reply():
    """Factory: a fake Responses reply."""
    return _responses_reply


@pytest.fixture(scope="session")
def anthropic_reply():
    """Factory: a fake Anthropic Messages reply."""
    return _anthropic_reply


# ---------------------------------------------------------------------------- gateway fakes


def _parsed(served: str | None, *, prompt_tokens: int, completion_tokens: int,
            content: str = '{"ok": true}', finish_reason: str = "stop") -> ParsedResponse:
    """A ``ParsedResponse`` carrying *served* as its new attribute (set after construction)."""
    parsed = ParsedResponse(
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    parsed.served_model = served  # type: ignore[attr-defined]  # the field requirement 1 adds
    return parsed


class ScriptedAdapter(ProviderAdapter):
    """A fake adapter; each ``parse`` plays the next scripted step.

    A step is ``("ok", served)``, ``("refusal", served)`` (a billed refusal whose usage
    carries *served*) or ``("error", None)`` (a permanent ``ValueError``).
    """

    def __init__(self, steps: list[tuple[str, str | None]], *, prompt_tokens: int = 1000,
                 completion_tokens: int = 100) -> None:
        self.provider = "fake"
        self.steps = list(steps)
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.parsed = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return dict(kwargs)

    def invoke(self, client: Any, params: dict) -> Any:
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        kind, served = self.steps[self.parsed]
        self.parsed += 1
        if kind == "error":
            raise ValueError("scripted permanent failure")
        usage = _parsed(served, prompt_tokens=self.prompt_tokens,
                        completion_tokens=self.completion_tokens)
        if kind == "refusal":
            usage.content = ""
            usage.finish_reason = "refusal"
            raise LLMRefusalError("the model declined to answer (refusal category: bio)",
                                  category="bio", usage=usage, billed=True)
        return usage


def _gateway(adapter: ProviderAdapter) -> LLMGateway:
    return LLMGateway(
        stage="spec",
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=RunBudget(),
        max_attempts=1,
        sleep_fn=lambda _s: None,
        adapters={p: adapter for p in PROVIDERS},
    )


@pytest.fixture(scope="session")
def scripted_adapter():
    """Factory: ``scripted_adapter(steps, prompt_tokens=, completion_tokens=)``."""
    return ScriptedAdapter


@pytest.fixture(scope="session")
def make_gateway():
    """Factory: ``make_gateway(adapter) -> LLMGateway``.

    A real ``LLMGateway`` with a real, private ``CostTracker``, *adapter* under every
    provider key, one attempt, no rate limiter and an inactive run budget.
    """
    return _gateway


def _play(gateway: LLMGateway, steps: list[tuple[str, str | None]],
          models: list[str]) -> list[Any]:
    """Makes one call per step (requested id ``models[i]``, the last one repeating);
    returns each result or the exception it raised."""
    out: list[Any] = []
    for i in range(len(steps)):
        model = models[min(i, len(models) - 1)]
        try:
            out.append(gateway.complete(messages=MESSAGES, model=model, stage="spec"))
        except (LLMRefusalError, ValueError) as exc:
            out.append(exc)
    return out


@pytest.fixture(scope="session")
def play():
    """Factory: ``play(gateway, steps, models) -> [result | exception]``."""
    return _play


# ---------------------------------------------------------------------------- full system


def _ok_response() -> SimpleNamespace:
    """A minimal answered ``XbrlRagResponse``-like object: one OK lookup step."""
    return SimpleNamespace(
        answer_summary="Revenue was 1.",
        metadata={"fully_answered": True},
        unanswered_parts=[],
        execution_result={"steps": [{"op": "lookup", "status": "OK", "value": 1.0,
                                     "citation": {"fact_id": 1}}]},
        narrative_chunks=[],
    )


def _declined_response() -> SimpleNamespace:
    """A declining response: nothing answered, the question flagged unanswered."""
    return SimpleNamespace(
        answer_summary="I cannot answer this.",
        metadata={"fully_answered": False},
        unanswered_parts=["the question"],
        execution_result={"steps": []},
        narrative_chunks=[],
    )


class FakePipeline:
    """An injected pipeline: opens its own trace and calls a real gateway per step.

    *outcome* is ``"ok"`` (an answered response), ``"refusal"`` (a refusal raised by
    the gateway is turned into a declining response, as the real pipeline declines) or
    ``"error"`` (``generate`` raises a permanent error after its calls).
    """

    def __init__(self, gateway: LLMGateway, steps: list[tuple[str, str | None]],
                 models: list[str], *, outcome: str = "ok") -> None:
        self.gateway = gateway
        self.steps = list(steps)
        self.models = list(models)
        self.outcome = outcome
        self.traces: list[Any] = []

    def generate(self, question: str) -> Any:
        with trace_query(question) as trace:
            self.traces.append(trace)
            results = _play(self.gateway, self.steps, self.models)
            if self.outcome == "error":
                raise ValueError("scripted pipeline failure")
            if self.outcome == "refusal" or any(
                    isinstance(r, LLMRefusalError) for r in results):
                return _declined_response()
            return _ok_response()


@pytest.fixture(scope="session")
def fake_pipeline():
    """Factory: ``fake_pipeline(gateway, steps, models, outcome="ok")``."""
    return FakePipeline


# ---------------------------------------------------------------------------- runner fakes


class FakeSystem:
    """A ``SystemUnderTest`` answering each case with its configured ``served_models``.

    ``served[case_id]`` is set on the answer by attribute assignment (the field
    requirement 4 adds). Every answer costs *cost_usd*.
    """

    name = "fake_system"

    def __init__(self, served: dict[str, dict[str, list[str]]], *,
                 cost_usd: float = 0.0) -> None:
        self.served = {k: {m: list(v) for m, v in d.items()} for k, d in served.items()}
        self.cost_usd = cost_usd
        self.answered: list[str] = []

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.answered.append(case.id)
        answer = SystemAnswer(numeric_values=[1.0], fact_ids=[1], cost_usd=self.cost_usd)
        answer.served_models = {  # type: ignore[attr-defined]  # requirement 4's field
            m: list(v) for m, v in self.served.get(case.id, {}).items()
        }
        return answer


@pytest.fixture(scope="session")
def fake_system():
    """Factory: ``fake_system(served_by_case_id, cost_usd=0.0) -> FakeSystem``."""
    return FakeSystem


@pytest.fixture(scope="session")
def make_cases():
    """Factory: ``make_cases(ids) -> [EvalCase]`` (scalar lookups, expected value 1.0)."""

    def make(ids: list[str]) -> list[EvalCase]:
        return [EvalCase(id=i, question=f"question {i}", kind="lookup",
                         expected_value=1.0, expected_fact_ids=[1]) for i in ids]

    return make


def union_served(maps: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    """The oracle union: requested id -> sorted distinct served ids (empty lists dropped)."""
    acc: dict[str, set[str]] = {}
    for m in maps:
        for requested, served in m.items():
            if served:
                acc.setdefault(requested, set()).update(served)
    return {k: sorted(v) for k, v in acc.items()}


@pytest.fixture(scope="session")
def union():
    """The oracle union function (see ``union_served``)."""
    return union_served
