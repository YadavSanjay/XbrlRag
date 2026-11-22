"""Fixtures for the verifier-derived-disclosures spec tests.

The steps are hand-built ``StepResult.to_dict()`` dicts. ``ap_screen`` and ``ppe_screen`` carry
real values read from stored traces (``audit.query_audit``, ``xbrl_db``, read-only, 2026-11-11):
public SEC XBRL facts, see the spec's Acceptance criteria. The only fake is the verifier's LLM: a
scripted provider adapter behind the real ``LLMGateway`` (as in the citation-integrity spec).
"""

from __future__ import annotations

import copy
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

    The entries are the JSON list the user prompt embeds (``statements_json``); it is found
    as the list holding the first ``"index"`` key, whatever the template's surrounding words.
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


def _citation(fid: int, accession: str) -> dict:
    return {"fact_id": fid, "page_num": None, "html_anchor": None, "accession_no": accession,
            "section_path": "Statement - Consolidated Balance Sheets"}


def screen_row(ticker: str, cik: str, fid: int, value: float, rank: int, period_end: str,
               fy: int, label: str) -> dict:
    return {"cik": cik, "rank": rank, "unit": "USD", "label": label, "value": value,
            "ticker": ticker, "fact_id": str(fid), "citation": _citation(fid, "acc"),
            "period_end": period_end, "fiscal_year": fy, "pct_of_total": None}


AP_ENDS = {"AMZN": "2023-12-31", "WMT": "2023-01-31", "COST": "2023-09-03", "HD": "2024-01-28"}
AP_IDS = {"AMZN": 6971, "WMT": 205130, "COST": 208864, "HD": 211999}
AP_VALUES = {"AMZN": 84_981_000_000.0, "WMT": 53_742_000_000.0, "COST": 17_483_000_000.0,
             "HD": 10_037_000_000.0}
AP_CIK = {"AMZN": "0001018724", "WMT": "0000104169", "COST": "0000909832", "HD": "0000354950"}


def ap_screen() -> dict:
    """The retailers' fiscal 2023 accounts payable screen (stored F3a trace)."""
    order = ["AMZN", "WMT", "COST", "HD"]
    rows = [screen_row(t, AP_CIK[t], AP_IDS[t], AP_VALUES[t], i, AP_ENDS[t], 2023,
                       "AccountsPayable") for i, t in enumerate(order, start=1)]
    return {"op": "screen", "rows": rows, "unit": None, "error": None, "value": None,
            "status": "OK", "step_id": "ap_screen", "citation": None, "confidence": 0.0,
            "disclosure": {"ranked": list(order), "members": 4, "universe": "retailers",
                           "exclusions": [], "fiscal_year": 2023,
                           "period_ends": dict(AP_ENDS), "period_ends_differ": True},
            "linked_chunk_ids": []}


def wmt_lookup() -> dict:
    """Walmart's fiscal 2023 accounts payable lookup (same trace)."""
    return {"op": "lookup", "unit": "USD", "error": None, "value": 53_742_000_000.0,
            "status": "OK", "step_id": "wmt_ap23", "citation": _citation(205130, "acc"),
            "confidence": 0.9, "linked_chunk_ids": []}


PPE_RANKED = ["ADBE", "NFLX", "CSCO", "AMD", "AVGO", "QCOM", "NVDA", "LMT", "KO", "ABT", "TXN",
              "VST", "CEG", "PG", "LLY", "MRK", "MCD", "COST", "ORCL", "AAPL", "WMT", "MSFT",
              "AMZN"]
PPE_ENDS = {"ADBE": "2025-11-28", "NFLX": "2025-12-31", "CSCO": "2025-07-26", "AMD": "2025-12-27",
            "AVGO": "2025-11-02", "QCOM": "2025-09-28", "NVDA": "2025-01-26", "LMT": "2025-12-31",
            "KO": "2025-12-31", "ABT": "2025-12-31", "TXN": "2025-12-31", "VST": "2025-12-31",
            "CEG": "2025-12-31", "PG": "2025-06-30", "LLY": "2025-12-31", "MRK": "2025-12-31",
            "MCD": "2025-12-31", "COST": "2025-08-31", "ORCL": "2025-05-31", "AAPL": "2025-09-27",
            "WMT": "2025-01-31", "MSFT": "2025-06-30", "AMZN": "2025-12-31"}
PPE_EXCLUSIONS = [
    {"ticker": "CRM", "reason": "metric_not_tagged"},
    {"ticker": "GOOGL", "reason": "period_not_filed", "latest_fiscal_year": 2024},
    {"ticker": "HD", "reason": "period_not_filed", "latest_fiscal_year": 2024},
    {"ticker": "INTC", "reason": "period_not_filed", "latest_fiscal_year": 2022},
    {"ticker": "META", "reason": "metric_not_tagged"},
    {"ticker": "MU", "reason": "metric_not_tagged"},
    {"ticker": "TSLA", "reason": "period_not_filed", "latest_fiscal_year": 2024},
    {"ticker": "TSM", "reason": "currency", "currency": "TWD"},
    {"ticker": "UNP", "reason": "period_not_filed", "latest_fiscal_year": 2023},
    {"ticker": "UPS", "reason": "metric_not_tagged"},
]
PPE_ROWS = [("ADBE", "0000796343", 171776, 1_873_000_000.0),
            ("NFLX", "0001065280", 188452, 2_004_350_000.0),
            ("CSCO", "0000858877", 265954, 2_113_000_000.0)]


def ppe_screen() -> dict:
    """The covered companies' FY2025 PP&E bottom-3 screen (stored F3a/F7a traces)."""
    rows = [screen_row(t, cik, fid, v, i, PPE_ENDS[t], 2025, "PropertyPlantAndEquipment")
            for i, (t, cik, fid, v) in enumerate(PPE_ROWS, start=1)]
    return {"op": "screen", "rows": rows, "unit": None, "error": None, "value": None,
            "status": "OK", "step_id": "ppe_screen", "citation": None, "confidence": 0.0,
            "disclosure": {"ranked": list(PPE_RANKED), "members": 33, "universe": "covered",
                           "exclusions": copy.deepcopy(PPE_EXCLUSIONS), "fiscal_year": 2025,
                           "period_ends": dict(PPE_ENDS), "period_ends_differ": True},
            "linked_chunk_ids": []}


def detail(statement: str, fact_ids: list) -> dict:
    return {"statement": statement, "fact_ids": [str(f) for f in fact_ids], "chunk_ids": []}


def execution(steps: list[dict]) -> dict:
    return {"status": "OK", "steps": steps, "bindings": {}, "error": None}


def response(details: list[dict], steps: list[dict]) -> XbrlRagResponse:
    """A recorded synthesizer response (before the verifier)."""
    return XbrlRagResponse(
        query="recorded question", answer_summary="recorded summary.",
        answer_details=details, unanswered_parts=[], metadata={"fully_answered": True},
        plan={"steps": [{"id": s["step_id"], "op": s["op"]} for s in steps]},
        execution_result=execution(steps), narrative_chunks=[])


@pytest.fixture(scope="session")
def vd():
    """The builders and constants above (spec tests cannot import conftest)."""
    names = ("AP_ENDS", "AP_IDS", "AP_VALUES", "PPE_RANKED", "PPE_ENDS", "PPE_EXCLUSIONS",
             "PPE_ROWS", "ap_screen", "ppe_screen", "wmt_lookup", "screen_row", "detail",
             "execution", "response", "make_verifier", "sent_entries")
    g = globals()
    return types.SimpleNamespace(**{n: g[n] for n in names})
