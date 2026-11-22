"""IntentRouter wiring of the stated-range fill.

Spec: docs/specs/fiscal-year-range-split.md requirement 7.

The fakes sit at the I/O boundaries only: a provider adapter behind a real ``LLMGateway`` (the
router's and the planner's LLM), and a fake connection under the real ``EntityExtractor``
answering entity_master and fiscal_calendar from memory. The router, the extractor's logic,
``fiscal_date`` and the planner's prompt building run for real. AAA and its calendars are
synthetic.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink, trace_query
from src.retriever.xbrl.entity_extractor import EntityExtractor
from src.retriever.xbrl.intent_router import IntentRouter
from src.retriever.xbrl.plan_generator import PlanGenerator

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
MODEL = "gpt-5-mini"  # a priced model, explicit so the test does not depend on the profile

AAA_CIK = "0000000001"
AAA = {"cik": AAA_CIK, "trading_symbol": "AAA", "company_name": "Alpha Holdings",
       "fye_month": 12, "fye_day": 31, "last_seen_fy": 2025}


def _fy(year: int, start: date, end: date) -> dict[str, Any]:
    return {"fiscal_year": year, "fiscal_period": "FY", "period_start": start, "period_end": end}


# A December-31 filer, fiscal 2021..2025.
DEC31 = [_fy(y, date(y, 1, 1), date(y, 12, 31)) for y in range(2021, 2026)]
# A 52/53-week filer labelled by the year its fiscal year starts.
WEEKS = [
    _fy(2022, date(2022, 1, 31), date(2023, 1, 29)),
    _fy(2023, date(2023, 1, 30), date(2024, 1, 28)),
    _fy(2024, date(2024, 1, 29), date(2025, 2, 2)),
    _fy(2025, date(2025, 2, 3), date(2026, 2, 1)),
]


@pytest.fixture(autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off (the DB boundary)."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


class _FakeAdapter(ProviderAdapter):
    """Answers every call with *reply* and records each request."""

    def __init__(self, reply: str) -> None:
        self.provider = "fake"
        self.reply = reply
        self.params: list[dict] = []
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.params.append(params)
        return self.reply

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=raw, finish_reason="stop", prompt_tokens=100,
                              completion_tokens=50, reasoning_tokens=0, cached_prompt_tokens=0)


def _gateway(stage: str, adapter: _FakeAdapter) -> LLMGateway:
    return LLMGateway(stage=stage, cost_tracker=CostTracker(), rate_limiter=None,
                      run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                      adapters={p: adapter for p in PROVIDERS})


class _Cursor:
    """entity_master by ticker, and fiscal_calendar (whole calendar or one year's row)."""

    def __init__(self, calendar: list[dict[str, Any]], log: list[str]) -> None:
        self._calendar = calendar
        self._log = log
        self._rows: list[dict[str, Any]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._rows = []
        if "fiscal_calendar" in sql:
            if "fiscal_year = %s" in sql:
                self._log.append("calendar_year")
                _cik, year, period = params
                hits = [r for r in self._calendar if r["fiscal_year"] == year]
                hits.sort(key=lambda r: (r["fiscal_period"] != period,
                                         r["fiscal_period"] != "FY"))
                self._rows = hits[:1]
            else:
                self._log.append("calendar_all")
                self._rows = [{k: r[k] for k in ("fiscal_year", "fiscal_period", "period_end")}
                              for r in self._calendar]
        elif "upper(trading_symbol)" in sql and str(params[0]).strip().upper() == "AAA":
            self._rows = [AAA]
        elif "WHERE cik = %s" in sql and params[0] == AAA_CIK:
            self._rows = [AAA]

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _Conn:
    def __init__(self, calendar: list[dict[str, Any]], log: list[str]) -> None:
        self._calendar = calendar
        self._log = log

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def cursor(self, cursor_factory: Any = None) -> _Cursor:
        return _Cursor(self._calendar, self._log)


class _FakeDbExtractor(EntityExtractor):
    """The real extractor on a fake connection."""

    def __init__(self, calendar: list[dict[str, Any]]) -> None:
        super().__init__(db_conn_params={}, company_aliases={})
        self.calendar = calendar
        self.log: list[str] = []

    def _connect(self) -> _Conn:
        return _Conn(self.calendar, self.log)


def _route(query: str, mentions: list[str], calendar: list[dict[str, Any]] | None = None):
    extractor = _FakeDbExtractor(DEC31 if calendar is None else calendar)
    router = IntentRouter(api_key="sk-unit-offline", model=MODEL, entity_extractor=extractor)
    reply = json.dumps({"intent": "NUMERIC_LOOKUP", "company_mentions": ["AAA"],
                        "period_mentions": mentions, "concept_phrases": ["revenue"],
                        "dimension_hint_present": False})
    router._llm_client = _gateway("intent_router", _FakeAdapter(reply))
    with trace_query(query) as tc:
        result = router.route(query)
    fills = [e for e in tc.events
             if e["kind"] == "decision" and e.get("decision") == "stated_range_fill"]
    return result, fills, extractor.log


def _planner_years(query: str, result) -> list[int]:
    """The ``FISCAL_YEARS_IN_SCOPE`` list the real planner writes into its user message."""
    adapter = _FakeAdapter(json.dumps({"error": "unit_capture", "reason": "prompt captured"}))
    planner = PlanGenerator(api_key="sk-unit-offline", model=MODEL, max_retries=0)
    planner._llm_client = _gateway("plan_generator", adapter)
    planner.generate_plan(query, result)
    text = "\n".join(
        m["content"] if isinstance(m["content"], str)
        else "".join(str(p.get("text", "")) for p in m["content"])
        for m in adapter.params[0]["messages"] if m.get("role") == "user"
    )
    after = text.split("FISCAL_YEARS_IN_SCOPE", 1)[1]
    start = after.index("[")
    return json.loads(after[start:after.index("]", start) + 1])


def test_split_range_mentions_get_the_middle_year_in_planner_scope():
    # oracle: the question states FY23 through FY25; the router split it into its endpoints
    query = "AAA revenue trend, FY23 through FY25"
    result, fills, log = _route(query, ["FY23", "FY25"])
    assert _planner_years(query, result) == [2023, 2024, 2025]
    added = result.periods[-1]
    assert (added.fiscal_year, added.fiscal_period, added.ytd, added.ambiguous) == (
        2024, "FY", False, False)
    assert added.mention == "FY23 through FY25"
    assert added.period_end == "2024-12-31"
    assert [(e["added"], e["resolved"], e["point"]) for e in fills] == [
        ([2024], [2023, 2025], "period_resolution")]
    # no full date written: the whole calendar is never read
    assert "calendar_all" not in log


def test_a_list_keeps_its_years_and_logs_nothing():
    # oracle: "FY23 and FY25" is a list of two years (spec requirement 2)
    query = "AAA revenue FY23 and FY25"
    result, fills, _log = _route(query, ["FY23", "FY25"])
    assert _planner_years(query, result) == [2023, 2025]
    assert fills == []


def test_one_range_mention_is_left_alone():
    # oracle: a range kept as one mention already resolves to every year (fiscal-year-range)
    query = "AAA revenue trend, FY23 through FY25"
    result, fills, _log = _route(query, ["FY23 through FY25"])
    assert [p.fiscal_year for p in result.periods] == [2023, 2024, 2025]
    assert fills == []


def test_dated_endpoints_read_the_filers_calendar():
    # oracle: WEEKS rows — 2024-01-28 ends fiscal 2023, 2026-02-01 ends fiscal 2025, so the
    # stated range is fiscal 2023 through 2025 and fiscal 2024 is missing
    query = ("AAA revenue trend, the fiscal year ended January 28, 2024 through the fiscal year "
             "ended February 1, 2026")
    result, fills, log = _route(query, ["the fiscal year ended January 28, 2024",
                                        "the fiscal year ended February 1, 2026"], WEEKS)
    assert _planner_years(query, result) == [2023, 2024, 2025]
    assert result.periods[-1].mention == ("the fiscal year ended January 28, 2024 through the "
                                          "fiscal year ended February 1, 2026")
    assert result.periods[-1].period_end == "2025-02-02"
    assert [e["added"] for e in fills] == [[2024]]
    assert "calendar_all" in log
