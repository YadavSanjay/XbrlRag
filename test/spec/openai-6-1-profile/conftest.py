"""Fixtures for the openai-6-1-profile spec tests (docs/specs/openai-6-1-profile.md).

Everything here is written by hand from the spec, never read back from the code under test:

- ``PER_MILLION``: requirement 1's price table in USD per 1M tokens, as the OpenAI pricing
  page (Standard, read 2026-11-03) and the measurement plan §5 / §12 list them;
- ``tier_env(value)``: sets or clears ``XBRLRAG_OPENAI_SERVICE_TIER`` for a ``with`` block and
  restores the previous value (usable inside hypothesis tests, unlike ``monkeypatch``);
- ``FakeOpenAIClient``: the OpenAI SDK client boundary. It records the kwargs of every
  ``chat.completions.create`` / ``responses.create`` call and returns a plain reply object;
- ``CapturingAdapter``: a fake ``ProviderAdapter`` (the provider boundary) that records the
  params ``invoke`` receives and answers with fixed usage;
- ``make_gateway(adapters)``: a real ``LLMGateway`` with a real private ``CostTracker``, one
  attempt, no rate limiter and an inactive run budget;
- ``FakeSystem`` and ``make_cases``: a ``SystemUnderTest`` for ``EvalRunner`` and its cases.

No network, no database, no data/ and no real keys.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.gateway.adapters.openai_adapter import OpenAIAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
RUN_SUITE = REPO_ROOT / "scripts" / "eval" / "run_suite.py"

TIER_ENV = "XBRLRAG_OPENAI_SERVICE_TIER"
TIERS = ("auto", "default", "flex", "priority")
LONG_CONTEXT_STAGES = ("long_context", "long_context_multi", "frontier_sec_tools")
LONG_CONTEXT_TIMEOUT_S = 900.0
LONG_PROMPT_THRESHOLD = 272_000

# Requirement 1, USD per 1M tokens: (input, cached read, cache write, output).
PER_MILLION: dict[str, dict[str, tuple[float, float, float, float]]] = {
    "gpt-6.1-sol": {"base": (2.00, 0.10, 2.50, 10.00), "long": (4.00, 0.20, 5.00, 15.00)},
    "gpt-6.1-terra": {"base": (1.00, 0.05, 1.25, 5.00), "long": (2.00, 0.10, 2.50, 7.50)},
    # llm-pricing-refresh C2: gpt-6-luna's long-context tier, over 272K input tokens (OpenAI
    # pricing page, Standard, read 2026-11-14).
    "gpt-6-luna": {"base": (0.10, 0.01, 0.125, 0.50), "long": (0.20, 0.02, 0.25, 0.75)},
}

# Requirement 2.
PROFILE_61 = {"small": "gpt-6-luna", "medium": "gpt-6.1-terra", "frontier": "gpt-6.1-sol"}
CEILINGS_61 = {"gpt-6-luna": 4000, "gpt-6.1-terra": 8000, "gpt-6.1-sol": 8000}
UNCHANGED_PROFILES = {
    "openai": {"small": "gpt-5-nano", "medium": "gpt-5-mini", "frontier": "gpt-5"},
    "openai-5.6": {"small": "gpt-5.6-luna", "medium": "gpt-5.6-terra", "frontier": "gpt-5.6-sol"},
    "anthropic": {
        "small": "claude-haiku-5-5", "medium": "claude-sonnet-5-5", "frontier": "claude-opus-5-5",
    },
    "deepseek": {"small": "deepseek-flash", "medium": "deepseek-flash",
                 "frontier": "deepseek-v4-pro"},
}

# A model with a committed pricing row and llm.yaml row on main, for gateway calls whose
# subject is the request, not the model.
GATEWAY_MODEL = "gpt-5-mini"

MESSAGES = [
    {"role": "system", "content": "Answer in JSON."},
    {"role": "user", "content": "What was AAA revenue in fiscal 2024?"},
]
CACHED_MESSAGES = [
    {"role": "system", "content": "Answer in JSON.", "cache_point": True},
    {"role": "user", "content": "What was AAA revenue in fiscal 2024?"},
]


def hand_cost(model: str, *, prompt: int, cached: int, written: int, completion: int) -> float:
    """The cost of one call from the per-1M table, the tier chosen by ``prompt > 272,000``."""
    rows = PER_MILLION[model]
    key = "long" if prompt > LONG_PROMPT_THRESHOLD and "long" in rows else "base"
    inp, read, write, out = rows[key]
    return ((prompt - cached - written) * inp + cached * read + written * write
            + completion * out) / 1_000_000


@contextlib.contextmanager
def tier_env(value: str | None) -> Iterator[None]:
    """Sets ``XBRLRAG_OPENAI_SERVICE_TIER`` to *value* (None: unset) inside the block."""
    previous = os.environ.get(TIER_ENV)
    if value is None:
        os.environ.pop(TIER_ENV, None)
    else:
        os.environ[TIER_ENV] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(TIER_ENV, None)
        else:
            os.environ[TIER_ENV] = previous


@pytest.fixture(autouse=True)
def _clean_tier_env():
    """Every test starts with the variable unset, whatever the shell has."""
    with tier_env(None):
        yield


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off: nothing these tests do is persisted."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


# ---------------------------------------------------------------------------- SDK boundary


def chat_reply() -> SimpleNamespace:
    """A plain object shaped like an OpenAI Chat Completions reply."""
    usage = SimpleNamespace(
        prompt_tokens=120, completion_tokens=30, total_tokens=150,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    choice = SimpleNamespace(index=0, finish_reason="stop",
                             message=SimpleNamespace(role="assistant", content='{"ok": true}'))
    return SimpleNamespace(id="chatcmpl_spec", object="chat.completion", choices=[choice],
                           usage=usage)


def responses_reply() -> SimpleNamespace:
    """A plain object shaped like an OpenAI Responses reply."""
    usage = SimpleNamespace(
        input_tokens=200, output_tokens=40, total_tokens=240,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    return SimpleNamespace(id="resp_spec", object="response", output_text='{"ok": true}',
                           status="completed", incomplete_details=None, usage=usage)


class _Recorder:
    def __init__(self, calls: list[tuple[str, dict]], name: str, reply: Any) -> None:
        self._calls = calls
        self._name = name
        self._reply = reply

    def create(self, **kwargs: Any) -> Any:
        self._calls.append((self._name, dict(kwargs)))
        return self._reply()


class FakeOpenAIClient:
    """The OpenAI SDK client: records each create call's kwargs, answers with a plain reply."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.chat = SimpleNamespace(completions=_Recorder(self.calls, "chat", chat_reply))
        self.responses = _Recorder(self.calls, "responses", responses_reply)


class FakeClientOpenAIAdapter(OpenAIAdapter):
    """The real OpenAI adapter whose SDK client (the network boundary) is the fake."""

    def __init__(self, client: FakeOpenAIClient) -> None:
        super().__init__(api_key="sk-spec")
        self._fake_client = client

    def get_client(self) -> Any:
        return self._fake_client


class CapturingAdapter(ProviderAdapter):
    """A fake provider: builds a minimal request and records what ``invoke`` receives."""

    def __init__(self) -> None:
        self.provider = "fake"
        self.invoked: list[dict] = []

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {"model": kwargs["model"], "messages": list(kwargs["messages"])}

    def build_tool_params(self, **kwargs: Any) -> dict:
        return {"model": kwargs["model"], "input": list(kwargs["messages"]),
                "tools": [t.name for t in kwargs["tools"]]}

    def invoke(self, client: Any, params: dict) -> Any:
        self.invoked.append(dict(params))
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content='{"ok": true}', finish_reason="stop",
                              prompt_tokens=100, completion_tokens=10)

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        parsed = ParsedResponse(content="done", finish_reason="stop",
                                prompt_tokens=100, completion_tokens=10)
        parsed.assistant_message = {"role": "assistant", "content": "done", "tool_calls": []}
        return parsed


def make_gateway(adapters: dict[str, ProviderAdapter]) -> LLMGateway:
    """A real gateway over *adapters*: one attempt, no limiter, an inactive run budget."""
    return LLMGateway(
        stage="spec",
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=RunBudget(),
        max_attempts=1,
        sleep_fn=lambda _s: None,
        adapters=dict(adapters),
    )


# ---------------------------------------------------------------------------- runner fakes


class FakeSystem:
    """A ``SystemUnderTest`` that answers every case with 1.0 and fact 1 at no cost."""

    def __init__(self, name: str = "fake_system") -> None:
        self.name = name
        self.answered: list[str] = []

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.answered.append(case.id)
        return SystemAnswer(numeric_values=[1.0], fact_ids=[1], cost_usd=0.0)


def make_cases(ids: list[str]) -> list[EvalCase]:
    """Scalar lookup cases with expected value 1.0 and fact 1."""
    return [EvalCase(id=i, question=f"question {i}", kind="lookup", expected_value=1.0,
                     expected_fact_ids=[1]) for i in ids]


@pytest.fixture(scope="session")
def spec() -> SimpleNamespace:
    """The constants and helpers above, for the test modules."""
    return SimpleNamespace(
        repo_root=REPO_ROOT,
        run_suite=RUN_SUITE,
        tier_env_name=TIER_ENV,
        tiers=TIERS,
        long_context_stages=LONG_CONTEXT_STAGES,
        long_context_timeout_s=LONG_CONTEXT_TIMEOUT_S,
        long_prompt_threshold=LONG_PROMPT_THRESHOLD,
        per_million=PER_MILLION,
        profile_61=PROFILE_61,
        ceilings_61=CEILINGS_61,
        unchanged_profiles=UNCHANGED_PROFILES,
        gateway_model=GATEWAY_MODEL,
        messages=MESSAGES,
        cached_messages=CACHED_MESSAGES,
        hand_cost=hand_cost,
        tier_env=tier_env,
        fake_openai_client=FakeOpenAIClient,
        fake_client_openai_adapter=FakeClientOpenAIAdapter,
        capturing_adapter=CapturingAdapter,
        make_gateway=make_gateway,
        fake_system=FakeSystem,
        make_cases=make_cases,
    )
