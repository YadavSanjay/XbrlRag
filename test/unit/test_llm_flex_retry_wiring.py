"""Wiring of the Flex 429 retry policy that the locked spec tests do not reach.

docs/specs/llm-flex-retry.md: ``run_suite.py`` refuses a bad policy before any cost, a bad
policy met mid-run fails loud, a run that never meets a Flex 429 never reads the policy, and an
exhausted Flex call records nothing in the cost tracker.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.profiles import LlmConfigError
from src.llm.request_options import (
    FLEX_RETRY_BASE_ENV,
    FLEX_RETRY_MAX_ATTEMPTS_ENV,
    FLEX_RETRY_MAX_BACKOFF_ENV,
    SERVICE_TIER_ENV,
)
from src.observability.trace_context import get_audit_sink, set_audit_sink, trace_query

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "run_suite.py"
_MESSAGES = [{"role": "user", "content": "u"}]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in (SERVICE_TIER_ENV, FLEX_RETRY_MAX_ATTEMPTS_ENV, FLEX_RETRY_BASE_ENV,
                 FLEX_RETRY_MAX_BACKOFF_ENV):
        monkeypatch.delenv(name, raising=False)
    previous = get_audit_sink()
    set_audit_sink(None)
    yield
    set_audit_sink(previous)


class RateLimitError(Exception):
    """A look-alike of openai's 429: ``status_code``, the error object as ``body``."""

    def __init__(self, code: str = "resource_unavailable") -> None:
        super().__init__("Error code: 429")
        self.status_code = 429
        self.body = {"message": "Resource unavailable.", "type": "server_error", "code": code}
        self.response = None


class _Script(ProviderAdapter):
    provider = "openai"

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {"model": kwargs["model"]}

    def invoke(self, client: Any, params: dict) -> Any:
        self.calls += 1
        outcome = self.script.pop(0) if self.script else "ok"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)


def _gateway(adapter: ProviderAdapter, sleeps: list[float], tracker: CostTracker) -> LLMGateway:
    return LLMGateway(stage="unit", cost_tracker=tracker, sleep_fn=sleeps.append,
                      adapters={"openai": adapter})


def test_a_bad_policy_met_mid_run_fails_loud(monkeypatch):
    monkeypatch.setenv(FLEX_RETRY_MAX_ATTEMPTS_ENV, "many")
    adapter = _Script([RateLimitError()])
    sleeps: list[float] = []
    gateway = _gateway(adapter, sleeps, CostTracker())
    with pytest.raises(LlmConfigError, match=FLEX_RETRY_MAX_ATTEMPTS_ENV):
        gateway.complete(messages=_MESSAGES, model="gpt-6.1-sol")
    assert adapter.calls == 1
    assert sleeps == []


def test_a_call_without_flex_429s_never_reads_the_policy(monkeypatch):
    monkeypatch.setenv(FLEX_RETRY_MAX_ATTEMPTS_ENV, "many")
    gateway = _gateway(_Script(["fine"]), [], CostTracker())
    assert gateway.complete(messages=_MESSAGES, model="gpt-6.1-sol").content == "fine"


def test_the_flex_retry_event_names_the_model_and_error(monkeypatch):
    monkeypatch.setenv(FLEX_RETRY_MAX_ATTEMPTS_ENV, "3")
    sleeps: list[float] = []
    gateway = _gateway(_Script([RateLimitError()]), sleeps, CostTracker())
    with trace_query("unit-flex") as trace:
        gateway.complete(messages=_MESSAGES, model="gpt-6.1-sol")
    (retry,) = [e for e in trace.events if e.get("kind") == "retry"]
    assert retry["error_class"] == "flex_unavailable"
    assert retry["provider"] == "openai"
    assert retry["model"] == "gpt-6.1-sol"
    assert "RateLimitError" in retry["error"]
    assert retry["max_attempts"] == 3
    assert sleeps == [5.0]


def test_an_exhausted_flex_call_records_no_cost(monkeypatch):
    monkeypatch.setenv(FLEX_RETRY_MAX_ATTEMPTS_ENV, "2")
    tracker = CostTracker()
    gateway = _gateway(_Script([RateLimitError(), RateLimitError()]), [], tracker)
    with trace_query("unit-flex-exhausted") as trace, pytest.raises(RateLimitError):
        gateway.complete(messages=_MESSAGES, model="gpt-6.1-sol")
    (call,) = [e for e in trace.events if e.get("kind") == "llm_call"]
    assert call["error_class"] == "flex_unavailable"
    assert "cost_usd" not in call
    assert trace.llm_calls == 0
    assert tracker.get_summary()["total_calls"] == 0


@pytest.fixture(scope="module")
def run_suite():
    spec = importlib.util.spec_from_file_location("run_suite_flex_wiring", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_flex_wiring"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name, value", [
    (FLEX_RETRY_MAX_ATTEMPTS_ENV, "0"),
    (FLEX_RETRY_BASE_ENV, "nan"),
    (FLEX_RETRY_MAX_BACKOFF_ENV, "1"),
])
def test_run_suite_refuses_a_bad_policy_before_any_cost(run_suite, monkeypatch, tmp_path,
                                                        capsys, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(run_suite, "_run", lambda *_a, **_k: pytest.fail("the run started"))
    missing = tmp_path / "no_such_suite.json"
    with pytest.raises(SystemExit) as exit_info:
        run_suite.main(["--suite-path", str(missing), "--system", "closed_book",
                        "--llm-profile", "deepseek"])
    assert exit_info.value.code == 2
    assert name in capsys.readouterr().err
