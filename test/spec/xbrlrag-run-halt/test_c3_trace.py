"""C3: the halted query's trace records the halt and is persisted once (requirement 3)."""

from __future__ import annotations

import contextlib
import threading

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.llm.run_budget import RunBudget, RunBudgetExceeded, RunHalt, RunStopScope
from src.observability.trace_context import get_audit_sink, set_audit_sink


@pytest.mark.criterion("C3")
def test_the_persisted_trace_is_annotated_with_the_halt(h, sink):
    # oracle: requirement 3 — one trace, halted=True, halt_reason the class name, error the
    # repr of the halt the caller received; two calls were made before the cap of 2
    kit = h.Kit(budget=RunBudget(max_llm_calls=2))
    with pytest.raises(RunBudgetExceeded) as info:
        kit.pipeline.generate(h.QUESTION)
    assert len(sink.traces) == 1
    trace = sink.traces[0]
    assert trace.annotations.get("halted") is True
    assert trace.annotations.get("halt_reason") == "RunBudgetExceeded"
    assert trace.error == repr(info.value)
    assert trace.llm_calls == 2


@pytest.mark.criterion("C3")
@settings(max_examples=24)
@given(stage=st.sampled_from(("decomposer", "router", "synthesizer", "verifier")),
       kind=st.sampled_from(("budget", "stop", "cap")))
def test_any_halt_is_recorded_on_its_trace(h, stage, kind):
    # oracle: requirement 3 — whatever the stage and halt, the one persisted trace names the
    # raised halt's class and holds its repr
    index = h.STAGES.index(stage)
    context: contextlib.AbstractContextManager = contextlib.nullcontext()
    if kind == "budget":
        kit = h.Kit(budget=RunBudget(max_llm_calls=index))
    else:
        kit = h.Kit(scripts={stage: [h.flex()]})
        if kind == "stop":
            stop = threading.Event()
            stop.set()
            context = RunStopScope(stop)
        else:
            context = h.env_vars(**h.flex_cap(1.0))
    recording = h.RecordingSink()
    previous = get_audit_sink()
    set_audit_sink(recording)
    raised = None
    try:
        with context:
            try:
                kit.pipeline.generate(h.QUESTION)
            except RunHalt as halt:
                raised = halt
    finally:
        set_audit_sink(previous)
    assert raised is not None, "the halt did not propagate"
    assert len(recording.traces) == 1
    trace = recording.traces[0]
    assert trace.annotations.get("halted") is True
    assert trace.annotations.get("halt_reason") == type(raised).__name__
    assert trace.error == repr(raised)
