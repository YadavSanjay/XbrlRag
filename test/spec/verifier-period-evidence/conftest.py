"""Fixtures for the verifier-period-evidence spec tests.

The steps are hand-built plan steps and ``StepResult.to_dict()`` dicts. ``ca_screen``,
``ca_median`` and Apple's FY2025 current-ratio steps carry real values read from the stored
2026-11-12 slice traces (``audit.query_audit``, ``xbrl_db``, read-only): public SEC XBRL facts,
see the spec's Acceptance criteria. The expected periods (``CA_PERIOD``, ``CUR_RATIO_PERIOD``,
``CA_MEDIAN_PERIOD``) are written by hand from Requirements 1-3: Apple's FY2025 balance-sheet
date is 2025-09-27, stored as 2025-09-28 (Arelle's next-midnight end).

The only fake is the verifier's LLM: a scripted provider adapter behind the real
``LLMGateway`` (the pattern of the verifier-derived-disclosures spec tests).
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


# --------------------------------------------------------------------------- expected periods
# Hand-written from Requirements 1-3 (never by running code).

# Apple FY2025 lookups: instants, stored end 2025-09-28, stated end 2025-09-27.
CA_PERIOD = {"fiscal_year": 2025, "fiscal_period": "FY", "ytd": False, "period_start": None,
             "period_end": "2025-09-27"}
CL_PERIOD = {"fiscal_year": 2025, "fiscal_period": "FY", "ytd": False, "period_start": None,
             "period_end": "2025-09-27"}
CUR_RATIO_PERIOD = {"inputs": [{"step_id": "ca", **CA_PERIOD}, {"step_id": "cl", **CL_PERIOD}],
                    "fiscal_years": [2025]}

# The semiconductors' FY2023 current-assets screen (stated period ends, as the rows hold them).
CA_ORDER = ["INTC", "NVDA", "QCOM", "MU", "AVGO", "AMD", "TXN"]
CA_ENDS = {"INTC": "2023-12-30", "NVDA": "2023-01-29", "QCOM": "2023-09-24", "MU": "2023-08-31",
           "AVGO": "2023-10-29", "AMD": "2023-12-30", "TXN": "2023-12-31"}
CA_IDS = {"INTC": 154795, "NVDA": 63024, "QCOM": 177873, "MU": 55674, "AVGO": 24737,
          "AMD": 19187, "TXN": 182004}
CA_VALUES = {"INTC": 43_269_000_000.0, "NVDA": 23_073_000_000.0, "QCOM": 22_464_000_000.0,
             "MU": 21_244_000_000.0, "AVGO": 20_847_000_000.0, "AMD": 16_768_000_000.0,
             "TXN": 15_122_000_000.0}
CA_CIK = {"INTC": "0000050863", "NVDA": "0001045810", "QCOM": "0000804328", "MU": "0000723125",
          "AVGO": "0001730168", "AMD": "0000002488", "TXN": "0000097476"}
CA_EXCLUSIONS = [{"ticker": "TSM", "reason": "currency", "currency": "TWD"}]
CA_MEDIAN_PERIOD = {"fiscal_year": 2023, "period_ends": dict(CA_ENDS), "period_ends_differ": True}


# --------------------------------------------------------------------------- recorded steps


def _citation(fid: int, section: str) -> dict:
    return {"fact_id": fid, "page_num": None, "html_anchor": None, "accession_no": "acc",
            "section_path": section}


def ca_plan_step() -> dict:
    """Apple's FY2025 total current assets lookup, as planned."""
    return {"id": "ca", "op": "lookup", "ticker": "AAPL", "metric": "AssetsCurrent", "fy": 2025}


def cl_plan_step() -> dict:
    """Apple's FY2025 total current liabilities lookup, as planned."""
    return {"id": "cl", "op": "lookup", "ticker": "AAPL", "metric": "LiabilitiesCurrent",
            "fy": 2025}


DEFAULT = "default"  # the step's recorded period
OMIT = "omit"  # no ``period`` key at all


def _with_period(step: dict, period: Any, default: dict) -> dict:
    """Sets ``step["period"]``: *default* for DEFAULT, nothing for OMIT, else *period*."""
    if period == DEFAULT:
        step["period"] = copy.deepcopy(default)
    elif period != OMIT:
        step["period"] = copy.deepcopy(period)
    return step


def lookup_step(step_id: str, fid: int, value: float) -> dict:
    """A lookup step dict without ``period``."""
    return {"op": "lookup", "unit": "USD", "error": None, "value": value, "status": "OK",
            "step_id": step_id,
            "citation": _citation(fid, "Statement - Consolidated Balance Sheets"),
            "confidence": 0.9, "linked_chunk_ids": []}


def ca_step(period: Any = DEFAULT) -> dict:
    """Apple's FY2025 total current assets (fact 14482)."""
    return _with_period(lookup_step("ca", 14482, 152_987_000_000.0), period, CA_PERIOD)


def cl_step(period: Any = DEFAULT) -> dict:
    """Apple's FY2025 total current liabilities (fact 14504)."""
    return _with_period(lookup_step("cl", 14504, 176_392_000_000.0), period, CL_PERIOD)


def cur_ratio_step(period: Any = DEFAULT) -> dict:
    """Apple's FY2025 current ratio, ca / cl."""
    step = {"op": "compute", "unit": None, "error": None,
            "value": 152_987_000_000.0 / 176_392_000_000.0, "status": "OK",
            "step_id": "cur_ratio", "citation": None, "confidence": 0.9,
            "linked_chunk_ids": [], "expr": "ca / cl"}
    return _with_period(step, period, CUR_RATIO_PERIOD)


def screen_row(ticker: str, rank: int) -> dict:
    fid = CA_IDS[ticker]
    return {"cik": CA_CIK[ticker], "rank": rank, "unit": "USD", "label": "AssetsCurrent",
            "value": CA_VALUES[ticker], "ticker": ticker, "fact_id": str(fid),
            "citation": _citation(fid, "Statement - Consolidated Balance Sheets"),
            "period_end": CA_ENDS[ticker], "fiscal_year": 2023, "pct_of_total": None}


def ca_screen() -> dict:
    """The semiconductors' FY2023 total current assets screen (stored slice trace)."""
    rows = [screen_row(t, i) for i, t in enumerate(CA_ORDER, start=1)]
    return {"op": "screen", "rows": rows, "unit": None, "error": None, "value": None,
            "status": "OK", "step_id": "ca_screen", "citation": None, "confidence": 0.0,
            "disclosure": {"ranked": list(CA_ORDER), "members": 8, "universe": "semiconductors",
                           "exclusions": copy.deepcopy(CA_EXCLUSIONS), "fiscal_year": 2023,
                           "period_ends": dict(CA_ENDS), "period_ends_differ": True},
            "linked_chunk_ids": []}


def ca_median_aggregate() -> dict:
    """``ca_median``'s ``aggregate`` dict (the median of ``ca_screen``'s rows)."""
    return {"fn": "median", "status": "OK", "value": 21_244_000_000.0, "unit": "USD", "n": 7,
            "members": list(CA_ORDER), "fact_ids": [str(CA_IDS[t]) for t in CA_ORDER],
            "middle": ["MU"], "universe": "semiconductors", "universe_members": 8,
            "exclusions": copy.deepcopy(CA_EXCLUSIONS), "period_ends": dict(CA_ENDS),
            "period_ends_differ": True, "error": None}


def ca_median_step(period: Any = DEFAULT) -> dict:
    """``ca_screen``'s median (the aggregate step)."""
    step = {"op": "aggregate", "unit": "USD", "error": None, "value": 21_244_000_000.0,
            "status": "OK", "step_id": "ca_median", "citation": None, "confidence": 0.9,
            "linked_chunk_ids": [], "aggregate": ca_median_aggregate()}
    return _with_period(step, period, CA_MEDIAN_PERIOD)


def detail(statement: str, fact_ids: list) -> dict:
    return {"statement": statement, "fact_ids": [str(f) for f in fact_ids], "chunk_ids": []}


def _plan_step(step: dict) -> dict:
    out = {"id": step["step_id"], "op": step["op"]}
    if step["op"] == "compute":
        out["expr"] = step.get("expr")
    if step["op"] == "aggregate":
        out.update(of="ca_screen", fn="median")
    return out


def response(details: list[dict], steps: list[dict]) -> XbrlRagResponse:
    """A recorded synthesizer response (before the verifier)."""
    return XbrlRagResponse(
        query="recorded question", answer_summary="recorded summary.",
        answer_details=details, unanswered_parts=[], metadata={"fully_answered": True},
        plan={"steps": [_plan_step(s) for s in steps]},
        execution_result={"status": "OK", "steps": steps, "bindings": {}, "error": None},
        narrative_chunks=[])


@pytest.fixture(scope="session")
def vpe():
    """The builders and constants above (spec tests cannot import conftest)."""
    names = ("CA_PERIOD", "CL_PERIOD", "CUR_RATIO_PERIOD", "CA_ORDER", "CA_ENDS", "CA_IDS",
             "CA_VALUES", "CA_EXCLUSIONS", "CA_MEDIAN_PERIOD", "DEFAULT", "OMIT", "ca_plan_step",
             "cl_plan_step",
             "lookup_step", "ca_step", "cl_step", "cur_ratio_step", "screen_row", "ca_screen",
             "ca_median_aggregate", "ca_median_step", "detail", "response", "make_verifier",
             "sent_entries")
    g = globals()
    return types.SimpleNamespace(**{n: copy.deepcopy(g[n]) if isinstance(g[n], (dict, list))
                                     else g[n] for n in names})
