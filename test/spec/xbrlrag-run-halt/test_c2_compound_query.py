"""C2: a halt in a compound query's later sub-query propagates (requirement 1).

The decomposer splits the question into two independent sub-queries, each a fast-path lookup,
so the query makes seven LLM calls in order: decomposer; router, synthesizer, verifier of
sub-query 1; the same three of sub-query 2.
"""

from __future__ import annotations

import threading

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.llm.run_budget import RunBudget, RunBudgetExceeded, RunHalt, RunStopped, RunStopScope

SUB_1 = "What was Apple's revenue in fiscal 2023?"
SUB_2 = "What was Apple's revenue in fiscal 2023 as reported?"
COMPOUND = f"{SUB_1} And {SUB_2}"
N_CALLS = 7  # 1 decomposer + 2 x (router, synthesizer, verifier)


def _kit(h, **kwargs):
    return h.Kit(decomposer=h.decomposer_reply(SUB_1, SUB_2), **kwargs)


def _calls_for(k: int) -> dict[str, int]:
    """Adapter calls per stage after the first *k* of the seven calls (hand count)."""
    order = ["decomposer", "router", "synthesizer", "verifier",
             "router", "synthesizer", "verifier"]
    out = {"decomposer": 0, "router": 0, "plan_generator": 0, "synthesizer": 0, "verifier": 0}
    for stage in order[:k]:
        out[stage] += 1
    return out


@pytest.mark.criterion("C2")
def test_a_halt_at_the_second_sub_query_discards_the_first_answer(h):
    # oracle: requirement 1 — four calls (decomposer + sub-query 1's three) are allowed; the
    # fifth, sub-query 2's router, halts and no merged answer is returned
    kit = _kit(h, budget=RunBudget(max_llm_calls=4))
    with pytest.raises(RunBudgetExceeded):
        kit.pipeline.generate(COMPOUND)
    assert kit.calls() == _calls_for(4)


@pytest.mark.criterion("C2")
def test_a_stop_at_the_second_synthesizer_propagates(h):
    # oracle: requirement 1 — sub-query 1's synthesizer answers, sub-query 2's meets a Flex 429
    # with the run's stop set, so RunStopped is raised there and the verifier is not called again
    kit = _kit(h, scripts={"synthesizer": [h.SYNTHESIZER_REPLY, h.flex()]})
    stop = threading.Event()
    stop.set()
    with RunStopScope(stop):
        with pytest.raises(RunStopped):
            kit.pipeline.generate(COMPOUND)
    assert kit.calls() == {"decomposer": 1, "router": 2, "plan_generator": 0,
                           "synthesizer": 2, "verifier": 1}


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_without_a_halt_the_merged_answer_has_both_parts_and_with_one_it_has_none(h):
    # oracle: requirement 1 — the merge is unchanged for an unhalted query (both sub-answers,
    # decomposed); a halt at sub-query 2 returns nothing, never the merge of sub-query 1 alone
    merged = _kit(h).pipeline.generate(COMPOUND)
    assert merged.metadata["decomposed"] is True
    assert merged.answer_summary.count(h.SUMMARY) == 2
    halted = _kit(h, budget=RunBudget(max_llm_calls=5))
    with pytest.raises(RunBudgetExceeded):
        halted.pipeline.generate(COMPOUND)


@pytest.mark.criterion("C2")
@settings(max_examples=20)
@given(k=st.integers(0, N_CALLS + 2))
def test_any_call_cap_below_seven_halts_the_compound_query(h, k):
    # oracle: requirement 1; hand count — below 7 the (k+1)-th call halts after k calls; at 7 or
    # more every call is made and the answer equals the unbudgeted one
    kit = _kit(h, budget=RunBudget(max_llm_calls=k))
    try:
        outcome, value = "answer", kit.pipeline.generate(COMPOUND)
    except RunHalt as halt:
        outcome, value = "raised", halt
    if k < N_CALLS:
        assert outcome == "raised" and type(value) is RunBudgetExceeded, value
        assert kit.calls() == _calls_for(k)
    else:
        reference = _kit(h).pipeline.generate(COMPOUND)
        assert outcome == "answer", value
        assert value.answer_summary == reference.answer_summary
        assert value.answer_details == reference.answer_details
        assert kit.calls() == _calls_for(N_CALLS)
