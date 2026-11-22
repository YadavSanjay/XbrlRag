"""The XBRL pipeline stages size their output budget from the model's configured ceiling.

A reasoning model spends its reasoning against the output budget. With a fixed 2,000 a
DeepSeek synthesizer or verifier call could end ``finish_reason=length`` with no content,
and the escalation rung, asked for the same 2,000, failed the same way. Each stage now
asks for the model's ``max_output_tokens`` from ``configs/llm/llm.yaml``, read per
attempt, so an escalated model gets its own (larger) ceiling; a cap always exists.

The provider is the fake: a reasoning model that needs a fixed number of output tokens
and, below that, returns an empty reply cut off at the cap. The gateway, the DeepSeek
adapter's request building and reply parsing, the escalation ladder and the stages are
real. Expected budgets come from ``llm.yaml`` as loaded by ``load_committed_settings``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.llm.config import SEC_TABLE_MODEL_CONFIGS
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.deepseek_adapter import DeepSeekAdapter
from src.llm.gateway.model_ref import (
    DEFAULT_STAGE_MAX_COMPLETION_TOKENS,
    resolve_model_ref,
    stage_call_params,
)
from src.llm.profiles import load_committed_settings
from src.llm.run_budget import RunBudget
from src.retriever.xbrl.intent_router import IntentRouter
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.query_decomposer import QueryDecomposer

_SETTINGS = load_committed_settings(profile="deepseek")
_MEDIUM = _SETTINGS.model_for_tier("medium")
_FRONTIER = _SETTINGS.model_for_tier("frontier")
_ANSWER = {"answer_summary": "Revenue was $10B.", "answer_details": [], "metadata": {}}


class _ReasoningClient:
    """A Chat Completions client for a reasoning model needing *need* output tokens."""

    def __init__(self, need: int) -> None:
        self.need = need
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **params: Any) -> SimpleNamespace:
        self.requests.append(params)
        cap = params["max_tokens"]
        done = cap >= self.need
        usage = SimpleNamespace(
            prompt_tokens=500, completion_tokens=self.need if done else cap,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=min(cap, self.need)),
            prompt_tokens_details=None,
        )
        message = SimpleNamespace(content=json.dumps(_ANSWER) if done else "")
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop" if done else "length",
                                     message=message)],
            usage=usage, model=params["model"],
        )


class _Adapter(DeepSeekAdapter):
    """The real DeepSeek adapter over a fake client: the network is the only fake."""

    def __init__(self, client: _ReasoningClient) -> None:
        super().__init__(api_key="sk-unit", base_url="https://deepseek.invalid")
        self._fake = client

    def get_client(self) -> Any:
        return self._fake


class _FakeExecutionResult:
    steps: list = []

    def to_dict(self) -> dict:
        return {"steps": []}


@pytest.fixture
def deepseek_env(monkeypatch):
    """The deepseek profile active, with a key so each stage builds."""
    monkeypatch.setenv("XBRLRAG_LLM_PROFILE", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-unit")
    monkeypatch.delenv("XBRLRAG_USE_REPLACEMENT_MODELS", raising=False)
    return monkeypatch


def _gateway(client: _ReasoningClient, tracker: CostTracker) -> LLMGateway:
    return LLMGateway(stage="unit", cost_tracker=tracker, run_budget=RunBudget(),
                      max_attempts=1, sleep_fn=lambda _s: None,
                      adapters={"deepseek": _Adapter(client)})


def _synthesizer(client: _ReasoningClient, tracker: CostTracker) -> XbrlRagSynthesizer:
    synth = XbrlRagSynthesizer()
    synth._llm_client = _gateway(client, tracker)
    return synth


# ---------------------------------------------------------------- stage_call_params


def test_a_configured_model_is_asked_for_its_ceiling():
    for profile in load_committed_settings().profile_names:
        settings = load_committed_settings(profile=profile)
        for tier in ("small", "medium", "frontier"):
            model = settings.model_for_tier(tier)
            ref = resolve_model_ref(model)
            own = SEC_TABLE_MODEL_CONFIGS.get(model, {}).get("max_completion_tokens")
            expected = own if own is not None else settings.max_output_tokens_for(model)
            budget = stage_call_params(ref.config, temperature=0.1)["max_completion_tokens"]
            assert budget == expected, (profile, tier, model)


def test_a_model_with_neither_budget_nor_ceiling_gets_the_bounded_default():
    assert stage_call_params({}, temperature=0.1) == {
        "max_completion_tokens": DEFAULT_STAGE_MAX_COMPLETION_TOKENS, "temperature": 0.1,
    }
    assert DEFAULT_STAGE_MAX_COMPLETION_TOKENS > 0


def test_the_openai_models_keep_their_own_budget():
    # SEC_TABLE_MODEL_CONFIGS wins over the ceiling: those calls are unchanged.
    for model, row in SEC_TABLE_MODEL_CONFIGS.items():
        if "max_completion_tokens" not in row:
            continue
        params = stage_call_params(resolve_model_ref(model).config, temperature=0.1)
        assert params["max_completion_tokens"] == row["max_completion_tokens"], model


# ---------------------------------------------------------------- every stage


@pytest.mark.parametrize("stage_cls", [
    IntentRouter, QueryDecomposer, PlanGenerator, XbrlRagSynthesizer, XbrlFaithfulnessVerifier,
])
def test_every_stage_sends_its_models_ceiling(deepseek_env, stage_cls):
    client = _ReasoningClient(need=10)
    stage = stage_cls()
    stage._llm_client = _gateway(client, CostTracker())
    stage._do_llm_call([{"role": "user", "content": [{"type": "text", "text": "q"}]}])
    model = client.requests[0]["model"]
    assert client.requests[0]["max_tokens"] == _SETTINGS.max_output_tokens_for(model)


# ---------------------------------------------------------------- the failure it fixes


def test_reasoning_beyond_the_old_cap_answers_on_the_first_model(deepseek_env):
    # Needs 3,000 output tokens: the old 2,000 cut it off on both rungs.
    client = _ReasoningClient(need=3000)
    resp = _synthesizer(client, CostTracker()).generate_answer(
        "What was revenue?", {"steps": []}, _FakeExecutionResult())
    assert resp.answer_summary == _ANSWER["answer_summary"]
    assert "synthesis_error" not in resp.metadata
    assert [r["model"] for r in client.requests] == [_MEDIUM]
    assert client.requests[0]["max_tokens"] == _SETTINGS.max_output_tokens_for(_MEDIUM)


def test_an_empty_length_reply_escalates_under_the_larger_ceiling(deepseek_env):
    medium_cap = _SETTINGS.max_output_tokens_for(_MEDIUM)
    frontier_cap = _SETTINGS.max_output_tokens_for(_FRONTIER)
    assert frontier_cap > medium_cap  # the premise: the escalation rung has more room
    client = _ReasoningClient(need=medium_cap + 1)
    tracker = CostTracker()
    resp = _synthesizer(client, tracker).generate_answer(
        "What was revenue?", {"steps": []}, _FakeExecutionResult())

    assert resp.answer_summary == _ANSWER["answer_summary"]
    assert "synthesis_error" not in resp.metadata
    assert [(r["model"], r["max_tokens"]) for r in client.requests] == [
        (_MEDIUM, medium_cap), (_FRONTIER, frontier_cap),
    ]
    # The cut-off reply was billed and is counted, as is the escalated one.
    per_model = tracker.get_summary()["per_model"]
    assert per_model[_MEDIUM]["completion_tokens"] == medium_cap
    assert per_model[_FRONTIER]["completion_tokens"] == medium_cap + 1
    assert per_model[_MEDIUM]["cost_usd"] > 0


def test_a_reply_past_every_ceiling_still_degrades_and_stays_bounded(deepseek_env):
    frontier_cap = _SETTINGS.max_output_tokens_for(_FRONTIER)
    client = _ReasoningClient(need=frontier_cap + 1)
    resp = _synthesizer(client, CostTracker()).generate_answer(
        "What was revenue?", {"steps": []}, _FakeExecutionResult())
    assert resp.metadata.get("fully_answered") is False
    assert resp.metadata["synthesis_error"]["type"] == "empty_content"
    assert all(r["max_tokens"] <= frontier_cap for r in client.requests)
    assert len(client.requests) == 2  # one per rung, never an unbounded retry
