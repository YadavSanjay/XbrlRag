"""Fakes and builders for the named-company-screen spec tests
(docs/specs/named-company-screen.md).

The fakes sit at the I/O boundaries only:

* the database behind ``XbrlFactstoreScreen``: ``psycopg2.connect`` is replaced by a fake
  store that answers the publish-manifest probe (``to_regclass``) with "absent", any SQL
  naming ``entity_master`` with the roster, and any other SQL with, per filer, the row of its
  latest fiscal year <= ``params["fy"]``. It ignores every other predicate (ticker lists
  included), so restricting the ranking has to happen on the rows it returns;
* the LLM provider behind a real ``LLMGateway`` for ``PlanGenerator``: a scripted adapter.

The screen, the validator, the planner and the gateway are real. All values are synthetic.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import types
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

import psycopg2
import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink
from src.retriever.xbrl.entity_extractor import ResolvedCompany
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.xbrl_factstore_screen import ScreenResult, XbrlFactstoreScreen

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
# A priced model, explicit so the tests do not depend on the active profile.
MODEL = "gpt-5-mini"

# Synthetic CIKs for the label tickers.
CIKS = {"AAPL": "0000000101", "MSFT": "0000000102", "GOOGL": "0000000103",
        "AMZN": "0000000104", "META": "0000000105", "NVDA": "0000000106",
        "TSLA": "0000000107", "ORCL": "0000000108"}
POOL = sorted(CIKS)


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


# ----------------------------------------------------------------------------------
# The database boundary behind the screen.
# ----------------------------------------------------------------------------------

# A store: ticker -> [(fiscal_year, value, stored period_end)].
Store = Mapping[str, Sequence[tuple[int, float, dt.date]]]


class _Cursor:
    def __init__(self, store: Store, roster: Sequence[str], as_dict: bool):
        self._store = store
        self._roster = roster
        self._as_dict = as_dict
        self._rows: list = []
        everyone = sorted(set(store) | set(roster))
        self._cik = {t: f"{i + 1:010d}" for i, t in enumerate(everyone)}

    def execute(self, sql, params=None):
        params = params if isinstance(params, Mapping) else {}
        if "to_regclass" in sql:
            self._rows = [(False,)]
            return
        if "entity_master" in sql:
            roster = [{"cik": self._cik[t], "ticker": t} for t in self._roster]
            self._rows = roster if self._as_dict else [(r["cik"], r["ticker"]) for r in roster]
            return
        fy = params.get("fy")
        rows = []
        for ticker in sorted(self._store):
            eligible = [e for e in self._store[ticker] if fy is None or e[0] <= fy]
            if not eligible:
                continue
            year, value, stored_end = max(eligible)
            rows.append({
                "ticker": ticker, "cik": self._cik[ticker], "fiscal_year": year,
                "value": value, "unit": "USD", "period_end": stored_end,
                "section_path": None, "html_anchor": None, "page_num": None,
                "accession_no": None, "fact_id": 1000 + len(rows),
                "concept": "us-gaap:NetIncomeLoss", "concept_pref_rank": 1,
            })
        self._rows = rows if self._as_dict else [tuple(r.values()) for r in rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def mogrify(self, sql, params=None):
        return sql.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, store: Store, roster: Sequence[str]):
        self._store = store
        self._roster = roster

    def cursor(self, cursor_factory=None):
        return _Cursor(self._store, self._roster, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_named_company_screen_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _run_screen(store: Store, roster: Iterable[str], fy: int, top_n: int | None = None,
                **step_fields: Any) -> ScreenResult:
    roster = list(roster)
    step: dict[str, Any] = {"id": "s1", "op": "screen", "fy": fy, "order": "desc",
                            "top_n": top_n, "dimension": "NONE", "metric_kind": "value",
                            "concept_family": "NetIncome"}
    step.update(step_fields)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda **kw: _Conn(store, roster))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


@pytest.fixture(scope="session")
def run_screen() -> Callable[..., ScreenResult]:
    """``run_screen(store, roster, fy, top_n=None, **step_fields)``: a flat net-income
    screen over the fake store; safe inside hypothesis tests (session-scoped)."""
    return _run_screen


# ----------------------------------------------------------------------------------
# The provider boundary behind the planner.
# ----------------------------------------------------------------------------------


class ScriptedAdapter(ProviderAdapter):
    """Answers call i with ``replies[i]`` (the last reply repeats); records each request."""

    def __init__(self, replies: Sequence[str]) -> None:
        self.provider = "fake"
        self.replies = list(replies)
        self.params: list[dict] = []
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.params.append(params)
            return self.replies[min(len(self.params) - 1, len(self.replies) - 1)]

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=raw, finish_reason="stop", prompt_tokens=100,
                              completion_tokens=50, reasoning_tokens=0, cached_prompt_tokens=0)

    @property
    def calls(self) -> int:
        return len(self.params)

    def user_text(self, call: int = 0) -> str:
        """The user message's text parts of call ``call``, joined."""
        parts: list[str] = []
        for message in self.params[call]["messages"]:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            else:
                parts.extend(str(p.get("text", "")) for p in content)
        return "\n".join(parts)


def _real_gateway(adapter: ScriptedAdapter) -> LLMGateway:
    return LLMGateway(stage="plan_generator", cost_tracker=CostTracker(), rate_limiter=None,
                      run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                      adapters={p: adapter for p in PROVIDERS})


def screen_plan(universe: Any = "<absent>") -> dict:
    """A one-step FY2024 R&D screen, top 1; ``universe`` omitted when ``"<absent>"``."""
    step: dict[str, Any] = {"id": "s1", "op": "screen", "fy": 2024, "dimension": "NONE",
                            "metric_kind": "value", "concept_family": "ResearchAndDevelopment",
                            "order": "desc", "top_n": 1}
    if universe != "<absent>":
        step["universe"] = universe
    return {"steps": [step]}


def company(ticker: str, mention: str | None = None) -> ResolvedCompany:
    """A resolved company with a synthetic CIK."""
    return ResolvedCompany(mention=mention or ticker, cik=CIKS[ticker.upper()], ticker=ticker,
                           company_name=f"{ticker.upper()} Synthetic Corp", fye_month=12,
                           fye_day=31, last_seen_fy=2025, resolution="exact_ticker",
                           similarity=1.0)


def unresolved(mention: str, resolution: str = "unresolved") -> ResolvedCompany:
    return ResolvedCompany(mention=mention, resolution=resolution)


def intent(tickers: Sequence[str], universe: str | None = None) -> IntentRouterResult:
    return IntentRouterResult(intent=XBRL_INTENT.NUMERIC_SCREEN,
                              companies=[company(t) for t in tickers],
                              concept_candidates=["research and development"],
                              universe=universe)


def _plan_with(replies: Sequence[dict], query: str, intent_result: IntentRouterResult
               ) -> tuple[dict, ScriptedAdapter]:
    adapter = ScriptedAdapter([json.dumps(r) for r in replies])
    planner = PlanGenerator(api_key="sk-spec-offline", model=MODEL, max_retries=1)
    planner._llm_client = _real_gateway(adapter)
    return planner.generate_plan(query, intent_result), adapter


@pytest.fixture(scope="session")
def plan_with() -> Callable[..., tuple[dict, ScriptedAdapter]]:
    """``plan_with(replies, query, intent)``: the real planner over scripted replies;
    returns ``(result, adapter)``."""
    return _plan_with


@pytest.fixture(scope="session")
def kit() -> types.SimpleNamespace:
    """The shared builders (session-scoped, so hypothesis tests may use it)."""
    return types.SimpleNamespace(POOL=POOL, CIKS=CIKS, screen_plan=screen_plan,
                                 company=company, unresolved=unresolved, intent=intent)
