"""Fakes and builders for the long-context-arms spec tests (docs/specs/long-context-arms.md).

The fakes sit at the I/O boundaries and nowhere else: a provider adapter behind a real
``LLMGateway`` (one for the router, one for the answer), an ``EntityExtractor`` whose
connection is fake, a ``FilingResolver`` subclass or a fake registry connection, and an
in-memory ``FilingStore``. ``IntentRouter``, the extractor's logic, the arms, ``filing_text``,
the prompt registry and the answer parser are all real.

Fixtures are session-scoped factories so hypothesis-driven tests can use them without
tripping the function-scoped-fixture health check. AAA, BBB and CCC are synthetic.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.systems.base import FilingResolver
from src.eval.systems.long_context_arms import LongContextMultiSystem, LongContextResolvedSystem
from src.ingest.sec_edgar.storage import FilingStore
from src.llm.config import MODEL_PRICING
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink
from src.retriever.xbrl.entity_extractor import EntityExtractor
from src.retriever.xbrl.intent_router import IntentRouter

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
# A priced model, explicit so the tests do not depend on the active profile.
ROUTER_MODEL = "gpt-5-mini"

AAA_2024 = "AAA/2024/10-K/canonical.htm"

ENTITIES: dict[str, dict[str, Any]] = {
    "AAA": {"cik": "0000000001", "trading_symbol": "AAA", "company_name": "Alpha Holdings",
            "fye_month": 12, "fye_day": 31, "last_seen_fy": 2024},
    "BBB": {"cik": "0000000002", "trading_symbol": "BBB", "company_name": "Beta Industries",
            "fye_month": 12, "fye_day": 31, "last_seen_fy": 2024},
    "CCC": {"cik": "0000000003", "trading_symbol": "CCC", "company_name": "Gamma Systems",
            "fye_month": 6, "fye_day": 30, "last_seen_fy": 2023},
    "NUL": {"cik": "0000000004", "trading_symbol": "NUL", "company_name": "Null Year Corp",
            "fye_month": 12, "fye_day": 31, "last_seen_fy": None},
    "HD": {"cik": "0000354950", "trading_symbol": "HD", "company_name": "Home Depot",
           "fye_month": 2, "fye_day": 1, "last_seen_fy": 2025},
}


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
# Hand-built filings.
# ----------------------------------------------------------------------------------


def html_doc(*paragraphs: str) -> str:
    """Canonical-style XHTML, one paragraph per argument (``filing_text`` gives one line each)."""
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>{body}</body></html>'


@pytest.fixture(scope="session")
def doc():
    """Factory: a filing with the given paragraphs."""
    return html_doc


# ----------------------------------------------------------------------------------
# The provider boundary.
# ----------------------------------------------------------------------------------


class FakeAdapter(ProviderAdapter):
    """Records each request and answers with a reply and token counts.

    *table* maps a marker to a reply: the first marker found in the user text wins, and
    *reply* is the default.
    """

    def __init__(
        self,
        reply: str = "{}",
        *,
        table: dict[str, str] | None = None,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
        error: BaseException | None = None,
    ) -> None:
        self.provider = "fake"
        self.reply = reply
        self.table = dict(table or {})
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

    def _choose(self, params: dict) -> str:
        user = _user_text(params)
        for marker, text in self.table.items():
            if marker in user:
                return text
        return self.reply

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.calls += 1
            self.params.append(params)
        if self.error is not None:
            raise self.error
        return self._choose(params)

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

    def messages(self, call: int = 0) -> list[dict]:
        return list(self.params[call]["messages"])

    def user_text(self, call: int = 0) -> str:
        return _user_text(self.params[call])

    def system_text(self, call: int = 0) -> str:
        return "\n".join(
            str(m.get("content", "")) for m in self.messages(call) if m.get("role") == "system"
        )


def _user_text(params: dict) -> str:
    return "\n".join(
        str(m.get("content", "")) for m in params["messages"] if m.get("role") == "user"
    )


class OverlapAdapter(FakeAdapter):
    """Counts calls in flight per filing marker; see long-context-arm's C4 for the method.

    With *barrier*, a call waits on it and so passes only alongside another. Without one, a
    call holds open up to *overlap_wait_s* for a second call to arrive, and never fails.
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
        self.in_flight = {m: 0 for m in markers}
        self.max_per_filing = {m: 0 for m in markers}
        self._cv = threading.Condition()

    def invoke(self, client: Any, params: dict) -> Any:
        user = _user_text(params)
        filing = next((m for m in self.markers if m in user), "<unknown>")
        with self._cv:
            self.calls += 1
            self.params.append(params)
            self.in_flight[filing] = self.in_flight.get(filing, 0) + 1
            self.max_per_filing[filing] = max(
                self.max_per_filing.get(filing, 0), self.in_flight[filing]
            )
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
        return self.reply


@pytest.fixture(scope="session")
def overlap_adapter():
    """Factory: an ``OverlapAdapter``."""
    return OverlapAdapter


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
# The database boundary: entity_master and fiscal_calendar behind the extractor.
# ----------------------------------------------------------------------------------


class _EntityCursor:
    """Answers the extractor's four SELECTs from ``ENTITIES``."""

    def __init__(self, entities: dict[str, dict[str, Any]]) -> None:
        self.entities = entities
        self.row: dict[str, Any] | None = None

    def __enter__(self) -> _EntityCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.row = None
        if "fiscal_calendar" in sql:
            return  # no calendar row: the extractor leaves the advisory dates None
        if "similarity(" in sql:
            wanted = str(params[0]).strip().lower()
            for entity in self.entities.values():
                if entity["company_name"].lower() == wanted:
                    self.row = {**entity, "sim": 1.0}
                    return
            self.row = {**next(iter(self.entities.values())), "sim": 0.0}
        elif "upper(trading_symbol)" in sql:
            self.row = self.entities.get(str(params[0]).strip().upper())
        elif "WHERE cik = %s" in sql:
            self.row = next(
                (e for e in self.entities.values() if e["cik"] == params[0]), None
            )

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _EntityConn:
    def __init__(self, entities: dict[str, dict[str, Any]]) -> None:
        self.entities = entities

    def __enter__(self) -> _EntityConn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def cursor(self, cursor_factory: Any = None) -> _EntityCursor:
        return _EntityCursor(self.entities)


class FakeDbExtractor(EntityExtractor):
    """The real extractor, on a fake connection (the DB boundary)."""

    def __init__(
        self,
        entities: dict[str, dict[str, Any]] | None = None,
        *,
        db_error: BaseException | None = None,
    ) -> None:
        super().__init__(db_conn_params={}, company_aliases={})
        self.entities = entities if entities is not None else ENTITIES
        self.db_error = db_error

    def _connect(self) -> _EntityConn:
        if self.db_error is not None:
            raise self.db_error
        return _EntityConn(self.entities)


def router_json(
    companies: list[str] | None = None,
    periods: list[str] | None = None,
    *,
    intent: str = "NUMERIC_LOOKUP",
    concepts: list[str] | None = None,
) -> str:
    """The router's reply object, as text."""
    return json.dumps(
        {
            "intent": intent,
            "company_mentions": list(companies or []),
            "period_mentions": list(periods or []),
            "concept_phrases": list(concepts if concepts is not None else ["revenue"]),
            "dimension_hint_present": False,
        }
    )


@pytest.fixture(scope="session")
def router_reply():
    """Factory: the router's reply text."""
    return router_json


def build_router(
    reply: str = "",
    *,
    table: dict[str, str] | None = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    error: BaseException | None = None,
    run_budget: RunBudget | None = None,
    entities: dict[str, dict[str, Any]] | None = None,
    db_error: BaseException | None = None,
) -> tuple[IntentRouter, FakeAdapter]:
    """A real ``IntentRouter`` on the real extractor logic; both boundaries are fake."""
    adapter = FakeAdapter(
        reply or router_json(["AAA"], ["fiscal 2024"]),
        table=table,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error=error,
    )
    router = IntentRouter(
        api_key="sk-spec-offline",
        model=ROUTER_MODEL,
        entity_extractor=FakeDbExtractor(entities, db_error=db_error),
    )
    # The gateway is the seam every stage is tested through: only its adapter is fake.
    router._llm_client = real_gateway("intent_router", adapter, run_budget)
    return router, adapter


def call_cost(model: str, prompt: int, completion: int) -> float:
    """One call's cost: rates are per 1,000 tokens, and the fakes report no cached tokens."""
    rates = MODEL_PRICING[model]
    return prompt / 1000 * rates["input"] + completion / 1000 * rates["output"]


@pytest.fixture(scope="session")
def router_cost():
    """Factory: the router call's cost on gpt-5-mini (defaults: 100 and 50 tokens)."""

    def cost(prompt: int = 100, completion: int = 50) -> float:
        return call_cost(ROUTER_MODEL, prompt, completion)

    return cost


@pytest.fixture(scope="session")
def answer_cost():
    """Factory: the answer call's cost on gpt-5.6-sol (defaults: 1000 and 500 tokens)."""

    def cost(prompt: int = 1000, completion: int = 500) -> float:
        return call_cost("gpt-5.6-sol", prompt, completion)

    return cost


@pytest.fixture(scope="session")
def make_router():
    """Factory: (real IntentRouter, its FakeAdapter)."""
    return build_router


# ----------------------------------------------------------------------------------
# The filing-registry and filing-store boundaries.
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


@pytest.fixture(scope="session")
def fake_resolver():
    """Factory: a ``FakeResolver``."""
    return FakeResolver


@pytest.fixture(scope="session")
def memory_store():
    """Factory: a ``MemoryStore``."""
    return MemoryStore


class _RegistryCursor:
    """Answers ``PostgresFilingResolver``'s SELECT from hand-copied registry rows.

    It selects on the label, which is what the real SQL does; the test also checks that the
    SQL's text reads ``fr.fiscal_year``.
    """

    def __init__(self, conn: RegistryConn) -> None:
        self.conn = conn
        self.rows: list[tuple[str, str, str]] = []

    def __enter__(self) -> _RegistryCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.conn.sql.append(sql)
        ticker, fiscal_year, _forms = params
        self.rows = [
            (r["accession_no"], r["form_type"], r["entry_url"])
            for r in self.conn.registry
            if r["ticker"] == ticker and r["fiscal_year"] == fiscal_year
        ]

    def fetchall(self) -> list[tuple[str, str, str]]:
        return self.rows


class RegistryConn:
    """A DB-API connection onto hand-copied ``filing_registry`` rows."""

    def __init__(self, registry: list[dict[str, Any]]) -> None:
        self.registry = registry
        self.sql: list[str] = []

    def cursor(self) -> _RegistryCursor:
        return _RegistryCursor(self)

    def close(self) -> None:
        return None


@pytest.fixture(scope="session")
def registry_conn():
    """Factory: a fake connection for ``PostgresFilingResolver``."""
    return RegistryConn


# ----------------------------------------------------------------------------------
# Cases, replies and the assembled arms.
# ----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def make_case():
    """Factory: a scalar case."""

    def make(
        question: str = "What was AAA total revenue in fiscal 2024?",
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


def answer_json(value: Any = 7, *, refused: bool = False) -> str:
    return json.dumps(
        {"value": value, "unit": "USD", "rows": [], "refused": refused, "hedged": False,
         "cited_chunks": ["should-be-dropped"], "text": "answer"}
    )


@pytest.fixture(scope="session")
def answer_reply():
    """Factory: the arm-neutral answer object, as reply text."""
    return answer_json


def _assemble(
    cls: type,
    *,
    router_text: str = "",
    router_table: dict[str, str] | None = None,
    router_error: BaseException | None = None,
    router_prompt_tokens: int = 100,
    router_completion_tokens: int = 50,
    db_error: BaseException | None = None,
    reply_text: str | None = None,
    prompt_tokens: int = 1000,
    completion_tokens: int = 500,
    provider_error: BaseException | None = None,
    keys: dict[tuple[str, int], str] | None = None,
    objects: dict[str, str] | None = None,
    resolver_error: BaseException | None = None,
    store_error: BaseException | None = None,
    run_budget: RunBudget | None = None,
    router_budget: RunBudget | None = None,
    adapter: FakeAdapter | None = None,
    resolver: FilingResolver | None = None,
    **arm_kwargs: Any,
):
    router, router_adapter = build_router(
        router_text,
        table=router_table,
        prompt_tokens=router_prompt_tokens,
        completion_tokens=router_completion_tokens,
        error=router_error,
        run_budget=router_budget,
        db_error=db_error,
    )
    if adapter is None:
        adapter = FakeAdapter(
            reply_text if reply_text is not None else answer_json(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error=provider_error,
        )
    stage = "long_context_multi" if cls is LongContextMultiSystem else "long_context"
    gateway = real_gateway(stage, adapter, run_budget)
    resolver = resolver if resolver is not None else FakeResolver(
        keys if keys is not None else {("AAA", 2024): AAA_2024}, error=resolver_error
    )
    store = MemoryStore(
        objects if objects is not None else {AAA_2024: html_doc("AAA revenue was 7 dollars.")},
        error=store_error,
    )
    # The model answer_cost prices, named: arm-model-from-profile makes the default the
    # active profile's frontier model.
    arm_kwargs.setdefault("model", "gpt-5.6-sol")
    arm = cls(gateway=gateway, router=router, resolver=resolver, store=store, **arm_kwargs)
    return arm, router_adapter, adapter, resolver, store


@pytest.fixture(scope="session")
def make_resolved():
    """Factory: (arm, router adapter, answer adapter, resolver, store) for the resolved arm."""

    def make(**kwargs: Any):
        return _assemble(LongContextResolvedSystem, **kwargs)

    return make


@pytest.fixture(scope="session")
def make_multi():
    """Factory: (arm, router adapter, answer adapter, resolver, store) for the multi arm."""

    def make(**kwargs: Any):
        return _assemble(LongContextMultiSystem, **kwargs)

    return make
