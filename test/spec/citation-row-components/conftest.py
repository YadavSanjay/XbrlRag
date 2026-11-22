"""Fixtures for the citation-row-components spec tests (docs/specs/citation-row-components.md).

The steps are hand-built ``StepResult.to_dict()`` dicts. The covered FY2024 Debt screen rows
carry real ``xbrl_db`` values and part ids, as the S4 measure builds them (read-only trace
replay, 2026-11-14; see the spec's Notes); the screen's disclosure, the breakdown's extra
component id and the derived row are synthetic. The only fake is the verifier's LLM: a
scripted provider adapter behind the real ``LLMGateway`` (as in the citation-integrity spec).
"""

from __future__ import annotations

import json
import os
import threading
import types
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse

API_KEY = "sk-spec-offline"
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
_ENV_VARS = ("XBRLRAG_LLM_PROFILE", "XBRLRAG_OPENAI_SERVICE_TIER", "XBRLRAG_QUERY_CACHE",
             "XBRLRAG_RUN_MAX_COST_USD", "XBRLRAG_RUN_MAX_LLM_CALLS", "XBRLRAG_RUN_MAX_TOKENS",
             "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES", "XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S")


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off (the database boundary)."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@pytest.fixture(scope="session", autouse=True)
def _clean_env():
    """Clears the profile, cache and run-budget variables for the session."""
    saved = {k: os.environ.pop(k) for k in _ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        for k in _ENV_VARS:
            os.environ.pop(k, None)
        os.environ.update(saved)


# --------------------------------------------------------------------------- LLM boundary


class ScriptAdapter(ProviderAdapter):
    """Answers every call with *final*, recording the params it was sent."""

    def __init__(self, final: str) -> None:
        self.provider = "fake"
        self.final = final
        self.invoked = 0
        self.params: list[dict] = []
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return {k: v for k, v in kwargs.items() if k != "extra"}

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.invoked += 1
            self.params.append(params)
            return self.final

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=str(raw), finish_reason="stop", prompt_tokens=10,
                              completion_tokens=5)


def supported_reply(n: int = 64) -> str:
    """The verifier LLM's reply: every index SUPPORTED."""
    return json.dumps({"verdicts": [{"index": i, "verdict": "SUPPORTED",
                                     "reason": "matches the cited evidence"} for i in range(n)]})


def make_verifier() -> tuple[XbrlFaithfulnessVerifier, ScriptAdapter]:
    """The real verifier; its LLM answers SUPPORTED for every statement."""
    verifier = XbrlFaithfulnessVerifier(api_key=API_KEY)
    adapter = ScriptAdapter(supported_reply())
    verifier._llm_client = LLMGateway(
        stage="verifier", cost_tracker=CostTracker(), rate_limiter=None,
        run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda s: None,
        adapters={p: adapter for p in PROVIDERS})
    return verifier, adapter


def _user_texts(adapter: ScriptAdapter) -> list[str]:
    texts: list[str] = []
    for params in adapter.params:
        for message in params.get("messages") or []:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
            else:
                texts += [c.get("text", "") for c in content or [] if isinstance(c, dict)]
    return texts


def sent_entries(adapter: ScriptAdapter) -> dict[str, dict]:
    """Every statement entry the LLM was sent, by statement text.

    The entries are the JSON list the user prompt embeds (``statements_json``), found as the
    list holding the first ``"index"`` key, whatever the template's surrounding words.
    """
    out: dict[str, dict] = {}
    decoder = json.JSONDecoder()
    for text in _user_texts(adapter):
        at = text.find('"index"')
        if at < 0:
            continue
        start = text.rfind("[", 0, at)
        entries, _ = decoder.raw_decode(text[start:])
        for entry in entries:
            out[entry["statement"]] = entry
    return out


# --------------------------------------------------------------------------- recorded steps

DEBT_ENDS = {"AAPL": "2024-09-28", "ORCL": "2024-05-31", "AVGO": "2024-11-03"}
DEBT_VALUES = {"AAPL": 106_629_000_000.0, "ORCL": 86_869_000_000.0, "AVGO": 67_566_000_000.0}
DEBT_PARTS = {"AAPL": [14500, 14502, 14506], "ORCL": [71847, 71859], "AVGO": [26581, 26587]}
DEBT_CIK = {"AAPL": "0000320193", "ORCL": "0001341439", "AVGO": "0001730168"}
DEBT_ORDER = ["AAPL", "ORCL", "AVGO"]
# Synthetic disclosure: 7 members, 6 ranked, 1 excluded; 3 rows returned.
DEBT_RANKED = ["AAPL", "ORCL", "AVGO", "AMZN", "META", "MSFT"]
DEBT_MEMBERS = 7
DEBT_EXCLUSIONS = [{"ticker": "TSM", "reason": "currency", "currency": "TWD"}]


def debt_row(ticker: str) -> dict:
    """One covered FY2024 Debt screen row, ranked by its S4 measure (first part = fact_id)."""
    parts = DEBT_PARTS[ticker]
    return {"cik": DEBT_CIK[ticker], "rank": DEBT_ORDER.index(ticker) + 1, "unit": "USD",
            "label": "Debt", "value": DEBT_VALUES[ticker], "ticker": ticker,
            "fact_id": parts[0],
            "citation": {"fact_id": parts[0], "page_num": None, "html_anchor": None,
                         "accession_no": "acc", "section_path": "Balance Sheet"},
            "period_end": DEBT_ENDS[ticker], "fiscal_year": 2024, "pct_of_total": None,
            "component_fact_ids": {f"part_{n}": fid for n, fid in enumerate(parts, start=1)},
            "definition": {"concept": "s4:debt_total", "basis": "total",
                           "disclosure": None}}


def debt_screen() -> dict:
    """The covered FY2024 Debt screen, top 3."""
    return {"op": "screen", "rows": [debt_row(t) for t in DEBT_ORDER], "unit": None,
            "error": None, "value": None, "status": "OK", "step_id": "debt_screen",
            "citation": None, "confidence": 0.0,
            "disclosure": {"ranked": list(DEBT_RANKED), "members": DEBT_MEMBERS,
                           "universe": "covered", "exclusions": [dict(e) for e in DEBT_EXCLUSIONS],
                           "fiscal_year": 2024, "period_ends": dict(DEBT_ENDS),
                           "period_ends_differ": True},
            "linked_chunk_ids": []}


# Synthetic breakdown row with a second component (array form) and a derived screen row with a
# synthetic fact_id whose components list one input's alternatives (one nested array level).
BREAKDOWN_ROW_FACT = 13550
BREAKDOWN_ROW_COMPONENTS = [13550, 99001]
DERIVED_ROW_FACT = "OperatingMargin:0000320193:2024"
DERIVED_ROW_COMPONENTS = {"operating_income": 99101, "revenue": [99102, 99103], "cogs": None}


def breakdown_step() -> dict:
    row = {"label": "iPhone", "member": "IPhoneMember", "value": 201_183_000_000.0,
           "unit": "USD", "pct_of_total": 0.5145, "fact_id": BREAKDOWN_ROW_FACT,
           "citation": {"fact_id": BREAKDOWN_ROW_FACT}, "fiscal_year": 2024,
           "component_fact_ids": list(BREAKDOWN_ROW_COMPONENTS)}
    return {"op": "breakdown", "rows": [row], "unit": "USD", "error": None, "value": None,
            "status": "OK", "step_id": "b1", "citation": None, "confidence": 1.0,
            "linked_chunk_ids": []}


def derived_screen() -> dict:
    row = {"ticker": "AAPL", "cik": "0000320193", "rank": 1, "value": 0.3151, "unit": None,
           "label": "OperatingMargin", "fact_id": DERIVED_ROW_FACT,
           "citation": {"fact_id": None}, "fiscal_year": 2024, "pct_of_total": None,
           "component_fact_ids": dict(DERIVED_ROW_COMPONENTS)}
    return {"op": "screen", "rows": [row], "unit": None, "error": None, "value": None,
            "status": "OK", "step_id": "m_screen", "citation": None, "confidence": 0.0,
            "linked_chunk_ids": []}


def lookup_step() -> dict:
    """Apple's FY2024 revenue lookup (fact 13253, 391,035,000,000; citation-integrity Notes)."""
    return {"op": "lookup", "unit": "USD", "error": None, "value": 391_035_000_000.0,
            "decimals": -6, "status": "OK", "step_id": "r_aapl",
            "citation": {"fact_id": 13253}, "confidence": 1.0, "linked_chunk_ids": []}


def detail(statement: str, fact_ids: list | None = None, chunk_ids: list | None = None) -> dict:
    return {"statement": statement, "fact_ids": [str(f) for f in fact_ids or []],
            "chunk_ids": list(chunk_ids or [])}


def execution(steps: list[dict]) -> dict:
    return {"status": "OK", "steps": steps, "bindings": {}, "error": None}


def response(details: list[dict], steps: list[dict]) -> XbrlRagResponse:
    """A recorded synthesizer response (before the verifier)."""
    return XbrlRagResponse(
        query="recorded question", answer_summary="recorded summary.",
        answer_details=details, unanswered_parts=[], metadata={"fully_answered": True},
        plan={"steps": []}, execution_result=execution(steps), narrative_chunks=[])


@pytest.fixture(scope="session")
def crc():
    """The builders and constants above, as attributes (spec tests cannot import conftest)."""
    names = ("DEBT_ENDS", "DEBT_VALUES", "DEBT_PARTS", "DEBT_CIK", "DEBT_ORDER", "DEBT_RANKED",
             "DEBT_MEMBERS", "DEBT_EXCLUSIONS", "BREAKDOWN_ROW_FACT", "BREAKDOWN_ROW_COMPONENTS",
             "DERIVED_ROW_FACT", "DERIVED_ROW_COMPONENTS", "breakdown_step", "debt_row",
             "debt_screen", "derived_screen", "detail", "execution", "lookup_step",
             "make_verifier", "response", "sent_entries")
    g = globals()
    return types.SimpleNamespace(**{n: g[n] for n in names})
