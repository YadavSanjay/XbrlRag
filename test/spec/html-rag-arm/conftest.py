"""Fakes and builders for the html-rag-arm spec tests (docs/specs/html-rag-arm.md).

The fakes sit at the I/O boundaries and nowhere else: a provider adapter behind a real
``LLMGateway`` (one for the router, one for the answer), an ``EntityExtractor`` whose connection
is fake, a fake Qdrant client, a fake embedder, a fake reranker, an in-memory ``FilingStore`` and a
fake database connection. The arm, ``IntentRouter``, the extractor's logic, the calculator, the
prompt registry, the retriever, the score cache and the config loader are all real.

The fixtures are session-scoped factories, so hypothesis-driven tests can use them without tripping
the function-scoped-fixture health check, and so that a stub is only ever reached inside a test's
call. AAA, BBB and CCC are synthetic.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from qdrant_client import models

from src.embeddings.base import DenseEmbedding, SparseEmbedding
from src.eval.core.case import EvalCase
from src.eval.systems.html_rag import HtmlRagSystem
from src.ingest.sec_edgar.storage import FilingStore
from src.llm.config import MODEL_PRICING
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink
from src.retriever.html10k.config import load_html10k_config
from src.retriever.html10k.rerank_cache import CachedReranker
from src.retriever.html10k.retriever import Html10kRetriever
from src.retriever.xbrl.entity_extractor import EntityExtractor
from src.retriever.xbrl.intent_router import IntentRouter

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
ROUTER_MODEL = "gpt-5-mini"  # priced, explicit: the tests do not depend on the active profile
COLLECTION = "html10k_text"
# html10k-chunks requirement 12.
CHUNK_NAMESPACE = uuid.UUID("5d1c3f0e-7a42-4e6b-9b3d-2c8e1f6a4d70")

AAA_CIK = "0000000001"
BBB_CIK = "0000000002"
CCC_CIK = "0000000003"

ENTITIES: dict[str, dict[str, Any]] = {
    "AAA": {"cik": AAA_CIK, "trading_symbol": "AAA", "company_name": "Alpha Holdings",
            "fye_month": 12, "fye_day": 31, "last_seen_fy": 2024},
    "BBB": {"cik": BBB_CIK, "trading_symbol": "BBB", "company_name": "Beta Industries",
            "fye_month": 12, "fye_day": 31, "last_seen_fy": 2024},
    "CCC": {"cik": CCC_CIK, "trading_symbol": "CCC", "company_name": "Gamma Systems",
            "fye_month": 6, "fye_day": 30, "last_seen_fy": 2023},
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "rrf_k": 2,
    "dense_weight": 2.0,
    "sparse_weight": 1.0,
    "prefetch_limit": 100,
    "rerank_candidates": 30,
    "context_top_k": 20,
    "reranker_model": "jinaai/jina-reranker-v3",
    "expand_neighbors": True,
    "expansion_score_threshold": 0.5,
    "expansion_max_chunks": 20,
    "max_filer_searches": 10,
    "table_max_chars": 1200,
    "embed_batch_size": 32,
}

TABLE_TEXT = "\n".join(
    [
        "Section: Item 8. Financial Statements",
        "Table: CONSOLIDATED STATEMENTS OF OPERATIONS",
        "Note: (In millions)",
        "| (In millions) | Year Ended Dec 31, 2024 | Year Ended Dec 31, 2023 |",
        "| --- | --- | --- |",
        "| Net sales | $1,200 | $1,000 |",
        "| Cost of sales | (700) | (600) |",
        "| Goodwill [a] | — | 5 |",
    ]
)
PROSE_TEXT = "Net sales grew because demand for widgets rose in every region."


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


_DEAD_ENDPOINTS = {
    "QDRANT_HOST": "127.0.0.1",
    "QDRANT_PORT": "9",
    "RAGINFER_URL": "http://127.0.0.1:9",
    "XBRL_DB_HOST": "127.0.0.1",
    "XBRL_DB_PORT": "9",
    "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
    "OPENAI_API_KEY": "sk-spec-offline",
    "ANTHROPIC_API_KEY": "sk-ant-spec-offline",
}


@pytest.fixture
def offline_env(monkeypatch):
    """Points every backend at a closed local port, with dummy API keys."""
    for key, value in _DEAD_ENDPOINTS.items():
        monkeypatch.setenv(key, value)
    yield


# ----------------------------------------------------------------------------------
# Settings.
# ----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def write_settings(tmp_path_factory):
    """Factory: writes a settings file and returns its path."""

    def write(settings: dict[str, Any]) -> Path:
        path = tmp_path_factory.mktemp("settings") / "rag.yaml"
        path.write_text(yaml.safe_dump(settings), encoding="utf-8")
        return path

    return write


@pytest.fixture(scope="session")
def make_config(write_settings, tmp_path_factory):
    """Factory: real settings, through the real loader, with a fresh cache file by default."""

    def make(**overrides: Any):
        settings = {
            **DEFAULT_SETTINGS,
            "rerank_cache_path": str(tmp_path_factory.mktemp("cache") / "rerank.sqlite3"),
            **overrides,
        }
        return load_html10k_config(write_settings(settings))

    return make


@pytest.fixture(scope="session")
def default_settings():
    """The settings of requirement 1, except the cache path."""
    return dict(DEFAULT_SETTINGS)


# ----------------------------------------------------------------------------------
# The Qdrant boundary.
# ----------------------------------------------------------------------------------


def chunk_id(accession_no: str, start: int, end: int, part: int = 0) -> str:
    """html10k-chunks requirement 12, computed here."""
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{accession_no}:{start}:{end}:{part}"))


def make_payload(
    marker: str,
    *,
    cik: str = AAA_CIK,
    ticker: str = "AAA",
    company_name: str = "Alpha Holdings",
    accession_no: str = "0000000001-25-000001",
    fiscal_year: int = 2024,
    start: int = 0,
    end: int = 100,
    part: int = 0,
    parts: int = 1,
    text: str | None = None,
    contains_table: bool = False,
    table_index: int | None = None,
    prev_chunk_id: str | None = None,
    next_chunk_id: str | None = None,
    section_key: str = "item_7",
    section_label: str = "Item 7. MD&A",
    **extra: Any,
) -> dict[str, Any]:
    """A payload of the html10k-chunks contract; the point's id is the chunk id."""
    return {
        "chunk_id": chunk_id(accession_no, start, end, part),
        "accession_no": accession_no,
        "text": text if text is not None else f"passage {marker}",
        "cik": cik,
        "ticker": ticker,
        "company_name": company_name,
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "form_type": "10-K",
        "period_end": f"{fiscal_year}-12-31",
        "section_key": section_key,
        "section_label": section_label,
        "section_keys": [section_key],
        "html_char_start": start,
        "html_char_end": end,
        "contains_table": contains_table,
        "table_index": table_index,
        "part": part,
        "parts": parts,
        "prev_chunk_id": prev_chunk_id,
        "next_chunk_id": next_chunk_id,
        **extra,
    }


def _conditions(query_filter: Any) -> dict[str, list]:
    """A filter as ``{key: allowed values}``; only what the spec's filters use."""
    if query_filter is None:
        return {}
    out: dict[str, list] = {}
    for condition in query_filter.must:
        match = condition.match
        out[condition.key] = list(getattr(match, "any", None) or [getattr(match, "value", None)])
    return out


class FakeQdrant:
    """The Qdrant client's calls this change makes, over payloads held in a list.

    Search results keep list order, as a stand-in for the server's fusion: the fusion itself is
    Qdrant's and is checked only as a request.
    """

    def __init__(
        self,
        payloads: Sequence[dict[str, Any]] = (),
        *,
        query_error: BaseException | None = None,
        retrieve_error: BaseException | None = None,
        upload_error: BaseException | None = None,
    ) -> None:
        self.payloads = list(payloads)
        self.query_error = query_error
        self.retrieve_error = retrieve_error
        self.upload_error = upload_error
        self.query_calls: list[dict[str, Any]] = []
        self.retrieve_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []
        self.uploaded: list[Any] = []
        self.collections: set[str] = set()
        self.created: list[str] = []
        self.indexes: list[str] = []
        self.touched: set[str] = set()

    def _matching(self, conditions: dict[str, list]) -> list[dict[str, Any]]:
        return [
            p for p in self.payloads
            if all(p.get(key) in allowed for key, allowed in conditions.items())
        ]

    def query_points(self, **kwargs: Any) -> Any:
        self.query_calls.append(kwargs)
        self.touched.add(kwargs["collection_name"])
        if self.query_error is not None:
            raise self.query_error
        found = self._matching(_conditions(kwargs["prefetch"][0].filter))
        return SimpleNamespace(
            points=[
                models.ScoredPoint(
                    id=p["chunk_id"], version=0, score=1.0 / (rank + 1), payload=p
                )
                for rank, p in enumerate(found[: kwargs["limit"]])
            ]
        )

    def retrieve(self, **kwargs: Any) -> list[Any]:
        self.retrieve_calls.append(kwargs)
        self.touched.add(kwargs["collection_name"])
        if self.retrieve_error is not None:
            raise self.retrieve_error
        by_id = {p["chunk_id"]: p for p in self.payloads}
        found = [by_id[i] for i in reversed(list(kwargs["ids"])) if i in by_id]
        return [models.Record(id=p["chunk_id"], payload=p) for p in found]

    def count(self, **kwargs: Any) -> Any:
        self.count_calls.append(kwargs)
        self.touched.add(kwargs["collection_name"])
        return SimpleNamespace(count=len(self._matching(_conditions(kwargs["count_filter"]))))

    def delete(self, **kwargs: Any) -> Any:
        self.delete_calls.append(kwargs)
        self.touched.add(kwargs["collection_name"])
        selector = kwargs["points_selector"]
        conditions = _conditions(getattr(selector, "filter", selector))
        gone = {p["chunk_id"] for p in self._matching(conditions)}
        self.payloads = [p for p in self.payloads if p["chunk_id"] not in gone]
        return None

    def collection_exists(self, *, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_collection(self, *, collection_name: str, **_kwargs: Any) -> None:
        self.collections.add(collection_name)
        self.created.append(collection_name)

    def create_payload_index(self, *, field_name: str, **_kwargs: Any) -> None:
        self.indexes.append(field_name)

    def upload_points(self, *, collection_name: str, points: Any, wait: bool = True) -> None:
        self.touched.add(collection_name)
        if self.upload_error is not None:
            raise self.upload_error
        points = list(points)
        self.uploaded.extend(points)
        self.payloads.extend(dict(point.payload) for point in points)


class FakeEmbedder:
    """Records every batch, and gives each text a small vector."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.batches: list[list[tuple[str, str]]] = []

    def embed_texts(self, id_texts: list[tuple[str, str]]) -> dict[str, tuple]:
        self.batches.append(list(id_texts))
        if self.error is not None:
            raise self.error
        return {
            key: (DenseEmbedding([0.25, 0.5]), SparseEmbedding([1, 7], [0.5, 0.25]))
            for key, _ in id_texts
        }


class FakeReranker:
    """Scores a document by the first marker found in it; records calls and overlap."""

    def __init__(
        self,
        scores: dict[str, float] | None = None,
        *,
        default: float = 0.25,
        error: BaseException | None = None,
        raw: list[float] | None = None,
        hold_s: float = 0.0,
    ) -> None:
        self.scores = dict(scores or {})
        self.default = default
        self.error = error
        self.raw = raw
        self.hold_s = hold_s
        self.calls: list[tuple[str, list[str]]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._cv = threading.Condition()

    def _score(self, document: str) -> float:
        return next((s for m, s in self.scores.items() if m in document), self.default)

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        with self._cv:
            self.calls.append((query, list(documents)))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self._cv.notify_all()
        try:
            if self.hold_s:
                with self._cv:
                    # Held open long enough for an overlapping call to show itself.
                    self._cv.wait_for(lambda: self.in_flight >= 2, timeout=self.hold_s)
            if self.error is not None:
                raise self.error
            return list(self.raw) if self.raw is not None else [self._score(d) for d in documents]
        finally:
            with self._cv:
                self.in_flight -= 1


@pytest.fixture(scope="session")
def payload():
    """Factory: a payload."""
    return make_payload


@pytest.fixture(scope="session")
def chunk_id_of():
    """The chunk-id formula."""
    return chunk_id


@pytest.fixture(scope="session")
def fake_qdrant():
    """Factory: a ``FakeQdrant``."""
    return FakeQdrant


@pytest.fixture(scope="session")
def fake_embedder():
    """Factory: a ``FakeEmbedder``."""
    return FakeEmbedder


@pytest.fixture(scope="session")
def fake_reranker():
    """Factory: a ``FakeReranker``."""
    return FakeReranker


@pytest.fixture(scope="session")
def conditions_of():
    """A Qdrant filter as ``{key: values}``."""
    return _conditions


@pytest.fixture(scope="session")
def make_retriever(make_config):
    """Factory: (real retriever, FakeQdrant, FakeEmbedder, FakeReranker)."""

    def make(payloads=(), *, scores=None, default=0.25, client=None, reranker=None,
             embedder=None, **settings: Any):
        config = make_config(**settings)
        client = client if client is not None else FakeQdrant(payloads)
        inner = reranker if reranker is not None else FakeReranker(scores, default=default)
        embedder = embedder if embedder is not None else FakeEmbedder()
        retriever = Html10kRetriever(
            embedder=embedder,
            client=client,
            reranker=CachedReranker(
                inner, model=config.reranker_model, cache_path=config.rerank_cache_path
            ),
            config=config,
        )
        return retriever, client, embedder, inner

    return make


# ----------------------------------------------------------------------------------
# The provider boundary.
# ----------------------------------------------------------------------------------


class FakeAdapter(ProviderAdapter):
    """Records each request and answers with a reply and token counts."""

    def __init__(
        self,
        reply: str = "{}",
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
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.calls += 1
            self.params.append(params)
        if self.error is not None:
            raise self.error
        return self.reply

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        with self._lock:
            self.stages.append(stage)
        return ParsedResponse(
            content=raw,
            finish_reason="stop",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            reasoning_tokens=0,
            cached_prompt_tokens=0,
        )

    def _text(self, role: str, call: int) -> str:
        return "\n".join(
            str(m.get("content", "")) for m in self.params[call]["messages"]
            if m.get("role") == role
        )

    def user_text(self, call: int = 0) -> str:
        return self._text("user", call)

    def system_text(self, call: int = 0) -> str:
        return self._text("system", call)

    def excerpts(self, call: int = 0) -> list[dict[str, Any]]:
        """The excerpt list of a call: requirement 16 has it start at the first ``[``."""
        user = self.user_text(call)
        found, _ = json.JSONDecoder().raw_decode(user, user.index("["))
        return found


def real_gateway(stage: str, adapter: FakeAdapter, run_budget: RunBudget | None) -> LLMGateway:
    return LLMGateway(
        stage=stage,
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=run_budget if run_budget is not None else RunBudget(),
        max_attempts=1,
        sleep_fn=lambda _s: None,
        adapters={p: adapter for p in PROVIDERS},
    )


# ----------------------------------------------------------------------------------
# The database boundary.
# ----------------------------------------------------------------------------------


class _EntityCursor:
    """Answers the extractor's SELECTs from ``ENTITIES``; the connection records the SQL."""

    def __init__(self, conn: _EntityConn) -> None:
        self.conn = conn
        self.row: dict[str, Any] | None = None

    def __enter__(self) -> _EntityCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.conn.sql.append(sql)
        self.row = None
        entities = self.conn.entities
        if "fiscal_calendar" in sql:
            return  # no calendar row: the extractor leaves the advisory dates None
        if "similarity(" in sql:
            wanted = str(params[0]).strip().lower()
            for entity in entities.values():
                if entity["company_name"].lower() == wanted:
                    self.row = {**entity, "sim": 1.0}
                    return
            self.row = {**next(iter(entities.values())), "sim": 0.0}
        elif "upper(trading_symbol)" in sql:
            self.row = entities.get(str(params[0]).strip().upper())
        elif "WHERE cik = %s" in sql:
            self.row = next((e for e in entities.values() if e["cik"] == params[0]), None)

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _EntityConn:
    def __init__(self, entities: dict[str, dict[str, Any]], sql: list[str]) -> None:
        self.entities = entities
        self.sql = sql

    def __enter__(self) -> _EntityConn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def cursor(self, cursor_factory: Any = None) -> _EntityCursor:
        return _EntityCursor(self)


class FakeDbExtractor(EntityExtractor):
    """The real extractor, on a fake connection that records its SQL."""

    def __init__(self, *, db_error: BaseException | None = None) -> None:
        super().__init__(db_conn_params={}, company_aliases={})
        self.entities = ENTITIES
        self.db_error = db_error
        self.sql: list[str] = []

    def _connect(self) -> _EntityConn:
        if self.db_error is not None:
            raise self.db_error
        return _EntityConn(self.entities, self.sql)


def router_json(
    companies: list[str] | None = None,
    periods: list[str] | None = None,
    *,
    intent: str = "NUMERIC_LOOKUP",
) -> str:
    """The router's reply object, as text."""
    return json.dumps(
        {
            "intent": intent,
            "company_mentions": list(companies or []),
            "period_mentions": list(periods or []),
            "concept_phrases": ["revenue"],
            "dimension_hint_present": False,
        }
    )


def build_router(
    reply: str = "",
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    error: BaseException | None = None,
    run_budget: RunBudget | None = None,
    db_error: BaseException | None = None,
) -> tuple[IntentRouter, FakeAdapter, FakeDbExtractor]:
    """A real ``IntentRouter`` on the real extractor logic; both boundaries are fake."""
    adapter = FakeAdapter(
        reply or router_json(["AAA"], ["fiscal 2024"]),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error=error,
    )
    extractor = FakeDbExtractor(db_error=db_error)
    router = IntentRouter(api_key="sk-spec-offline", model=ROUTER_MODEL, entity_extractor=extractor)
    # The gateway is the seam every stage is tested through: only its adapter is fake.
    router._llm_client = real_gateway("intent_router", adapter, run_budget)
    return router, adapter, extractor


@pytest.fixture(scope="session")
def router_reply():
    """Factory: the router's reply text."""
    return router_json


def call_cost(model: str, prompt: int, completion: int) -> float:
    """One call's cost: rates are per 1,000 tokens, and the fakes report no cached tokens."""
    rates = MODEL_PRICING[model]
    return prompt / 1000 * rates["input"] + completion / 1000 * rates["output"]


@pytest.fixture(scope="session")
def cost_of():
    """Factory: one call's cost."""
    return call_cost


@pytest.fixture(scope="session")
def router_model():
    """The router's model in these tests."""
    return ROUTER_MODEL


# ----------------------------------------------------------------------------------
# The filing-store and registry boundaries.
# ----------------------------------------------------------------------------------


class MemoryStore(FilingStore):
    """An in-memory filing store; a missing key raises FileNotFoundError like the local one."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
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


class _RegistryCursor:
    def __init__(self, conn: RegistryConn) -> None:
        self.conn = conn

    def __enter__(self) -> _RegistryCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.sql.append(sql)
        self.conn.params.append(params)

    def fetchall(self) -> list[tuple]:
        return list(self.conn.rows)


class RegistryConn:
    """A DB-API connection giving hand-written rows, in the column order of requirement 30."""

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.sql: list[str] = []
        self.params: list[Any] = []

    def cursor(self) -> _RegistryCursor:
        return _RegistryCursor(self)


@pytest.fixture(scope="session")
def memory_store():
    """Factory: a ``MemoryStore``."""
    return MemoryStore


@pytest.fixture(scope="session")
def registry_conn():
    """Factory: a fake registry connection."""
    return RegistryConn


# ----------------------------------------------------------------------------------
# Cases, replies and the assembled arm.
# ----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def make_case():
    """Factory: a scalar case."""

    def make(
        question: str = "What was AAA net sales in fiscal 2024?",
        *,
        ticker: str | None = "AAA",
        fy: int | None = 2024,
        cik: int | None = None,
        kind: str = "lookup",
        universe: list[str] | None = None,
        case_id: str = "c1",
    ) -> EvalCase:
        return EvalCase(
            id=case_id, question=question, kind=kind, expected_value=7.0, ticker=ticker, fy=fy,
            cik=cik, universe=list(universe or []),
        )

    return make


def reply_json(
    inputs: list[dict[str, Any]] | None = None,
    value_expr: str | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
    passages: list[dict[str, Any]] | None = None,
    refused: bool = False,
    hedged: bool = False,
    text: str = "The answer.",
    **extra: Any,
) -> str:
    """The answerer's reply object, as text."""
    return json.dumps(
        {
            "inputs": inputs or [],
            "value_expr": value_expr,
            "unit": "USD",
            "rows": rows or [],
            "passages": passages or [],
            "refused": refused,
            "hedged": hedged,
            "text": text,
            **extra,
        }
    )


@pytest.fixture(scope="session")
def reply():
    """Factory: the answerer's reply text."""
    return reply_json


def table_payload(**kwargs: Any) -> dict[str, Any]:
    return make_payload(
        "table",
        text=TABLE_TEXT,
        contains_table=True,
        table_index=3,
        section_key="item_8",
        section_label="Item 8. Financial Statements",
        **kwargs,
    )


@pytest.fixture(scope="session")
def table_chunk():
    """Factory: the payload of the hand-written income statement."""
    return table_payload


@pytest.fixture(scope="session")
def prose_chunk():
    """Factory: the payload of the hand-written passage."""

    def make(**kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("start", 100)
        kwargs.setdefault("end", 200)
        return make_payload("prose", text=PROSE_TEXT, **kwargs)

    return make


@pytest.fixture(scope="session")
def cell():
    """Factory: one cited input."""

    def make(
        id_: str, chunk: str, row: str | None, column: str | None, printed: str,
        scale: Any = 1000000,
    ) -> dict[str, Any]:
        return {"id": id_, "chunk_id": chunk, "row": row, "column": column,
                "printed": printed, "scale": scale}

    return make


@pytest.fixture(scope="session")
def make_arm(make_config):
    """Factory: an assembled arm and its fakes, as a namespace."""

    def make(
        payloads: Sequence[dict[str, Any]] | None = None,
        *,
        reply_text: str = "",
        router_text: str = "",
        router_error: BaseException | None = None,
        router_budget: RunBudget | None = None,
        db_error: BaseException | None = None,
        provider_error: BaseException | None = None,
        run_budget: RunBudget | None = None,
        embedder: FakeEmbedder | None = None,
        client: FakeQdrant | None = None,
        reranker: FakeReranker | None = None,
        scores: dict[str, float] | None = None,
        config: Any = None,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
        **settings: Any,
    ) -> SimpleNamespace:
        router, router_adapter, extractor = build_router(
            router_text, error=router_error, run_budget=router_budget, db_error=db_error
        )
        adapter = FakeAdapter(
            reply_text or reply_json(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error=provider_error,
        )
        client = client if client is not None else FakeQdrant(
            payloads if payloads is not None else [table_payload()]
        )
        embedder = embedder if embedder is not None else FakeEmbedder()
        reranker = reranker if reranker is not None else FakeReranker(scores)
        config = config if config is not None else make_config(**settings)
        arm = HtmlRagSystem(
            gateway=real_gateway("html_rag", adapter, run_budget),
            router=router,
            embedder=embedder,
            qdrant_client=client,
            reranker=reranker,
            config=config,
        )
        return SimpleNamespace(
            arm=arm, adapter=adapter, router_adapter=router_adapter, client=client,
            embedder=embedder, reranker=reranker, extractor=extractor, config=config,
        )

    return make
