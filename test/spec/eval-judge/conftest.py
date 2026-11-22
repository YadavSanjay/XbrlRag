"""Fakes and builders for the eval-judge spec tests (docs/specs/eval-judge.md).

The only fake is the provider adapter behind a real ``LLMGateway`` (the LLM provider
boundary). The judge, its aggregation, the gateway and the calibration functions are
real. Fixtures are session-scoped factories so hypothesis-driven tests can use them.
Everything is synthetic.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.eval.core.graders.judge import Claim, ClaimJudge
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")

# A cross-family pair: the judge is Anthropic, the generator OpenAI.
JUDGE_MODEL = "claude-opus-5-5"
GENERATOR_MODEL = "gpt-5.6-sol"


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
            prompt_tokens=1000,
            completion_tokens=200,
            reasoning_tokens=0,
            cached_prompt_tokens=0,
        )

    def _role_text(self, role: str, call: int) -> str:
        parts = []
        for m in self.params[call]["messages"]:
            if m.get("role") != role:
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in content
                )
            parts.append(str(content))
        return "\n".join(parts)

    def user_text(self, call: int = 0) -> str:
        return self._role_text("user", call)

    def system_text(self, call: int = 0) -> str:
        return self._role_text("system", call)


def judge_reply(verdicts: list[tuple[str, bool, bool]], covered: list[bool]) -> str:
    """The judge reply object (requirement 3): claims by id, key points by index."""
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
def claim():
    """Factory: a ``Claim`` (id, text, passages)."""

    def make(claim_id: str, text: str = "Revenue grew.", passages: Any = ("Revenue grew 5%.",)):
        return Claim(claim_id, text, list(passages))

    return make


@pytest.fixture(scope="session")
def make_judge():
    """Factory: (ClaimJudge on a real gateway, FakeAdapter)."""

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
