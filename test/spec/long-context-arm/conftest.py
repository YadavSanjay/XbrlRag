"""Fakes and builders for the long-context-arm spec tests (docs/specs/long-context-arm.md).

The fakes sit at the I/O boundaries the spec names and nowhere else: a provider adapter
behind a real ``LLMGateway``, a ``FilingResolver`` subclass and an in-memory
``FilingStore`` subclass. The arm, ``filing_text``, the gateway, the prompt registry and
the answer parser are all real.

Fixtures are session-scoped factories so hypothesis-driven tests can use them without
tripping the function-scoped-fixture health check. Everything is synthetic.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.systems.base import FilingResolver
from src.eval.systems.long_context import LongContextSystem
from src.ingest.sec_edgar.storage import FilingStore
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

# Every provider key a model could resolve to, so the tests do not depend on which
# provider the arm's model names.
# deepseek-provider: the deepseek models are priced, so C6's "any priced model" property
# samples them, and they resolve to provider "deepseek".
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")

IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XHTML_NS = "http://www.w3.org/1999/xhtml"
XBRLI_NS = "http://www.xbrl.org/2003/instance"

DEFAULT_KEY = "AAA/2024/10-K/canonical.htm"


# ----------------------------------------------------------------------------------
# Audit sink: no trace opened by these tests may be persisted anywhere.
# ----------------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


# Endpoints a lazily built backend would read. Pointing them at a closed local port
# means a constructor that reached out would fail fast instead of finding a real service.
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


# ----------------------------------------------------------------------------------
# Hand-built canonical XHTML.
# ----------------------------------------------------------------------------------


def _xhtml(body: str, head: str = "") -> str:
    return (
        f'<html xmlns="{XHTML_NS}" xmlns:ix="{IX_NS}" xmlns:xbrli="{XBRLI_NS}">'
        f"<head>{head}</head><body>{body}</body></html>"
    )


def _ix_header(hidden_text: str) -> str:
    """An inline-XBRL header (hidden by its wrapper, as in a filing) holding *hidden_text*."""
    return (
        '<div style="display:none"><ix:header><ix:hidden>'
        f'<ix:nonNumeric name="dei:DocumentType" contextRef="c1">{hidden_text}</ix:nonNumeric>'
        f"</ix:hidden><ix:resources>"
        f'<xbrli:context id="c1"><xbrli:entity>{hidden_text}</xbrli:entity></xbrli:context>'
        f"</ix:resources></ix:header></div>"
    )


@pytest.fixture(scope="session")
def xhtml():
    """Factory: wraps a body (and optional head) in a canonical-style XHTML document."""
    return _xhtml


@pytest.fixture(scope="session")
def ix_header():
    """Factory: an ``ix:header`` block holding the given hidden text."""
    return _ix_header


# ----------------------------------------------------------------------------------
# Provider adapter fake (the LLM provider boundary).
# ----------------------------------------------------------------------------------


class FakeAdapter(ProviderAdapter):
    """Records each request and answers with a fixed reply and token counts."""

    def __init__(
        self,
        reply: str,
        *,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
        error: BaseException | None = None,
    ) -> None:
        self.provider = "fake"
        self.reply = reply
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.error = error
        self.params: list[dict] = []
        self.stages: list[str] = []
        self.calls = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.calls += 1
        self.params.append(params)
        if self.error is not None:
            raise self.error
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        self.stages.append(stage)
        return ParsedResponse(
            content=self.reply,
            finish_reason="stop",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            reasoning_tokens=0,
            cached_prompt_tokens=0,
        )

    # -- views over what was sent ---------------------------------------------------
    def messages(self, call: int = 0) -> list[dict]:
        return list(self.params[call]["messages"])

    def _role_text(self, role: str, call: int) -> str:
        return "\n".join(
            str(m.get("content", "")) for m in self.messages(call) if m.get("role") == role
        )

    def user_text(self, call: int = 0) -> str:
        return self._role_text("user", call)

    def system_text(self, call: int = 0) -> str:
        return self._role_text("system", call)


class OverlapAdapter(FakeAdapter):
    """Records how many calls are in flight, per filing and in total (C4).

    Each call is attributed to a filing by the first of *markers* found in its user
    message. While in flight, a call either waits on *barrier* (which raises
    ``BrokenBarrierError`` after its own timeout, so a wrong implementation fails rather
    than hangs) or, without a barrier, waits up to *overlap_wait_s* for a second call to
    be in flight with it and then returns normally. The wait only opens a window in
    which overlapping calls are seen; it never fails a call.
    """

    def __init__(
        self,
        reply: str,
        markers: tuple[str, ...],
        *,
        barrier: threading.Barrier | None = None,
        overlap_wait_s: float = 1.0,
    ) -> None:
        super().__init__(reply)
        self.markers = markers
        self.barrier = barrier
        self.overlap_wait_s = overlap_wait_s
        self.passed_barrier = 0
        self.in_flight: dict[str, int] = {m: 0 for m in markers}
        self.max_per_filing: dict[str, int] = {m: 0 for m in markers}
        self.max_total = 0
        self._cv = threading.Condition()

    def _filing(self, params: dict) -> str:
        user = "\n".join(
            str(m.get("content", "")) for m in params["messages"] if m.get("role") == "user"
        )
        for marker in self.markers:
            if marker in user:
                return marker
        return "<unknown>"

    def invoke(self, client: Any, params: dict) -> Any:
        filing = self._filing(params)
        with self._cv:
            self.calls += 1
            self.params.append(params)
            self.in_flight[filing] = self.in_flight.get(filing, 0) + 1
            self.max_per_filing[filing] = max(
                self.max_per_filing.get(filing, 0), self.in_flight[filing]
            )
            self.max_total = max(self.max_total, sum(self.in_flight.values()))
            self._cv.notify_all()
        try:
            if self.barrier is not None:
                self.barrier.wait()
                with self._cv:
                    self.passed_barrier += 1
            else:
                with self._cv:
                    self._cv.wait_for(
                        lambda: sum(self.in_flight.values()) >= 2, timeout=self.overlap_wait_s
                    )
        finally:
            with self._cv:
                self.in_flight[filing] -= 1
        return "raw"


@pytest.fixture(scope="session")
def overlap_adapter():
    """Factory: an ``OverlapAdapter`` (reply, markers, barrier=, overlap_wait_s=)."""
    return OverlapAdapter


@pytest.fixture(scope="session")
def make_gateway():
    """Factory: (real LLMGateway, FakeAdapter) with the adapter under every provider key."""

    def make(
        reply: str = "{}",
        *,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
        error: BaseException | None = None,
        run_budget: RunBudget | None = None,
        adapter: FakeAdapter | None = None,
    ) -> tuple[LLMGateway, FakeAdapter]:
        if adapter is None:
            adapter = FakeAdapter(
                reply,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=error,
            )
        gateway = LLMGateway(
            stage="long_context",
            cost_tracker=CostTracker(),
            rate_limiter=None,
            run_budget=run_budget if run_budget is not None else RunBudget(),
            max_attempts=1,
            sleep_fn=lambda _s: None,
            adapters={p: adapter for p in PROVIDERS},
        )
        return gateway, adapter

    return make


# ----------------------------------------------------------------------------------
# FilingResolver fake (the filing-registry boundary).
# ----------------------------------------------------------------------------------


class FakeResolver(FilingResolver):
    """Maps (ticker, fiscal year) to a key; records every call."""

    def __init__(
        self, keys: dict[tuple[str, int], str] | None = None, error: BaseException | None = None
    ) -> None:
        self.keys = dict(keys or {})
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def canonical_key(self, ticker: str, fiscal_year: int) -> str | None:
        self.calls.append((ticker, fiscal_year))
        if self.error is not None:
            raise self.error
        return self.keys.get((ticker, fiscal_year))


# ----------------------------------------------------------------------------------
# FilingStore fake (the local-FS / S3 boundary).
# ----------------------------------------------------------------------------------


class MemoryStore(FilingStore):
    """An in-memory filing store; a missing key raises FileNotFoundError like the local one."""

    def __init__(self, objects: dict[str, str] | None = None,
                 error: BaseException | None = None) -> None:
        self.objects = {k: v.encode("utf-8") for k, v in (objects or {}).items()}
        self.error = error
        self.reads: list[str] = []

    def write_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = bytes(data)

    def read_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        if self.error is not None:
            raise self.error
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(key).decode(encoding)

    def exists(self, key: str) -> bool:
        if self.error is not None:
            raise self.error
        return key in self.objects

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        return iter(())

    def localize_dir(self, prefix: str):
        raise RuntimeError("the in-memory store has no local directories")


# ----------------------------------------------------------------------------------
# Cases, replies and the assembled arm.
# ----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def make_case():
    """Factory: a scalar lookup case for one filer and year."""

    def make(
        question: str = "What was AAA total revenue in fiscal 2024?",
        *,
        ticker: str | None = "AAA",
        fy: int | None = 2024,
        case_id: str = "c1",
        kind: str = "lookup",
    ) -> EvalCase:
        return EvalCase(
            id=case_id,
            question=question,
            kind=kind,
            expected_value=391035000000.0,
            ticker=ticker,
            fy=fy,
        )

    return make


@pytest.fixture(scope="session")
def reply():
    """Factory: the arm-neutral JSON answer object, as reply text."""

    def make(
        value: Any = 391035000000,
        *,
        rows: list[dict] | None = None,
        refused: bool = False,
        hedged: bool = False,
        cited_chunks: list[str] | None = None,
        unit: str = "USD",
    ) -> str:
        return json.dumps(
            {
                "value": value,
                "unit": unit,
                "rows": list(rows or []),
                "refused": refused,
                "hedged": hedged,
                "cited_chunks": list(cited_chunks or []),
            }
        )

    return make


@pytest.fixture(scope="session")
def make_arm(make_gateway, reply):
    """Factory: (arm, adapter, resolver, store) wired to the fakes.

    By default the resolver maps ("AAA", 2024) to ``DEFAULT_KEY`` and the store holds
    *html* there.
    """

    def make(
        html: str = _xhtml("<p>Total net sales were 391,035 million.</p>"),
        *,
        reply_text: str | None = None,
        keys: dict[tuple[str, int], str] | None = None,
        objects: dict[str, str] | None = None,
        resolver_error: BaseException | None = None,
        store_error: BaseException | None = None,
        provider_error: BaseException | None = None,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
        run_budget: RunBudget | None = None,
        max_input_tokens: int | None = None,
        model: str | None = None,
        adapter: FakeAdapter | None = None,
    ):
        gateway, adapter = make_gateway(
            reply_text if reply_text is not None else reply(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error=provider_error,
            run_budget=run_budget,
            adapter=adapter,
        )
        resolver = FakeResolver(
            keys if keys is not None else {("AAA", 2024): DEFAULT_KEY}, error=resolver_error
        )
        store = MemoryStore(
            objects if objects is not None else {DEFAULT_KEY: html}, error=store_error
        )
        kwargs: dict[str, Any] = {}
        if max_input_tokens is not None:
            kwargs["max_input_tokens"] = max_input_tokens
        if model is not None:
            kwargs["model"] = model
        arm = LongContextSystem(gateway=gateway, resolver=resolver, store=store, **kwargs)
        return arm, adapter, resolver, store

    return make
