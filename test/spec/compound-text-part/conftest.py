"""Builders and fakes for the compound-text-part spec tests (docs/specs/compound-text-part.md).

Cases are hand-built dicts in hx-authoring's C7 built shape, fictional tickers: a compound
whose ranking is DEVA 300m > DEVB 200m over DEVA..DEVD (DEVD excluded, ``not_tagged``) and
whose dependent is a ``text`` part on DEVA FY2025 with two key points; and the same compound
with a two-year trend dependent (FY2024 250m, FY2025 300m) as the control.

Two fakes, both at I/O boundaries (as in judge-runner-wiring's tests): a provider adapter
behind a real ``LLMGateway`` (the LLM provider), answering the judge from the payload it
receives, and a system under test returning prepared ``SystemAnswer``s (the pipeline). The
graders, the judge, the runner and the wiring are real. Everything is synthetic.
"""

from __future__ import annotations

import copy
import json
import threading
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.graders.judge import ClaimJudge
from src.eval.core.system import SystemAnswer
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

M = 1e6
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
JUDGE_MODEL = "claude-opus-5-5"  # Anthropic judge; the run's generator profile is deepseek
GENERATOR_MODEL = "gpt-5.6-sol"
GENERATOR_PROFILE = "deepseek"

ROWS = [{"ticker": "DEVA", "value": 300 * M, "fact_id": 1},
        {"ticker": "DEVB", "value": 200 * M, "fact_id": 2}]
REVERSED = [{"ticker": "DEVB", "value": 200 * M, "fact_id": 2},
            {"ticker": "DEVA", "value": 300 * M, "fact_id": 1}]
DISCLOSED = "Among the 4 companies DEVA leads, then DEVB; DEVD could not be ranked."
SILENT = "DEVA leads, then DEVB."
QUESTION = "Synthetic compound question c01?"
REFERENCE = "DEVA names a single synthetic supplier as its main supply risk."
KEY_POINTS = ["a single supplier", "the main supply risk"]


def _common(cid: str, kind: str, subtype: str | None = None) -> dict[str, Any]:
    tags = ["split:external", kind] + ([subtype] if subtype else [])
    return {"id": f"hx-{cid}", "question": f"Synthetic {kind} question {cid}?",
            "suite": "heldout_x_v1", "origin": "handwritten:h_x", "tags": tags}


def ranking_fields() -> dict[str, Any]:
    return {"kind": "screen", "fy": 2025, "unit": "USD",
            "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
            "expected_exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
            "expected_rows": [{"entity": "DEVA", "value": 300 * M},
                              {"entity": "DEVB", "value": 200 * M}],
            "order_sensitive": True, "top_n": 2, "expected_fact_ids": [1, 2],
            "fact_id_classes": [[1, 11], [2]], "rel_tol": 1e-4}


def text_fields(reference: str = REFERENCE, key_points: list[str] | None = None,
                passages: list[dict] | None = None) -> dict[str, Any]:
    return {"kind": "narrative", "ticker": "DEVA", "fy": 2025, "expected_text": reference,
            "key_points": list(KEY_POINTS if key_points is None else key_points),
            "passages": list(passages if passages is not None else
                             [{"accession": "0000900101-26-000001",
                               "quote": "We rely on a single synthetic supplier."}])}


def trend_fields() -> dict[str, Any]:
    return {"kind": "breakdown", "ticker": "DEVA", "fy": 2025,
            "expected_rows": [{"entity": "FY2024", "value": 250 * M},
                              {"entity": "FY2025", "value": 300 * M}],
            "order_sensitive": False, "rel_tol": 1e-4,
            "expected_fact_ids": [34, 1], "fact_id_classes": [[34], [1, 11]]}


def compound(dependent: dict[str, Any] | None = None, cid: str = "c01",
             text_first: bool = False, ranking: bool = True) -> dict[str, Any]:
    """The H-X compound dict; ``dependent`` defaults to the text part."""
    rank = ranking_fields()
    parts = []
    if ranking:
        parts.append({"part": "ranking", **copy.deepcopy(rank)})
    parts.append({"part": "dependent", **(dependent if dependent is not None
                                          else text_fields())})
    if text_first:
        parts.reverse()
    return {**_common(cid, "compound"), **rank, "question": QUESTION, "parts": parts}


def standalone_text(cid: str = "e01") -> dict[str, Any]:
    return {**_common(cid, "text"), **text_fields()}


def screen(cid: str = "a01") -> dict[str, Any]:
    return {**_common(cid, "cross_filer", "screen_top"), **ranking_fields()}


def case(d: dict[str, Any]) -> EvalCase:
    return EvalCase.from_dict(d)


def answer(text: str = DISCLOSED, rows: list[dict] | None = None,
           values: list[float] | None = None, fact_ids: list[int] | None = None,
           refused: bool = False) -> SystemAnswer:
    return SystemAnswer(text=text, numeric_values=list(values or []),
                        rows=list(ROWS if rows is None else rows),
                        fact_ids=list([1, 2] if fact_ids is None else fact_ids),
                        refused=refused)


def payload_of(user_text: str) -> dict:
    """The JSON payload embedded in the judge's user message."""
    start = user_text.index("{")
    obj, _ = json.JSONDecoder().raw_decode(user_text, start)
    return obj


def covering_reply(payload: dict) -> str:
    """Every claim correct and supported, every key point covered."""
    return json.dumps({
        "claims": [{"id": c["id"], "correct": True, "supported": True}
                   for c in payload["claims"]],
        "key_points": [{"index": p["index"], "covered": True}
                       for p in payload["key_points"]],
    })


class PayloadAdapter(ProviderAdapter):
    """Answers each judge request from its payload and records the payloads."""

    def __init__(self) -> None:
        self.provider = "fake"
        self.payloads: list[dict] = []
        self._lock = threading.Lock()

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
        payload = payload_of("\n".join(parts))
        with self._lock:
            self.payloads.append(payload)
        return covering_reply(payload)

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=raw, finish_reason="stop", prompt_tokens=1000,
                              completion_tokens=200, reasoning_tokens=0,
                              cached_prompt_tokens=0)


def make_judge() -> tuple[ClaimJudge, PayloadAdapter]:
    adapter = PayloadAdapter()
    gateway = LLMGateway(stage="eval_judge", cost_tracker=CostTracker(), rate_limiter=None,
                         run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                         adapters={p: adapter for p in PROVIDERS})
    return ClaimJudge(model=JUDGE_MODEL, generator_model=GENERATOR_MODEL,
                      gateway=gateway), adapter


class FakeSystem:
    """A system under test answering from a prepared map by case id."""

    def __init__(self, answers: dict[str, SystemAnswer]) -> None:
        self.name = "fake"
        self._answers = answers
        self._lock = threading.Lock()

    def answer(self, case: EvalCase) -> SystemAnswer:
        prepared = self._answers.get(case.id)
        if prepared is None:
            return SystemAnswer(text="")
        return SystemAnswer(**{**prepared.__dict__})


class Kit:
    M = M
    ROWS = ROWS
    REVERSED = REVERSED
    DISCLOSED = DISCLOSED
    SILENT = SILENT
    QUESTION = QUESTION
    REFERENCE = REFERENCE
    KEY_POINTS = KEY_POINTS
    GENERATOR_PROFILE = GENERATOR_PROFILE
    compound = staticmethod(compound)
    text_fields = staticmethod(text_fields)
    trend_fields = staticmethod(trend_fields)
    standalone_text = staticmethod(standalone_text)
    screen = staticmethod(screen)
    case = staticmethod(case)
    answer = staticmethod(answer)
    make_judge = staticmethod(make_judge)
    FakeSystem = FakeSystem


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()
