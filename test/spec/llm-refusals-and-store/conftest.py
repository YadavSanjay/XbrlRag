"""Fakes and builders for the llm-refusals-and-store spec tests.

Spec: docs/specs/llm-refusals-and-store.md. The fakes sit only at the I/O boundaries the
spec names:

- fake raw Anthropic replies for ``AnthropicAdapter.parse`` (``SimpleNamespace`` objects,
  copied from test/spec/llm-anthropic-and-caching/conftest.py);
- ``RefusingAdapter``: a fake provider adapter behind a real ``LLMGateway`` (with a real
  ``CostTracker``) that raises a configured refusal;
- ``FakeReader``: the SQL row source ``metrics_exporter`` reads views through;
- the committed DDL and pricing files, read as text / YAML.

Fixtures are session-scoped factories so hypothesis tests can use them. Everything is
synthetic; no network, no data/ and no real keys.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import LLMRefusalError, ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]

# Every provider key a model id could resolve to, so a test does not depend on which one
# the gateway infers.
PROVIDERS = ("openai", "openai_compat", "anthropic", "huggingface")


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


# ---------------------------------------------------------------------------- committed files


@pytest.fixture(scope="session")
def pricing_row():
    """Factory: a model's committed row in configs/llm/pricing.yaml (USD per 1,000 tokens)."""
    doc = yaml.safe_load((REPO_ROOT / "configs" / "llm" / "pricing.yaml").read_text(
        encoding="utf-8"))

    def row(model: str) -> dict[str, Any]:
        return dict(doc[model])

    return row


@pytest.fixture(scope="session")
def analyst_views_sql() -> str:
    """The committed scripts/ddl/audit/analyst_views.sql, as text."""
    return (REPO_ROOT / "scripts" / "ddl" / "audit" / "analyst_views.sql").read_text(
        encoding="utf-8")


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
    content = [SimpleNamespace(type=kind, text=text) for kind, text in blocks]
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


@pytest.fixture(scope="session")
def anthropic_reply():
    """Factory: a fake Anthropic Messages reply."""
    return _anthropic_reply


# ---------------------------------------------------------------------------- refusals


def _refusal(
    *,
    category: str | None = "bio",
    billed: bool = True,
    prompt_tokens: int | None = 100_000,
    cached_prompt_tokens: int = 0,
    cache_write_tokens: int = 0,
    completion_tokens: int = 0,
) -> LLMRefusalError:
    """A refusal as the spec's adapter would raise it (requirement 2).

    Built with the message-only constructor that exists today, then given the three new
    attributes, so the fake does not depend on the implementer's constructor. With
    ``prompt_tokens=None`` the refusal carries no usage.
    """
    exc = LLMRefusalError(f"the model declined to answer (refusal category: {category})")
    usage = None
    if prompt_tokens is not None:
        usage = ParsedResponse(
            content="",
            finish_reason="refusal",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            cache_write_tokens=cache_write_tokens,
        )
    exc.category = category  # type: ignore[attr-defined]  # the attribute requirement 2 adds
    exc.usage = usage  # type: ignore[attr-defined]  # the attribute requirement 2 adds
    exc.billed = billed  # type: ignore[attr-defined]  # the attribute requirement 2 adds
    return exc


@pytest.fixture(scope="session")
def make_refusal():
    """Factory: ``make_refusal(category=, billed=, prompt_tokens=, cached_prompt_tokens=,
    cache_write_tokens=, completion_tokens=) -> LLMRefusalError``."""
    return _refusal


class RefusingAdapter(ProviderAdapter):
    """A fake adapter whose every ``parse`` raises the configured refusal."""

    def __init__(self, refusal: BaseException) -> None:
        self.provider = "fake"
        self.refusal = refusal
        self.invoked = 0
        self.parsed = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return dict(kwargs)

    def invoke(self, client: Any, params: dict) -> Any:
        self.invoked += 1
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        self.parsed += 1
        raise self.refusal


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
def refusing_adapter():
    """Factory: ``refusing_adapter(refusal) -> RefusingAdapter``."""
    return RefusingAdapter


@pytest.fixture(scope="session")
def make_gateway():
    """Factory: ``make_gateway(adapter, max_attempts=3, sleeps=None) -> LLMGateway``.

    A real ``LLMGateway`` with a real, private ``CostTracker``, *adapter* under every
    provider key, a no-op sleep (recorded into *sleeps* when given), no rate limiter and
    an inactive run budget.
    """
    return _gateway


# ---------------------------------------------------------------------------- metrics reader


class FakeReader:
    """The exporter's SQL row source: returns configured rows per view, else ``[]``.

    The DB probe (``SELECT 1``) answers one row, so the store looks up.
    """

    def __init__(self, views: dict[str, list[dict[str, Any]]]) -> None:
        self.views = {k: [dict(r) for r in v] for k, v in views.items()}
        self.queries: list[str] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.queries.append(sql)
        if sql.strip().upper().startswith("SELECT 1"):
            return [{"up": 1}]
        for view, rows in self.views.items():
            if f"audit.{view}" in sql and f"audit.{view}_" not in sql:
                return [dict(r) for r in rows]
        return []


@pytest.fixture(scope="session")
def fake_reader():
    """Factory: ``fake_reader({"v_llm_calls": [rows], ...}) -> FakeReader``."""
    return FakeReader
