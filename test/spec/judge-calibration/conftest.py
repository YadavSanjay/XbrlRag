"""Fakes and builders for the judge-calibration spec tests (docs/specs/judge-calibration.md).

The only fake is a provider adapter behind a real ``LLMGateway`` (the LLM provider
boundary). The judge, the calibration functions and the gateway are real. Fixtures are
session-scoped factories so hypothesis-driven tests can use them. Everything is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.eval.core.graders.judge import Claim, ClaimJudge
from src.eval.core.graders.judge_calibration import CalibrationAnswer
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

REPO_ROOT = Path(__file__).resolve().parents[3]
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")

# The development judge (Anthropic) and the development generator (DeepSeek).
JUDGE_MODEL = "claude-haiku-5-5"
GENERATOR_MODEL = "deepseek-flash"
PROMPT_TOKENS = 1000
COMPLETION_TOKENS = 200


def pricing_row(model: str) -> dict:
    """The model's row of configs/llm/pricing.yaml (USD per 1,000 tokens)."""
    with open(REPO_ROOT / "configs" / "llm" / "pricing.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)[model]


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


class FakeAdapter(ProviderAdapter):
    """Records each request and answers with a fixed reply (or raises *error*)."""

    def __init__(self, reply: str, *, error: BaseException | None = None) -> None:
        self.provider = "fake"
        self.reply = reply
        self.error = error
        self.params: list[dict] = []
        self.stages: list[str] = []
        self.calls = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.calls += 1
        self.params.append(params)
        if self.error is not None:
            raise self.error
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        self.stages.append(stage)
        return ParsedResponse(
            content=self.reply,
            finish_reason="stop",
            prompt_tokens=PROMPT_TOKENS,
            completion_tokens=COMPLETION_TOKENS,
            reasoning_tokens=0,
            cached_prompt_tokens=0,
        )


def judge_reply(verdicts: list[tuple[str, bool, bool]], covered: list[bool]) -> str:
    """The judge reply object (eval-judge requirement 3)."""
    return json.dumps(
        {
            "claims": [
                {"id": cid, "correct": correct, "supported": supported}
                for cid, correct, supported in verdicts
            ],
            "key_points": [{"index": i, "covered": c} for i, c in enumerate(covered)],
        }
    )


@pytest.fixture(scope="session")
def reply():
    """Factory: ``judge_reply(verdicts, covered)``."""
    return judge_reply


@pytest.fixture(scope="session")
def make_judge():
    """Factory: (ClaimJudge on a real gateway with a fake adapter, FakeAdapter)."""

    def make(
        reply_text: str = "{}",
        *,
        error: BaseException | None = None,
        run_budget: RunBudget | None = None,
        model: str = JUDGE_MODEL,
        generator_model: str = GENERATOR_MODEL,
    ):
        adapter = FakeAdapter(reply_text, error=error)
        gateway = LLMGateway(
            stage="eval_judge",
            cost_tracker=CostTracker(),
            rate_limiter=None,
            run_budget=run_budget if run_budget is not None else RunBudget(),
            max_attempts=1,
            sleep_fn=lambda _s: None,
            adapters={p: adapter for p in PROVIDERS},
        )
        judge = ClaimJudge(model=model, generator_model=generator_model, gateway=gateway)
        return judge, adapter

    return make


def make_answer(
    answer_id: str = "a1",
    *,
    claims: Any = (("s0", "Revenue grew.", ("Revenue grew 5%.",)),),
    key_points: Any = ("Revenue grew",),
    generator_model: str = GENERATOR_MODEL,
    question: str = "What happened to revenue at AAA?",
    reference_answer: str = "Revenue grew 5%.",
) -> CalibrationAnswer:
    """A dev calibration answer; claims as (claim_id, text, passages) tuples."""
    return CalibrationAnswer(
        answer_id=answer_id,
        case_id=f"case-{answer_id}",
        split="dev",
        generator_model=generator_model,
        question=question,
        reference_answer=reference_answer,
        key_points=list(key_points),
        claims=[Claim(cid, text, list(passages)) for cid, text, passages in claims],
    )


@pytest.fixture(scope="session")
def answer():
    """Factory: ``make_answer(...)``."""
    return make_answer


def label_keys(ans: CalibrationAnswer) -> list[tuple[str, str, str]]:
    """Requirement 3's label keys of one answer, in sheet order (requirement 4)."""
    keys = []
    for c in ans.claims:
        keys.append((ans.answer_id, "correct", c.claim_id))
        if list(c.cited_passages):
            keys.append((ans.answer_id, "supported", c.claim_id))
    for i in range(len(ans.key_points)):
        keys.append((ans.answer_id, "covered", f"kp{i}"))
    return keys


@pytest.fixture(scope="session")
def keys_of():
    """Factory: ``label_keys(answer)``."""
    return label_keys


@pytest.fixture(scope="session")
def env():
    """The constants and config readers above, as attributes (tests cannot import conftest)."""
    return SimpleNamespace(
        JUDGE_MODEL=JUDGE_MODEL,
        GENERATOR_MODEL=GENERATOR_MODEL,
        PROMPT_TOKENS=PROMPT_TOKENS,
        COMPLETION_TOKENS=COMPLETION_TOKENS,
        pricing_row=pricing_row,
        llm_yaml=llm_yaml,
    )
