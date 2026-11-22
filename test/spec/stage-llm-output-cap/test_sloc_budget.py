"""C1-C2: a StageLLM call leaves room for reasoning, within the model's own limits."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.systems._stage_llm import StageLLM
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")
STAGES = ("closed_book", "long_context", "long_context_multi", "html_rag")
# oracle: configs/llm/llm.yaml, models with no budget of their own and a ceiling of 8,000 or more
UNCAPPED = (
    "deepseek-flash",
    "deepseek-v4-pro",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
)
# oracle: configs/llm/llm.yaml ceilings and SEC_TABLE_MODEL_CONFIGS's own value, read by hand
EXPECTED = {
    **dict.fromkeys(UNCAPPED, 8000),
    "gpt-5.6-luna": 4000,
    "gpt-5.6-terra": 8000,
    "gpt-5.6-sol": 8000,
    "gpt-5-mini": 8000,
}


class _Adapter(ProviderAdapter):
    """The provider: the network boundary. Records each request and replies."""

    def __init__(self) -> None:
        self.provider = "fake"
        self.params: list[dict] = []

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.params.append(params)
        return "{}"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(
            content=raw,
            finish_reason="stop",
            prompt_tokens=100,
            completion_tokens=10,
            reasoning_tokens=0,
            cached_prompt_tokens=0,
        )


def _budget(model: str, stage: str = "html_rag", text: str = "question") -> tuple[int, str]:
    adapter = _Adapter()
    gateway = LLMGateway(
        stage=stage,
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=RunBudget(),
        max_attempts=1,
        sleep_fn=lambda _s: None,
        adapters={p: adapter for p in PROVIDERS},
    )
    reply = StageLLM(stage, gateway, model=model).complete(text)
    return adapter.params[0]["max_completion_tokens"], reply


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model", UNCAPPED)
def test_a_model_without_a_budget_gets_room_for_reasoning(model):
    # oracle: requirement 1, 8,000, the budget frontier_sec_tools already spends
    assert _budget(model)[0] == 8000


@pytest.mark.criterion("C1")
@given(
    model=st.sampled_from(UNCAPPED),
    stage=st.sampled_from(STAGES),
    text=st.text(alphabet="abc 123", min_size=1, max_size=30),
)
@settings(deadline=None)
def test_every_arm_takes_the_same_budget(model, stage, text):
    # oracle: requirement 4, no comparison between arms is a comparison of budgets
    assert _budget(model, stage, text) == (8000, "{}")


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5-mini"])
def test_ceilings_and_own_budgets_hold(model):
    # oracle: requirements 2 and 3; the first line is the case the base code gets wrong
    assert _budget("deepseek-flash")[0] == 8000
    assert _budget(model)[0] == EXPECTED[model]


@pytest.mark.criterion("C2")
@given(model=st.sampled_from(sorted(EXPECTED)), stage=st.sampled_from(STAGES))
@settings(deadline=None)
def test_any_model_gets_its_own_limit(model, stage):
    # oracle: requirements 2 and 3 over the table above; the last line is the base bug
    assert _budget(model, stage)[0] == EXPECTED[model]
    assert _budget("deepseek-flash", stage)[0] == 8000
