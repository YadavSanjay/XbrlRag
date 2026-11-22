"""Fakes and builders for the baseline-arms spec tests (docs/specs/baseline-arms.md).

The fakes sit at the I/O boundaries the spec names and nowhere else: a provider adapter
behind a real ``LLMGateway``, a query embedder and a Qdrant client. The arm code, the
gateway, the prompt registry and the answer parser are all real.

Fixtures are session-scoped factories so hypothesis-driven tests can use them without
tripping the function-scoped-fixture health check. Everything is synthetic.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.embeddings.base import BaseEmbeddingGenerator, DenseEmbedding, SparseEmbedding
from src.eval.core.case import EvalCase
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

# Every provider key a stage's model could resolve to, so the tests do not depend on
# which provider the medium tier names.
PROVIDERS = ("openai", "openai_compat", "anthropic", "huggingface")


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

    def all_text(self, call: int = 0) -> str:
        return "\n".join(str(m.get("content", "")) for m in self.messages(call))

    def user_text(self, call: int = 0) -> str:
        return "\n".join(
            str(m.get("content", "")) for m in self.messages(call) if m.get("role") == "user"
        )


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
        stage: str = "llm",
    ) -> tuple[LLMGateway, FakeAdapter]:
        adapter = FakeAdapter(
            reply,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error=error,
        )
        gateway = LLMGateway(
            stage=stage,
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
# Embedder fake (the BGE-M3 / RAGINFER boundary).
# ----------------------------------------------------------------------------------


class FakeEmbedder(BaseEmbeddingGenerator):
    """Returns a fixed dense+sparse pair for every text; records what it embedded."""

    model_name = "fake-bge-m3"

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.embed_calls: list[list[tuple[str, str]]] = []
        self.other_calls = 0

    def dense_embed_texts(self, id_texts):
        self.other_calls += 1
        return [(i, DenseEmbedding(values=[0.1, 0.2, 0.3])) for i, _ in id_texts]

    def sparse_embed_texts(self, id_texts):
        self.other_calls += 1
        return [(i, SparseEmbedding(indices=[1, 7], values=[0.5, 0.25])) for i, _ in id_texts]

    def embed_texts(self, id_texts):
        self.embed_calls.append(list(id_texts))
        if self.error is not None:
            raise self.error
        return {
            i: (
                DenseEmbedding(values=[0.1, 0.2, 0.3]),
                SparseEmbedding(indices=[1, 7], values=[0.5, 0.25]),
            )
            for i, _ in id_texts
        }


@pytest.fixture(scope="session")
def make_embedder():
    def make(error: BaseException | None = None) -> FakeEmbedder:
        return FakeEmbedder(error=error)

    return make


# ----------------------------------------------------------------------------------
# Qdrant client fake (the vector-DB boundary).
# ----------------------------------------------------------------------------------


def _hybrid_signature(
    collection_name=None,
    vector_d=None,
    vector_s=None,
    ciks_in_query=None,
    fiscal_years_in_query=None,
    section_path_prefixes=None,
    contains_table=None,
    limit=15,
    tickers_in_query=None,
):
    """The parameters of XbrlQdrantVectorDBClient.hybrid_query_text (after this change)."""


_HYBRID_SIG = inspect.signature(_hybrid_signature)


class FakeQdrant:
    """Records each ``hybrid_query_text`` call (with defaults applied) and returns points."""

    def __init__(self, points: list[Any], error: BaseException | None = None) -> None:
        self.points = list(points)
        self.error = error
        self.calls: list[dict] = []

    def hybrid_query_text(self, *args: Any, **kwargs: Any) -> Any:
        bound = _HYBRID_SIG.bind(*args, **kwargs)
        bound.apply_defaults()
        self.calls.append(dict(bound.arguments))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(points=list(self.points))


def make_point(chunk_id: str, text: str, *, score: float = 0.5, ticker: str = "AAA",
               fiscal_year: int = 2024) -> Any:
    """A Qdrant-like scored point whose id is the chunk id."""
    return SimpleNamespace(
        id=chunk_id,
        score=score,
        payload={
            "chunk_id": chunk_id,
            "text": text,
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "section_path": "Part II > Item 7. MD&A",
        },
    )


@pytest.fixture(scope="session")
def make_qdrant():
    def make(points: list[Any] | None = None, error: BaseException | None = None) -> FakeQdrant:
        return FakeQdrant(points or [], error=error)

    return make


@pytest.fixture(scope="session")
def point():
    return make_point


# ----------------------------------------------------------------------------------
# Cases and replies.
# ----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def make_case():
    """Factory: a scalar lookup case."""

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
    """Factory: the JSON answer object of arm-neutral-grading, as reply text."""

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
