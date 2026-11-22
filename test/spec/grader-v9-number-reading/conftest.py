"""Builders and fakes for the grader-v9-number-reading spec tests
(docs/specs/grader-v9-number-reading.md).

Cases are fictional records in the shape the v2 builder emits (tickers ``DEVA`` ... ``DEVZ``,
round amounts, the "retailers" group); answers are hand-built SystemAnswers with a citation,
so only the rule under test decides the grade.

- ``NOTE``: ``year_length_disclosure``'s note for AAPL FY2024 against FY2023 (period-disclosure
  C3), copied by hand.
- ``SCREEN_PART``: period-year-length-answers C5's screen sentence, copied by hand.
- ``E`` / ``R``: the synthesizer's two fixed fallback summaries (grader-synthesis-error
  requirement 1).

Two fakes, both at I/O boundaries (as judge-runner-wiring's): a provider adapter behind a real
``LLMGateway`` that answers the judge from its JSON payload, and a system under test that
returns prepared answers. The judge, the runner and the graders are real. Fixtures are
session-scoped so hypothesis tests can use them.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.eval.core.case import EvalCase
from src.eval.core.graders.judge import ClaimJudge
from src.eval.core.system import SystemAnswer
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

REPO_ROOT = Path(__file__).resolve().parents[3]
M = 1e6

NOTE = ("The compared fiscal periods differ in length (one is a 53-week year): 2023-10-01 to "
        "2024-09-28 has 364 days and 2022-09-25 to 2023-09-30 has 371 days; the change is on "
        "the reported basis, not adjusted for the different number of days.")
SCREEN_PART = ("Growth compares fiscal years of different lengths: AAPL 2023-10-01 to "
               "2024-09-28 (364 days) against 2022-09-25 to 2023-09-30 (371 days), one is a "
               "53-week year. Growth is on the reported basis, not adjusted for the different "
               "number of days.")
E = ("This answer could not be completed: the synthesis step returned a response that was not "
     "valid JSON. The retrieved facts are preserved in execution_result; please retry the "
     "query.")
R = ("This answer could not be completed: the model declined to answer the synthesis step. "
     "The retrieved facts are preserved in execution_result.")

MEMBERS = [("DEVA", 500 * M), ("DEVB", 400 * M), ("DEVS", 300 * M), ("DEVC", 200 * M),
           ("DEVD", 100 * M)]


def _tags(shape: str) -> list[str]:
    return ["split:dev", "intent:int-g9", "family:formal", "key_source:sec_companyfacts",
            "key_status:verified", f"shape:{shape}"]


def _key(values: list[float]) -> dict[str, Any]:
    return {"definition": None, "disagreements": [],
            "inputs": [{"accession": "0000000000-25-000001", "fiscal_year": 2025,
                        "form": "10-K", "period_end": "2025-12-31", "unit": "USD",
                        "value": v} for v in values],
            "policies": ["S1", "S2", "S3", "S5", "S6"], "same_filing": None,
            "source": "sec_companyfacts", "status": "verified"}


def _disclosures(members: list[tuple[str, float]]) -> dict[str, Any]:
    ranked = [t for t, _ in members]
    return {"universe": "retailers", "members": len(ranked), "ranked": ranked,
            "excluded": [], "period_ends": {t: "2025-12-31" for t in ranked},
            "disclose_period_ends": False}


def _row(ticker: str, value: float) -> dict[str, Any]:
    return {"ticker": ticker, "value": value, "fiscal_year": 2025, "period_end": "2025-12-31"}


def _record(shape: str, kind: str, **fields: Any) -> EvalCase:
    base: dict[str, Any] = {
        "id": f"v2-g9-{shape}", "suite": "xbrl_v2_dev", "kind": kind,
        "origin": f"v2:{shape}", "fy": 2025, "unit": "USD", "rel_tol": 1e-4,
        "question": f"A {shape} question about the retailers?", "tags": _tags(shape)}
    base.update(fields)
    return EvalCase.from_dict(base)


def answer(text: str, values: list[float] | None = None,
           rows: list[tuple[str, float]] | None = None, *, refused: bool = False,
           hedged: bool = False) -> SystemAnswer:
    """A cited answer; ``values`` default to the rows' values."""
    row_dicts = [{"ticker": t, "value": float(v)} for t, v in rows or []]
    nums = [float(v) for v in values] if values is not None else [r["value"] for r in row_dicts]
    return SystemAnswer(text=text, numeric_values=nums, rows=row_dicts, fact_ids=[1],
                        refused=refused, hedged=hedged)


def lookup_case(expected: float = 500 * M) -> EvalCase:
    return _record("lookup", "lookup", ticker="DEVA", expected_value=expected,
                   concept="us-gaap:Revenues")


def sum_case() -> EvalCase:
    members = MEMBERS[:4]
    return _record("screen.sum", "compute", expected_value=sum(v for _, v in members),
                   disclosures=_disclosures(members), key=_key([v for _, v in members]))


def peer_case(subject: str = "DEVS", rank: int = 3) -> EvalCase:
    return _record("peer_relative", "screen",
                   question=f"Where does {subject} rank among the retailers?",
                   expected_rows=[_row(t, v) for t, v in MEMBERS],
                   disclosures=_disclosures(MEMBERS),
                   subject={"ticker": subject, "rank": rank, "universe_median": 300 * M,
                            "position": "at"},
                   key=_key([v for _, v in MEMBERS]))


class Kit:
    M = M
    NOTE = NOTE
    SCREEN_PART = SCREEN_PART
    E = E
    R = R
    MEMBERS = MEMBERS
    answer = staticmethod(answer)
    lookup_case = staticmethod(lookup_case)
    sum_case = staticmethod(sum_case)
    peer_case = staticmethod(peer_case)


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()


# --- the judge in a run (as judge-runner-wiring's conftest) ---------------------------------

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
JUDGE_MODEL = "claude-opus-5-5"
GENERATOR_MODEL = "gpt-5.6-sol"
PROMPT_TOKENS = 1000
COMPLETION_TOKENS = 200


def judge_call_cost(model: str = JUDGE_MODEL) -> float:
    """One fake judge call's USD cost from configs/llm/pricing.yaml (USD per 1,000 tokens)."""
    with open(REPO_ROOT / "configs" / "llm" / "pricing.yaml", encoding="utf-8") as f:
        rates = yaml.safe_load(f)[model]
    return PROMPT_TOKENS / 1000 * rates["input"] + COMPLETION_TOKENS / 1000 * rates["output"]


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


def _payload_of(user_text: str) -> dict:
    start = user_text.index("{")
    obj, _ = json.JSONDecoder().raw_decode(user_text, start)
    return obj


def _all_right(payload: dict) -> str:
    claims = [{"id": c["id"], "correct": True, "supported": True} for c in payload["claims"]]
    points = [{"index": p["index"], "covered": True} for p in payload["key_points"]]
    return json.dumps({"claims": claims, "key_points": points})


class PayloadAdapter(ProviderAdapter):
    """Answers each judge request from its payload; records every call."""

    def __init__(self, responder: Callable[[dict], str]) -> None:
        self.provider = "fake"
        self.responder = responder
        self.payloads: list[dict] = []
        self._lock = threading.Lock()

    @property
    def calls(self) -> int:
        with self._lock:
            return len(self.payloads)

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        parts = []
        for m in params["messages"]:
            if m.get("role") != "user":
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in content
                )
            parts.append(str(content))
        payload = _payload_of("\n".join(parts))
        with self._lock:
            self.payloads.append(payload)
        return self.responder(payload)

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(
            content=raw, finish_reason="stop", prompt_tokens=PROMPT_TOKENS,
            completion_tokens=COMPLETION_TOKENS, reasoning_tokens=0, cached_prompt_tokens=0,
        )


@pytest.fixture(scope="session")
def make_judge():
    """Factory: (ClaimJudge on a real gateway, PayloadAdapter)."""

    def make():
        adapter = PayloadAdapter(_all_right)
        gateway = LLMGateway(
            stage="eval_judge", cost_tracker=CostTracker(), rate_limiter=None,
            run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
            adapters={p: adapter for p in PROVIDERS},
        )
        judge = ClaimJudge(model=JUDGE_MODEL, generator_model=GENERATOR_MODEL,
                           gateway=gateway)
        return judge, adapter

    return make


@pytest.fixture(scope="session")
def unit_judge_cost() -> float:
    return judge_call_cost()


class FakeSystem:
    """A system under test answering from a prepared map."""

    def __init__(self, answers: dict[str, SystemAnswer], name: str = "fake") -> None:
        self.name = name
        self._answers = answers

    def answer(self, case: EvalCase) -> SystemAnswer:
        prepared = self._answers.get(case.id) or SystemAnswer(text="")
        return SystemAnswer(**{**prepared.__dict__})


@pytest.fixture(scope="session")
def fake_system():
    return FakeSystem


@pytest.fixture(scope="session")
def text_case():
    """Factory: a text-graded narrative case through ``EvalCase.from_dict``."""

    def make(case_id: str) -> EvalCase:
        return EvalCase.from_dict({"id": case_id, "question": f"Why did {case_id} change?",
                                   "kind": "narrative", "expected_text": "Reference.",
                                   "key_points": ["p0", "p1"]})

    return make
