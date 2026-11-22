"""Fakes and builders for the llm-anthropic-and-caching spec tests.

Spec: docs/specs/llm-anthropic-and-caching.md. The fakes sit only at the I/O boundaries the
spec names:

- ``FakeClient``: a fake SDK client object passed to ``adapter.invoke``. Its
  ``messages.stream``, ``chat.completions.create`` and ``responses.create`` record their
  kwargs and return a configured reply; ``messages.create`` (and any endpoint without a
  configured reply) raises ``AssertionError`` so a wrong route fails as an assertion.
- Fake raw replies for ``parse``, built as ``SimpleNamespace`` objects (``anthropic_reply``,
  ``responses_reply``).
- ``RecordingAdapter``: a fake provider adapter behind a real ``LLMGateway`` (C1, C4).
- ``ClientOpenAIAdapter``: the real ``OpenAIAdapter`` whose ``get_client`` (the SDK-client
  construction boundary) returns a ``FakeClient``, so the gateway drives the real
  ``build_params``/``invoke``/``parse`` (C6).
- The long-context arm's fakes (a provider adapter, ``FakeResolver``, ``MemoryStore``),
  copied from test/spec/long-context-arm/conftest.py (spec tests cannot import another
  spec's conftest).

Fixtures are session-scoped factories so hypothesis tests can use them. Everything is
synthetic; no network, no data/ and no real keys.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.eval.core.case import EvalCase
from src.eval.systems.base import FilingResolver
from src.eval.systems.long_context import LongContextSystem
from src.ingest.sec_edgar.storage import FilingStore
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.gateway.adapters.openai_adapter import OpenAIAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]

# Every provider key a model id could resolve to, so a test does not depend on which one
# the gateway infers.
PROVIDERS = ("openai", "openai_compat", "anthropic", "huggingface")

XHTML_NS = "http://www.w3.org/1999/xhtml"
IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI_NS = "http://www.xbrl.org/2003/instance"
DEFAULT_KEY = "AAA/2024/10-K/canonical.htm"


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


# ---------------------------------------------------------------------------- environment

_LLM_ENV = (
    "XBRLRAG_LLM_PROFILE",
    "XBRLRAG_USE_REPLACEMENT_MODELS",
    "ARAG_LLM_PROFILE",
    "ARAG_USE_REPLACEMENT_MODELS",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_FILE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY_FILE",
    "OPENAI_BASE_URL",
    "OPENAI_COMPAT_API_KEY",
    "XBRLRAG_RUN_MAX_COST_USD",
    "XBRLRAG_RUN_MAX_LLM_CALLS",
    "XBRLRAG_RUN_MAX_TOKENS",
    "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES",
)


@pytest.fixture
def llm_env(monkeypatch):
    """Removes every profile, key and budget variable; returns ``monkeypatch``."""
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# Endpoints a lazily built backend would read, pointed at a closed local port.
_DEAD_ENDPOINTS = {
    "QDRANT_HOST": "127.0.0.1",
    "QDRANT_PORT": "9",
    "QDRANT_HTTPS": "false",
    "RAGINFER_URL": "http://127.0.0.1:9",
    "RAGINFER_FALLBACK_LOCAL": "false",
    "XBRL_DB_HOST": "127.0.0.1",
    "XBRL_DB_PORT": "9",
    "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
    "OPENAI_API_KEY": "sk-spec-offline",
    "ANTHROPIC_API_KEY": "sk-ant-spec-offline",
}


@pytest.fixture
def offline_env(monkeypatch):
    """Points every backend endpoint at a closed local port and uses dummy API keys."""
    for key, value in _DEAD_ENDPOINTS.items():
        monkeypatch.setenv(key, value)
    yield


# ---------------------------------------------------------------------------- committed config


@pytest.fixture(scope="session")
def committed_models() -> dict[str, dict[str, Any]]:
    """The ``models`` section of the committed configs/llm/llm.yaml (llm-provider-profiles)."""
    doc = yaml.safe_load((REPO_ROOT / "configs" / "llm" / "llm.yaml").read_text(encoding="utf-8"))
    return copy.deepcopy(doc["models"])


@pytest.fixture(scope="session")
def committed_claude_models(committed_models) -> dict[str, dict[str, Any]]:
    """The committed models whose provider is ``anthropic``."""
    return {m: cfg for m, cfg in committed_models.items() if cfg.get("provider") == "anthropic"}


# ---------------------------------------------------------------------------- fake SDK client


class _Endpoint:
    """One SDK method: records each call's kwargs and returns the configured reply."""

    def __init__(self, name: str, reply: Any = None) -> None:
        self.name = name
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self.reply is None:
            raise AssertionError(f"unexpected SDK call: {self.name}")
        return self.reply


class _StreamManager:
    """What ``messages.stream(...)`` returns: a context manager over the final message.

    ``get_final_message`` works on the manager and on what ``__enter__`` returns, so the
    fake does not dictate whether the adapter uses ``with``.
    """

    def __init__(self, owner: _Stream) -> None:
        self._owner = owner

    def __enter__(self) -> _StreamManager:
        self._owner.entered += 1
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._owner.exited += 1
        return False

    def get_final_message(self) -> Any:
        self._owner.final_calls += 1
        return self._owner.reply


class _Stream:
    """``client.messages.stream``: records kwargs, returns a ``_StreamManager``."""

    def __init__(self, reply: Any = None) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []
        self.entered = 0
        self.exited = 0
        self.final_calls = 0

    def __call__(self, **kwargs: Any) -> _StreamManager:
        self.calls.append(dict(kwargs))
        if self.reply is None:
            raise AssertionError("unexpected SDK call: messages.stream")
        return _StreamManager(self)


class FakeClient:
    """A fake provider SDK client exposing the endpoints the adapters may call."""

    def __init__(
        self,
        *,
        stream_reply: Any = None,
        chat_reply: Any = None,
        responses_reply: Any = None,
        max_retries: int | None = None,
    ) -> None:
        self.stream = _Stream(stream_reply)
        self.create = _Endpoint("messages.create")  # never configured: must not be called
        self.messages = SimpleNamespace(stream=self.stream, create=self.create)
        self.chat_create = _Endpoint("chat.completions.create", chat_reply)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.chat_create))
        self.responses_create = _Endpoint("responses.create", responses_reply)
        self.responses = SimpleNamespace(create=self.responses_create)
        self.max_retries = max_retries


@pytest.fixture(scope="session")
def fake_client():
    """Factory: ``fake_client(stream_reply=, chat_reply=, responses_reply=)``."""
    return FakeClient


# ---------------------------------------------------------------------------- fake raw replies


def _anthropic_reply(
    blocks: list[tuple[str, str]],
    *,
    stop_reason: str = "end_turn",
    stop_details: Any = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> SimpleNamespace:
    """An Anthropic Messages reply; *blocks* are ``(type, text)`` pairs."""
    content = []
    for kind, text in blocks:
        if kind == "thinking":
            content.append(SimpleNamespace(type="thinking", thinking=text, signature="sig"))
        else:
            content.append(SimpleNamespace(type=kind, text=text))
    return SimpleNamespace(
        id="msg_spec",
        type="message",
        role="assistant",
        model="claude-opus-5-5",
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


_ABSENT = object()


def _responses_reply(
    output_text: str,
    *,
    status: str = "completed",
    incomplete_reason: str | None = None,
    input_tokens: int = 0,
    cached_tokens: Any = _ABSENT,
    cache_write_tokens: Any = _ABSENT,
    output_tokens: int = 0,
    reasoning_tokens: Any = _ABSENT,
    details: str = "present",
) -> SimpleNamespace:
    """An OpenAI Responses reply.

    *details* is ``"present"`` (the detail objects exist, with only the given fields),
    ``"none"`` (they are ``None``) or ``"absent"`` (the attributes do not exist).
    """
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                            total_tokens=input_tokens + output_tokens)
    if details == "present":
        in_details = SimpleNamespace()
        if cached_tokens is not _ABSENT:
            in_details.cached_tokens = cached_tokens
        if cache_write_tokens is not _ABSENT:
            in_details.cache_write_tokens = cache_write_tokens
        out_details = SimpleNamespace()
        if reasoning_tokens is not _ABSENT:
            out_details.reasoning_tokens = reasoning_tokens
        usage.input_tokens_details = in_details
        usage.output_tokens_details = out_details
    elif details == "none":
        usage.input_tokens_details = None
        usage.output_tokens_details = None
    incomplete = (
        SimpleNamespace(reason=incomplete_reason) if incomplete_reason is not None else None
    )
    return SimpleNamespace(
        id="resp_spec",
        object="response",
        model="gpt-5.6-sol",
        output_text=output_text,
        status=status,
        incomplete_details=incomplete,
        usage=usage,
    )


@pytest.fixture(scope="session")
def anthropic_reply():
    """Factory: a fake Anthropic Messages reply."""
    return _anthropic_reply


@pytest.fixture(scope="session")
def responses_reply():
    """Factory: a fake OpenAI Responses reply."""
    return _responses_reply


# ---------------------------------------------------------------------------- gateway fakes


class RecordingAdapter(ProviderAdapter):
    """A fake adapter: records ``build_params`` kwargs; raises or answers per attempt.

    *errors* is consumed one per ``parse`` call; ``None`` entries (or running out) answer
    with a fixed reply.
    """

    def __init__(self, *, errors: list[BaseException | None] | None = None,
                 prompt_tokens: int = 100, completion_tokens: int = 10) -> None:
        self.provider = "fake"
        self.errors = list(errors or [])
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.built: list[dict[str, Any]] = []
        self.invoked = 0
        self.parsed = 0
        self.client_requests = 0

    def get_client(self) -> Any:
        self.client_requests += 1
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        self.built.append(kwargs)
        return dict(kwargs)

    def invoke(self, client: Any, params: dict) -> Any:
        self.invoked += 1
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        index = self.parsed
        self.parsed += 1
        if index < len(self.errors) and self.errors[index] is not None:
            raise self.errors[index]
        return ParsedResponse(
            content='{"ok": true}',
            finish_reason="stop",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )


def _gateway(adapter: ProviderAdapter, *, max_attempts: int = 3,
             sleeps: list[float] | None = None) -> LLMGateway:
    sink = sleeps if sleeps is not None else []
    return LLMGateway(
        stage="spec",
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=RunBudget(),
        max_attempts=max_attempts,
        sleep_fn=sink.append,
        adapters={p: adapter for p in PROVIDERS},
    )


@pytest.fixture(scope="session")
def recording_adapter():
    """Factory: a ``RecordingAdapter``."""
    return RecordingAdapter


@pytest.fixture(scope="session")
def make_gateway():
    """Factory: ``make_gateway(adapter, max_attempts=3, sleeps=None) -> LLMGateway``.

    A real ``LLMGateway`` with *adapter* under every provider key, a no-op sleep (recorded
    into *sleeps* when given), no rate limiter and an inactive run budget.
    """
    return _gateway


class ClientOpenAIAdapter(OpenAIAdapter):
    """The real ``OpenAIAdapter``; only SDK-client construction returns the fake client."""

    def __init__(self, client: FakeClient) -> None:
        super().__init__(api_key="sk-spec-offline")
        self.fake = client

    def get_client(self) -> Any:
        return self.fake


@pytest.fixture(scope="session")
def client_openai_adapter():
    """Factory: ``client_openai_adapter(fake_client) -> OpenAIAdapter`` using that client."""
    return ClientOpenAIAdapter


# ---------------------------------------------------------------------------- long-context arm


class ArmAdapter(ProviderAdapter):
    """Records each request's params and answers with a fixed reply (long-context arm)."""

    def __init__(self, reply: str) -> None:
        self.provider = "fake"
        self.reply = reply
        self.params: list[dict] = []
        self.calls = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.calls += 1
        self.params.append(params)
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(
            content=self.reply,
            finish_reason="stop",
            prompt_tokens=1000,
            completion_tokens=50,
        )

    def messages(self, call: int = 0) -> list[dict]:
        return list(self.params[call]["messages"])


class FakeResolver(FilingResolver):
    """Maps (ticker, fiscal year) to a key."""

    def __init__(self, keys: dict[tuple[str, int], str]) -> None:
        self.keys = dict(keys)
        self.calls: list[tuple[str, int]] = []

    def canonical_key(self, ticker: str, fiscal_year: int) -> str | None:
        self.calls.append((ticker, fiscal_year))
        return self.keys.get((ticker, fiscal_year))


class MemoryStore(FilingStore):
    """An in-memory filing store; a missing key raises FileNotFoundError like the local one."""

    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = {k: v.encode("utf-8") for k, v in objects.items()}
        self.reads: list[str] = []

    def write_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = bytes(data)

    def read_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(key).decode(encoding)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        return iter(())

    def localize_dir(self, prefix: str):
        raise RuntimeError("the in-memory store has no local directories")


def _xhtml(body: str) -> str:
    return (
        f'<html xmlns="{XHTML_NS}" xmlns:ix="{IX_NS}" xmlns:xbrli="{XBRLI_NS}">'
        f"<head></head><body>{body}</body></html>"
    )


@pytest.fixture(scope="session")
def xhtml():
    """Factory: wraps a body in a canonical-style XHTML document."""
    return _xhtml


@pytest.fixture(scope="session")
def make_case():
    """Factory: a scalar lookup case for one filer and year."""

    def make(question: str, *, case_id: str = "c1", ticker: str = "AAA",
             fy: int = 2024) -> EvalCase:
        return EvalCase(id=case_id, question=question, kind="lookup",
                        expected_value=1.0, ticker=ticker, fy=fy)

    return make


@pytest.fixture(scope="session")
def make_arm():
    """Factory: ``make_arm(html) -> (arm, adapter)``; ("AAA", 2024) resolves to *html*."""

    def make(html: str, *, model: str | None = None):
        adapter = ArmAdapter(
            '{"value": 1, "unit": "USD", "rows": [], "refused": false, "hedged": false, '
            '"cited_chunks": []}'
        )
        gateway = LLMGateway(
            stage="long_context",
            cost_tracker=CostTracker(),
            rate_limiter=None,
            run_budget=RunBudget(),
            max_attempts=1,
            sleep_fn=lambda _s: None,
            adapters={p: adapter for p in PROVIDERS},
        )
        kwargs: dict[str, Any] = {}
        if model is not None:
            kwargs["model"] = model
        arm = LongContextSystem(
            gateway=gateway,
            resolver=FakeResolver({("AAA", 2024): DEFAULT_KEY}),
            store=MemoryStore({DEFAULT_KEY: html}),
            **kwargs,
        )
        return arm, adapter

    return make
