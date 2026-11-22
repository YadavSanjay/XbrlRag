"""Fakes and builders for the judge-runner-wiring spec tests (docs/specs/judge-runner-wiring.md).

Two fakes, both at I/O boundaries: a provider adapter behind a real ``LLMGateway`` (the LLM
provider), which answers the judge from the JSON payload it receives, and a system under
test that returns prepared ``SystemAnswer``s (the pipeline). The judge, the runner, the
graders and the checks are real. Fixtures are session-scoped factories so
hypothesis-driven tests can use them. Everything is synthetic.
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
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")

# A cross-family pair: the judge is Anthropic, the generator OpenAI.
JUDGE_MODEL = "claude-opus-5-5"
GENERATOR_MODEL = "gpt-5.6-sol"
JUDGE_VERSION = "v1:claude-opus-5-5"
# The fake provider's usage per call.
PROMPT_TOKENS = 1000
COMPLETION_TOKENS = 200


def judge_call_cost(model: str = JUDGE_MODEL) -> float:
    """One fake judge call's USD cost from configs/llm/pricing.yaml (USD per 1,000 tokens)."""
    with open(REPO_ROOT / "configs" / "llm" / "pricing.yaml", encoding="utf-8") as f:
        rates = yaml.safe_load(f)[model]
    return PROMPT_TOKENS / 1000 * rates["input"] + COMPLETION_TOKENS / 1000 * rates["output"]


def llm_yaml() -> dict:
    """The committed configs/llm/llm.yaml."""
    with open(REPO_ROOT / "configs" / "llm" / "llm.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


def payload_of(user_text: str) -> dict:
    """The JSON payload embedded in the judge's user message."""
    start = user_text.index("{")
    obj, _ = json.JSONDecoder().raw_decode(user_text, start)
    return obj


def rule_reply(
    claim_rule: Callable[[dict], tuple[bool, bool]] | None = None,
    point_rule: Callable[[int], bool] | None = None,
) -> Callable[[dict], str]:
    """A responder: each claim's (correct, supported) and each key point's coverage by rule.

    Defaults: every claim correct and supported, every key point covered.
    """

    def respond(payload: dict) -> str:
        claims = []
        for c in payload["claims"]:
            correct, supported = claim_rule(c) if claim_rule else (True, True)
            claims.append({"id": c["id"], "correct": correct, "supported": supported})
        points = [
            {"index": p["index"], "covered": point_rule(p["index"]) if point_rule else True}
            for p in payload["key_points"]
        ]
        return json.dumps({"claims": claims, "key_points": points})

    return respond


@pytest.fixture(scope="session")
def rule():
    """Factory: ``rule_reply(claim_rule, point_rule)``."""
    return rule_reply


@pytest.fixture(scope="session")
def unit_judge_cost():
    """One fake judge call's cost on the default judge model (pricing.yaml)."""
    return judge_call_cost()


@pytest.fixture(scope="session")
def llm_config():
    """The committed configs/llm/llm.yaml, as a dict."""
    return llm_yaml()


class PayloadAdapter(ProviderAdapter):
    """Answers each request from its payload via *responder* (or raises *error*)."""

    def __init__(
        self,
        responder: Callable[[dict], str],
        *,
        error: BaseException | None = None,
    ) -> None:
        self.provider = "fake"
        self.responder = responder
        self.error = error
        self.payloads: list[dict] = []
        self._pending: list[str] = []
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
        payload = payload_of("\n".join(parts))
        with self._lock:
            self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.responder(payload)

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(
            content=raw,
            finish_reason="stop",
            prompt_tokens=PROMPT_TOKENS,
            completion_tokens=COMPLETION_TOKENS,
            reasoning_tokens=0,
            cached_prompt_tokens=0,
        )


@pytest.fixture(scope="session")
def make_judge():
    """Factory: (ClaimJudge on a real gateway, PayloadAdapter)."""

    def make(
        responder: Callable[[dict], str] | None = None,
        *,
        error: BaseException | None = None,
        model: str = JUDGE_MODEL,
        generator_model: str = GENERATOR_MODEL,
    ):
        adapter = PayloadAdapter(responder or rule_reply(), error=error)
        gateway = LLMGateway(
            stage="eval_judge",
            cost_tracker=CostTracker(),
            rate_limiter=None,
            run_budget=RunBudget(),
            max_attempts=1,
            sleep_fn=lambda _s: None,
            adapters={p: adapter for p in PROVIDERS},
        )
        judge = ClaimJudge(model=model, generator_model=generator_model, gateway=gateway)
        return judge, adapter

    return make


class FakeSystem:
    """A system under test answering from a prepared map (or raising per case id)."""

    def __init__(
        self,
        answers: dict[str, SystemAnswer] | None = None,
        *,
        raises: dict[str, BaseException] | None = None,
        name: str = "fake",
    ) -> None:
        self.name = name
        self._answers = answers or {}
        self._raises = raises or {}
        self.answered: list[str] = []
        self._lock = threading.Lock()

    def answer(self, case: EvalCase) -> SystemAnswer:
        with self._lock:
            self.answered.append(case.id)
        if case.id in self._raises:
            raise self._raises[case.id]
        prepared = self._answers.get(case.id)
        if prepared is None:
            return SystemAnswer(text="")
        # A fresh copy per call, so the runner's own edits never leak between runs.
        return SystemAnswer(**{**prepared.__dict__})


@pytest.fixture(scope="session")
def fake_system():
    """Factory: ``FakeSystem(answers, raises=...)``."""
    return FakeSystem


@pytest.fixture(scope="session")
def make_case():
    """Factory: an ``EvalCase`` through ``EvalCase.from_dict`` (the suite JSON path)."""

    def make(case_id: str, **fields: Any) -> EvalCase:
        d = {"id": case_id, "question": fields.pop("question", f"Question {case_id}?")}
        d.update(fields)
        return EvalCase.from_dict(d)

    return make


def response(
    *,
    details: list | None = None,
    steps: list | None = None,
    chunks: list | None = None,
    plan_steps: list | None = None,
) -> dict:
    """An ``XbrlRagResponse``-shaped dict."""
    out: dict[str, Any] = {
        "query": "q",
        "answer_summary": "summary",
        "plan": {"steps": list(plan_steps or [])},
        "execution_result": {"status": "OK", "steps": list(steps or []), "bindings": {},
                             "error": None},
        "narrative_chunks": list(chunks or []),
    }
    if details is not None:
        out["answer_details"] = details
    return out


@pytest.fixture(scope="session")
def make_response():
    """Factory: ``response(details=..., steps=..., chunks=..., plan_steps=...)``."""
    return response
