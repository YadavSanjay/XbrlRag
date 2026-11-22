"""C5: each answer carries its own trace id, its LLM cost and its latency."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.closed_book import ClosedBookSystem
from src.eval.systems.naive_rag import NaiveRagSystem

ARMS = ["closed_book", "naive_rag_plain", "naive_rag_filtered"]


@pytest.fixture(scope="session")
def build_arm(make_gateway, make_embedder, make_qdrant, point, reply):
    """Factory: (system, adapter) for an arm whose fake provider reports the given tokens."""

    def make(arm: str, *, prompt_tokens: int = 1000, completion_tokens: int = 500):
        gateway, adapter = make_gateway(
            reply(7.5), prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )
        if arm == "closed_book":
            return ClosedBookSystem(gateway=gateway), adapter
        system = NaiveRagSystem(
            filtered=(arm == "naive_rag_filtered"),
            gateway=gateway,
            embedder=make_embedder(),
            vector_db_client=make_qdrant([point("chunk-1", "Revenue was 7.5 million dollars.")]),
        )
        return system, adapter

    return make


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("arm", ARMS)
def test_answer_carries_trace_cost_and_latency(build_arm, make_case, arm):
    # oracle: requirement 8 — a provider call was made, so cost is positive and the
    # answer has its own trace
    system, adapter = build_arm(arm)
    first = system.answer(make_case(case_id="a"))
    second = system.answer(make_case(case_id="b"))
    assert adapter.calls == 2
    for ans in (first, second):
        assert ans.error is None
        assert isinstance(ans.query_id, str) and ans.query_id != ""
        assert ans.cost_usd > 0
        assert ans.latency_ms >= 0
    assert first.query_id != second.query_id


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("arm", ARMS)
def test_cost_is_per_answer_not_cumulative(build_arm, make_case, arm):
    # oracle: requirement 8 — two identical calls on one gateway cost the same each;
    # a running total would double the second
    system, _ = build_arm(arm)
    first = system.answer(make_case(case_id="a"))
    second = system.answer(make_case(case_id="b"))
    assert first.cost_usd > 0
    # tolerance: identical token counts and price give the same float; 1e-9 guards summation
    assert second.cost_usd == pytest.approx(first.cost_usd, rel=1e-9)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(
    arm=st.sampled_from(ARMS),
    # arm-model-from-profile makes openai-6.1 the default profile, whose medium model is
    # priced higher above 272K prompt tokens: the doubled prompt stays at or below that,
    # where pricing is linear on every profile.
    prompt_tokens=st.integers(min_value=1, max_value=136_000),
    completion_tokens=st.integers(min_value=1, max_value=50_000),
)
def test_doubling_tokens_doubles_cost(build_arm, make_case, arm, prompt_tokens, completion_tokens):
    # oracle: pricing is linear in tokens and the fake reports no cached tokens, so
    # doubling prompt and completion tokens doubles cost_usd
    base_system, _ = build_arm(
        arm, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )
    double_system, _ = build_arm(
        arm, prompt_tokens=2 * prompt_tokens, completion_tokens=2 * completion_tokens
    )
    base = base_system.answer(make_case())
    double = double_system.answer(make_case())
    assert base.cost_usd > 0
    # tolerance: linear pricing in float arithmetic; 1e-9 relative covers rounding
    assert double.cost_usd == pytest.approx(2 * base.cost_usd, rel=1e-9)
    assert base.latency_ms >= 0 and double.latency_ms >= 0
    assert base.query_id and double.query_id and base.query_id != double.query_id
