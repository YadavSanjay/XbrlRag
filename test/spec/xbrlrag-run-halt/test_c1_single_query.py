"""C1: a halt at any stage of one query propagates out of the pipeline (requirements 1, 2).

The real pipeline answers a fast-path lookup in four LLM calls: decomposer, router,
synthesizer, verifier. Each halt is made by the real gateway: a ``RunBudget`` call cap (the
halt comes before the stage's call), a set ``RunStopScope`` event met at a Flex 429, or a Flex
trace cap below the first wait (both after the stage's first attempt).
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.llm.run_budget import (
    CircuitBreakerOpen,
    FlexWaitExceeded,
    RunBudget,
    RunBudgetExceeded,
    RunHalt,
    RunStopped,
    RunStopScope,
)

KINDS = {"budget": RunBudgetExceeded, "stop": RunStopped, "cap": FlexWaitExceeded}


def _halting_kit(h: Any, stage: str, kind: str) -> tuple[Any, contextlib.AbstractContextManager]:
    """A kit whose query halts at *stage* with *kind*, and the context to answer it in."""
    index = h.STAGES.index(stage)
    if kind == "budget":
        return h.Kit(budget=RunBudget(max_llm_calls=index)), contextlib.nullcontext()
    kit = h.Kit(scripts={stage: [h.flex()]})
    if kind == "stop":
        stop = threading.Event()
        stop.set()
        return kit, RunStopScope(stop)
    return kit, h.env_vars(**h.flex_cap(1.0))


def _generate(h: Any, kit: Any, context: contextlib.AbstractContextManager) -> tuple[str, Any]:
    """``("answer", response)`` or ``("raised", halt)`` for the kit's question."""
    with context:
        try:
            return "answer", kit.pipeline.generate(h.QUESTION)
        except RunHalt as halt:
            return "raised", halt


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_a_call_cap_halt_propagates_from_each_stage(h, k):
    # oracle: requirement 1; hand count — the cap k lets exactly the first k of the four calls
    # (decomposer, router, synthesizer, verifier) through, and the (k+1)-th halts
    kit = h.Kit(budget=RunBudget(max_llm_calls=k))
    with pytest.raises(RunBudgetExceeded):
        kit.pipeline.generate(h.QUESTION)
    assert kit.calls() == h.calls_through(k, at_stage=0)


@pytest.mark.criterion("C1")
def test_a_stop_at_the_decomposer_is_not_swallowed(h):
    # oracle: requirement 2 — the decomposer must let RunStopped through; the stop is met at the
    # decomposer's Flex 429, so it made one attempt and no later stage is called
    kit, context = _halting_kit(h, "decomposer", "stop")
    kind, value = _generate(h, kit, context)
    assert kind == "raised" and isinstance(value, RunStopped), value
    assert kit.calls() == h.calls_through(0, at_stage=1)


@pytest.mark.criterion("C1")
def test_a_flex_cap_halt_at_the_verifier_is_not_swallowed(h):
    # oracle: requirement 2 — the verifier is the last call; its halt must not become LLM_ERROR
    kit, context = _halting_kit(h, "verifier", "cap")
    kind, value = _generate(h, kit, context)
    assert kind == "raised" and isinstance(value, FlexWaitExceeded), value
    assert kit.calls() == h.calls_through(3, at_stage=1)
    assert kit.sleeper.sleeps == []  # the cap halt takes the place of the wait


@pytest.mark.criterion("C1")
def test_the_breaker_trips_after_the_decomposer_passes_a_failure_through(h):
    # oracle: requirement 2 — a non-halt decomposer error keeps its pass-through fallback (the
    # router is reached), and with a breaker limit of 1 the router's call halts
    kit = h.Kit(budget=RunBudget(max_consecutive_failures=1),
                scripts={"decomposer": [h.PermanentError("400 bad request")]})
    with pytest.raises(CircuitBreakerOpen):
        kit.pipeline.generate(h.QUESTION)
    assert kit.calls() == h.calls_through(1, at_stage=0)


@pytest.mark.criterion("C1")
def test_the_verifier_keeps_its_fallback_for_an_error_but_not_for_a_halt(h):
    # oracle: requirement 2 — an ordinary verifier failure is still LLM_ERROR on an answer; a
    # halt at the same call (the call cap at 3) propagates
    failing = h.Kit(scripts={"verifier": [h.PermanentError("400 bad request")] * 3})
    answer = failing.pipeline.generate(h.QUESTION)
    assert answer.metadata["faithfulness"]["status"] == "LLM_ERROR"
    assert h.SUMMARY in answer.answer_summary
    halting = h.Kit(budget=RunBudget(max_llm_calls=3))
    with pytest.raises(RunBudgetExceeded):
        halting.pipeline.generate(h.QUESTION)


@pytest.mark.criterion("C1")
def test_generate_from_decision_lets_a_halt_through(h):
    # oracle: requirement 1 — the decision is made outside any trace (no budget applies); the
    # query's own trace starts at 0 calls, so a cap of 0 halts its first (synthesizer) call
    kit = h.Kit(budget=RunBudget(max_llm_calls=0))
    decision = kit.pipeline.dispatcher.decide(h.QUESTION)
    with pytest.raises(RunBudgetExceeded):
        kit.pipeline.generate_from_decision(h.QUESTION, decision)
    assert kit.adapters["synthesizer"].invoked == 0
    assert kit.adapters["verifier"].invoked == 0


@pytest.mark.criterion("C1")
def test_the_raised_halt_keeps_its_class_and_message(h, sink):
    # oracle: requirement 1 — the caller gets the gateway's halt, not a wrapper: its exact class
    # and RunBudget.check's message; the trace records repr() of what passed through it
    kit = h.Kit(budget=RunBudget(max_llm_calls=2))
    with pytest.raises(RunBudgetExceeded) as info:
        kit.pipeline.generate(h.QUESTION)
    assert type(info.value) is RunBudgetExceeded
    assert str(info.value) == "2 LLM calls >= limit 2"  # RunBudget.check's own message
    assert [t.error for t in sink.traces] == [repr(info.value)]


@pytest.mark.criterion("C1")
@settings(max_examples=24)
@given(stage=st.sampled_from(("decomposer", "router", "synthesizer", "verifier")),
       kind=st.sampled_from(sorted(KINDS)))
def test_any_halt_at_any_stage_propagates(h, stage, kind):
    # oracle: requirements 1-2; hand count — earlier stages made one call each, the halting
    # stage none for a budget halt (checked before the call) and one for a halt met at its
    # Flex 429, later stages none
    kit, context = _halting_kit(h, stage, kind)
    outcome, value = _generate(h, kit, context)
    assert outcome == "raised" and type(value) is KINDS[kind], value
    index = h.STAGES.index(stage)
    assert kit.calls() == h.calls_through(index, at_stage=0 if kind == "budget" else 1)
    # the fact store is read once the router has planned, before the synthesizer
    assert len(kit.lookup.steps) == (1 if index >= 2 else 0)
