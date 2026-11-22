"""Fakes and builders for the flex-retry-halt spec tests (docs/specs/flex-retry-halt.md).

The fakes sit only at the provider boundary:

- a look-alike of openai 2.14.0's ``RateLimitError`` for the Flex "resource unavailable" 429
  (class name, ``status_code``, ``body`` = the reply's ``"error"`` member, ``code``/``type``,
  a case-insensitive ``response.headers``), copied from the llm-flex-retry spec conftest;
- ``ScriptAdapter``: a fake provider adapter behind a real ``LLMGateway`` (real
  ``CostTracker``, real trace). Each attempt plays one script item: an exception is raised, a
  callable is run first (a hook standing for another stage or worker changing the real
  trace while the request is in flight) and its result played, anything else is the reply.
- ``Sleeper``: the gateway's ``sleep_fn``. It records every sleep and may run a hook on a
  given sleep (the same kind of outside change, during the wait). No test sleeps.

The environment variables the change reads are cleared for the session and set through
``env_vars`` (a context manager, so hypothesis tests can use it).
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

TIER_ENV = "XBRLRAG_OPENAI_SERVICE_TIER"
ATTEMPTS_ENV = "XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS"
BASE_ENV = "XBRLRAG_FLEX_RETRY_BASE_S"
MAX_ENV = "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S"
CAP_ENV = "XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S"
_ENV_VARS = (TIER_ENV, ATTEMPTS_ENV, BASE_ENV, MAX_ENV, CAP_ENV,
             "XBRLRAG_RUN_MAX_COST_USD", "XBRLRAG_RUN_MAX_LLM_CALLS", "XBRLRAG_RUN_MAX_TOKENS",
             "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES")

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
OPENAI_MODEL = "gpt-6.1-sol"
MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off: no trace these tests open is persisted."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture(scope="session", autouse=True)
def _clean_env():
    """Clears the tier, Flex policy, cap and run-budget variables for the session."""
    saved = {k: os.environ.pop(k) for k in _ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        for k in _ENV_VARS:
            os.environ.pop(k, None)
        os.environ.update(saved)


@contextlib.contextmanager
def _env_vars(**values: str | None) -> Iterator[None]:
    """Sets (a str) or unsets (None) environment variables for the block."""
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(scope="session")
def env_vars():
    """Context manager: ``with env_vars(NAME="value", OTHER=None): ...``."""
    return _env_vars


def policy_env(attempts: int | None = None, base: float | None = None,
               maximum: float | None = None, cap: float | None = None) -> dict[str, str | None]:
    """The Flex policy and cap variables; None leaves a variable unset (its default)."""
    return {
        ATTEMPTS_ENV: None if attempts is None else str(attempts),
        BASE_ENV: None if base is None else repr(float(base)),
        MAX_ENV: None if maximum is None else repr(float(maximum)),
        CAP_ENV: None if cap is None else repr(float(cap)),
    }


# ---------------------------------------------------------------------------- SDK errors


class _Headers(dict):
    """A case-insensitive header mapping, as ``httpx.Headers`` is."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        super().__init__({k.lower(): v for k, v in (values or {}).items()})

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(key.lower(), default)


class _Response:
    """The parts of an ``httpx.Response`` an SDK error carries."""

    def __init__(self, status: int, headers: dict[str, str] | None) -> None:
        self.status_code = status
        self.headers = _Headers(headers)


class RateLimitError(Exception):
    """openai 2.14.0's ``RateLimitError`` shape: ``status_code`` 429, ``body`` the error."""

    def __init__(self, message: str, *, body: dict, headers: dict[str, str] | None) -> None:
        super().__init__(message)
        self.message = message
        self.response = _Response(429, headers)
        self.status_code = 429
        self.body = body
        self.code = body.get("code")
        self.type = body.get("type")


def flex(headers: dict[str, str] | None = None) -> RateLimitError:
    """The Flex 429: code ``resource_unavailable`` (a Flex 429 at any tier)."""
    error = {"message": "Resource unavailable.", "type": "server_error", "param": None,
             "code": "resource_unavailable"}
    return RateLimitError(f"Error code: 429 - {json.dumps({'error': error})}", body=error,
                          headers=headers)


# ---------------------------------------------------------------------------- fakes


class ScriptAdapter(ProviderAdapter):
    """Plays one item per attempt; when the script runs out, every attempt answers *final*."""

    def __init__(self, script: list[Any] | None = None, *, final: str = "ok") -> None:
        self.provider = "fake"
        self.script = list(script or [])
        self.final = final
        self.invoked = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return json.loads(json.dumps({k: v for k, v in kwargs.items() if k != "extra"}))

    def build_tool_params(self, **kwargs: Any) -> dict:
        kwargs = dict(kwargs)
        kwargs["tools"] = [t.name for t in kwargs["tools"]]
        return json.loads(json.dumps(kwargs))

    def invoke(self, client: Any, params: dict) -> Any:
        self.invoked += 1
        outcome = self.script.pop(0) if self.script else self.final
        if callable(outcome) and not isinstance(outcome, BaseException):
            outcome = outcome()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        return self.parse(raw, stage=stage)


class Sleeper:
    """The gateway's ``sleep_fn``: records each sleep; ``hooks[i]`` runs during sleep *i*.

    Sleep indices count from 1 over the sleeper's whole life.
    """

    def __init__(self, hooks: dict[int, Callable[[], None]] | None = None) -> None:
        self.sleeps: list[float] = []
        self.hooks = dict(hooks or {})

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        hook = self.hooks.get(len(self.sleeps))
        if hook is not None:
            hook()


def make_gateway(adapter: ProviderAdapter, sleeper: Sleeper, *,
                 run_budget: RunBudget | None = None, max_attempts: int = 3) -> LLMGateway:
    """A real ``LLMGateway``: real private ``CostTracker``, *adapter* under every provider."""
    return LLMGateway(
        stage="spec",
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=run_budget if run_budget is not None else RunBudget(),
        max_attempts=max_attempts,
        sleep_fn=sleeper,
        adapters={p: adapter for p in PROVIDERS},
    )


def call(gateway: LLMGateway) -> tuple[str, Any]:
    """One ``complete`` call; ``("ok", result)`` or ``("raised", exception)``."""
    try:
        return "ok", gateway.complete(messages=MESSAGES, model=OPENAI_MODEL, stage="spec")
    except Exception as exc:
        return "raised", exc


def events(trace: Any, kind: str) -> list[dict]:
    return [e for e in trace.events if e.get("kind") == kind]


def flex_waits(attempts: int, base: float, maximum: float) -> list[float]:
    """llm-flex-retry requirement 4's closed form: the waits after Flex 429s 1..attempts-1."""
    return [min(maximum, base * 2 ** (k - 1)) for k in range(1, attempts)]


def slices(wait: float, size: float = 10.0) -> list[float]:
    """Requirement 6's slices of *wait*: ``size`` each, the last the remainder."""
    out: list[float] = []
    left = wait
    while left > 1e-12:
        piece = min(size, left)
        out.append(piece)
        left -= piece
    return out


@pytest.fixture(scope="session")
def h():
    """The builders and helpers above, as attributes."""
    class _H:
        pass
    for name, value in {
        "flex": flex, "ScriptAdapter": ScriptAdapter, "Sleeper": Sleeper,
        "make_gateway": make_gateway, "call": call, "events": events,
        "flex_waits": flex_waits, "slices": slices, "policy_env": policy_env,
        "messages": MESSAGES, "model": OPENAI_MODEL, "cap_env": CAP_ENV,
    }.items():
        setattr(_H, name, staticmethod(value) if callable(value) and not isinstance(value, type)
                else value)
    return _H
