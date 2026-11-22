"""Fakes and builders for the llm-flex-retry spec tests (docs/specs/llm-flex-retry.md).

The fakes sit only at the provider boundary:

- look-alikes of the SDKs' exception classes (``RateLimitError`` and friends), shaped as the
  pinned clients raise them (openai 2.14.0 passes the reply's ``"error"`` member as ``body``;
  anthropic 0.109.1 passes the whole JSON body; headers on ``response.headers``);
- ``ScriptAdapter``: a fake provider adapter behind a real ``LLMGateway`` (real
  ``CostTracker``, real trace) that raises or answers per attempt;
- ``RefusingTransport``: the SEC HTTP seam under a real ``SecToolbox``, refusing every request.

Sleeps go to the gateway's ``sleep_fn`` and are recorded; no test sleeps. The environment
variables the change reads are set through ``env_vars`` (a context manager, so hypothesis
tests can use it) and cleared for the whole session.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.keys.base import HttpTransport
from src.eval.sec_tools.client import SecApiClient, SecSnapshot
from src.eval.sec_tools.toolbox import SecToolbox
from src.eval.systems.frontier_sec_tools import FrontierSecToolsSystem
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.gateway.tools import ToolCall
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

TIER_ENV = "XBRLRAG_OPENAI_SERVICE_TIER"
ATTEMPTS_ENV = "XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS"
BASE_ENV = "XBRLRAG_FLEX_RETRY_BASE_S"
MAX_ENV = "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S"
_ENV_VARS = (TIER_ENV, ATTEMPTS_ENV, BASE_ENV, MAX_ENV)

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
OPENAI_MODEL = "gpt-6.1-sol"
CLAUDE_MODEL = "claude-haiku-5-5"
MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


# ---------------------------------------------------------------------------- session state


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
    """Clears the tier and the Flex policy variables for the session, then restores them."""
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


@pytest.fixture(scope="session")
def names():
    """The environment variable names, as an object with attributes."""
    class _Names:
        tier = TIER_ENV
        attempts = ATTEMPTS_ENV
        base = BASE_ENV
        max = MAX_ENV
    return _Names


# ---------------------------------------------------------------------------- SDK errors


#
# Look-alikes of the pinned SDKs' exceptions (openai 2.14.0 ``_exceptions.py``, anthropic
# 0.109.1): the same class names (``classify_transient`` matches by name), ``status_code``,
# ``body`` where each client puts it, ``code``/``type`` as OpenAI's ``APIError`` sets them, and
# ``response.headers`` as a case-insensitive mapping like ``httpx.Headers``. The spec lint keeps
# network packages out of spec tests, so the SDK classes themselves are not imported.


class _Headers(dict):
    """A case-insensitive header mapping, as ``httpx.Headers`` is."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        super().__init__({k.lower(): v for k, v in (values or {}).items()})

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(key.lower(), default)

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key.lower())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and super().__contains__(key.lower())


class _Response:
    """The parts of an ``httpx.Response`` an SDK error carries."""

    def __init__(self, status: int, headers: dict[str, str] | None) -> None:
        self.status_code = status
        self.headers = _Headers(headers)


class _StatusError(Exception):
    """``APIStatusError``: message, ``response``, ``status_code``, ``body``, ``code``, ``type``."""

    def __init__(self, message: str, *, status: int, body: Any,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.response = _Response(status, headers)
        self.status_code = status
        self.body = body
        inner = body if isinstance(body, dict) else {}
        self.code = inner.get("code")
        self.type = inner.get("type")


class RateLimitError(_StatusError):
    pass


class InternalServerError(_StatusError):
    pass


class BadRequestError(_StatusError):
    pass


class APITimeoutError(Exception):
    """``openai.APITimeoutError``: no status and no response."""

    def __init__(self) -> None:
        super().__init__("Request timed out.")
        self.message = "Request timed out."
        self.body = None


def _openai_429(
    *,
    code: str | None = "resource_unavailable",
    type_: str | None = "server_error",
    message: str = "Resource unavailable.",
    headers: dict[str, str] | None = None,
) -> RateLimitError:
    """A 429 as openai 2.14.0 raises it: ``body`` is the reply's ``"error"`` member."""
    error = {"message": message, "type": type_, "param": None, "code": code}
    return RateLimitError(f"Error code: 429 - {json.dumps({'error': error})}", status=429,
                          body=error, headers=headers)


def _openai_status(status: int, *, headers: dict[str, str] | None = None) -> _StatusError:
    """An OpenAI status error (500 → ``InternalServerError``, 400 → ``BadRequestError``)."""
    kind = "server_error" if status >= 500 else "invalid_request_error"
    error = {"message": "boom", "type": kind, "param": None, "code": None}
    cls = InternalServerError if status >= 500 else BadRequestError
    return cls(f"Error code: {status} - {json.dumps({'error': error})}", status=status,
               body=error, headers=headers)


def _openai_timeout() -> APITimeoutError:
    return APITimeoutError()


def _anthropic_429(*, headers: dict[str, str] | None = None) -> RateLimitError:
    """A 429 as anthropic 0.109.1 raises it: ``body`` is the whole JSON body."""
    message = "Number of requests has exceeded your rate limit"
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": message}}
    return RateLimitError(f"Error code: 429 - {json.dumps(body)}", status=429, body=body,
                          headers=headers)


@pytest.fixture(scope="session")
def sdk_errors():
    """The error builders, as an object with attributes."""
    class _Errors:
        openai_429 = staticmethod(_openai_429)
        openai_status = staticmethod(_openai_status)
        openai_timeout = staticmethod(_openai_timeout)
        anthropic_429 = staticmethod(_anthropic_429)
    return _Errors


def flex(**kwargs: Any) -> RateLimitError:
    """The canonical Flex 429: code ``resource_unavailable``."""
    return _openai_429(**kwargs)


# ---------------------------------------------------------------------------- fake adapter


class ScriptAdapter(ProviderAdapter):
    """Plays one outcome per attempt: an exception is raised, anything else is the reply text.

    When the script runs out, every further attempt answers *final*.
    """

    def __init__(self, script: list[Any] | None = None, *, final: str = "ok") -> None:
        self.provider = "fake"
        self.script = list(script or [])
        self.final = final
        self.params: list[dict] = []

    @property
    def invoked(self) -> int:
        return len(self.params)

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return json.loads(json.dumps({k: v for k, v in kwargs.items() if k != "extra"}))

    def build_tool_params(self, **kwargs: Any) -> dict:
        kwargs = dict(kwargs)
        kwargs["tools"] = [t.name for t in kwargs["tools"]]
        return json.loads(json.dumps(kwargs))

    def invoke(self, client: Any, params: dict) -> Any:
        self.params.append(json.loads(json.dumps(params)))
        outcome = self.script.pop(0) if self.script else self.final
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)


def _gateway(adapter: ProviderAdapter, *, max_attempts: int = 3,
             sleeps: list[float] | None = None,
             run_budget: RunBudget | None = None, stage: str = "spec") -> LLMGateway:
    sink = sleeps if sleeps is not None else []
    return LLMGateway(
        stage=stage,
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=run_budget if run_budget is not None else RunBudget(),
        max_attempts=max_attempts,
        sleep_fn=sink.append,
        adapters={p: adapter for p in PROVIDERS},
    )


@pytest.fixture(scope="session")
def script_adapter():
    """The ``ScriptAdapter`` class."""
    return ScriptAdapter


@pytest.fixture(scope="session")
def make_gateway():
    """Factory: ``make_gateway(adapter, max_attempts=3, sleeps=None, run_budget=None)``.

    A real ``LLMGateway`` with a real, private ``CostTracker``, *adapter* under every provider
    key, sleeps recorded into *sleeps*, no rate limiter and an inactive budget unless given.
    """
    return _gateway


def outcome(fn: Any) -> tuple[str, Any]:
    """Runs *fn*; ``("ok", result)`` or ``("raised", exception)``, so a test asserts on it."""
    try:
        return "ok", fn()
    except Exception as exc:
        return "raised", exc


def events(trace: Any, kind: str) -> list[dict]:
    return [e for e in trace.events if e.get("kind") == kind]


# ---------------------------------------------------------------------------- frontier arm


class RefusingTransport(HttpTransport):
    """The SEC HTTP seam: no request may be sent in these tests."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.urls.append(url)
        raise AssertionError(f"no SEC request expected, got {url}")


class AgentAdapter(ScriptAdapter):
    """Tool turns: a list of ``(tool, arguments)`` becomes tool calls, a str is the reply."""

    def __init__(self, script: list[Any], *, final: str) -> None:
        super().__init__(script, final=final)
        self._ids = 0

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        calls: list[ToolCall] = []
        if isinstance(raw, list):
            for name, arguments in raw:
                self._ids += 1
                calls.append(ToolCall(f"call-{self._ids}", name, arguments))
        text = raw if isinstance(raw, str) else ""
        message = {"role": "assistant", "content": text,
                   "tool_calls": [{"id": c.call_id, "name": c.name, "arguments": c.arguments}
                                  for c in calls]}
        return ParsedResponse(content=text, finish_reason="stop", prompt_tokens=1000,
                              completion_tokens=500, tool_calls=calls,
                              assistant_message=message)


AS_OF = "2025-06-30"


def answer_json(value: Any = 120) -> str:
    return json.dumps({"value": value, "unit": "USD", "rows": [], "refused": False,
                       "hedged": False, "cited_chunks": [], "statements": [],
                       "text": f"Revenue was {value}."})


@pytest.fixture(scope="session")
def make_arm(tmp_path_factory):
    """Factory: ``make_arm(script, final=answer_json(), sleeps=None) -> (arm, adapter, sec)``.

    The real arm over a real gateway (``max_attempts=3``) and a real toolbox whose transport
    refuses every request.
    """

    def make(script: list[Any], *, final: str | None = None,
             sleeps: list[float] | None = None):
        adapter = AgentAdapter(script, final=final if final is not None else answer_json())
        sec = RefusingTransport()
        snapshot = SecSnapshot(Path(tmp_path_factory.mktemp("snapshot")), as_of=AS_OF)
        client = SecApiClient(snapshot, transport=sec, identity="Spec Tester spec@example.com",
                              sleep=lambda _s: None)
        toolbox = SecToolbox(client, as_of=AS_OF, withheld_ciks=())
        gateway = _gateway(adapter, max_attempts=3, sleeps=sleeps,
                           stage="frontier_sec_tools")
        arm = FrontierSecToolsSystem(
            gateway=gateway, toolbox=toolbox,
            universes={"covered": [{"ticker": "AAA", "cik": "0000000001"}],
                       "groups": {}, "as_of": AS_OF},
        )
        return arm, adapter, sec

    return make


@pytest.fixture(scope="session")
def make_case():
    def make() -> EvalCase:
        return EvalCase(id="case-1", question="What was Alpha Holdings' revenue in fiscal 2023?",
                        tags=["split:dev"])
    return make


@pytest.fixture(scope="session")
def reply():
    return answer_json


@pytest.fixture(scope="session")
def helpers():
    """``flex(**kw)`` (a Flex 429), ``outcome(fn)``, ``events(trace, kind)``, the models."""
    class _Helpers:
        pass
    _Helpers.flex = staticmethod(flex)
    _Helpers.outcome = staticmethod(outcome)
    _Helpers.events = staticmethod(events)
    _Helpers.openai_model = OPENAI_MODEL
    _Helpers.claude_model = CLAUDE_MODEL
    _Helpers.messages = MESSAGES
    return _Helpers
