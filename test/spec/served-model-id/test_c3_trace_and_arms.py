"""C3: the trace collects served ids per requested id; the arms copy them onto answers.

Spec: docs/specs/served-model-id.md, requirements 3-4 and criterion C3.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.systems._stage_llm import answer_in_trace
from src.eval.systems.xbrlrag_full import XbrlRagFullSystem
from src.llm.gateway import LLMRefusalError
from src.observability.trace_context import current_trace, trace_query

M = "gpt-5-mini"
M2 = "gpt-5-nano"
S1 = "gpt-5-mini-2025-08-07"
S2 = "gpt-5-mini-2025-01-01"
MESSAGES = [{"role": "system", "content": "You are a test."},
            {"role": "user", "content": "Say ok."}]

STEP = st.tuples(
    st.sampled_from(["ok", "ok", "refusal", "error"]),
    st.sampled_from([M, M2]),
    st.one_of(st.none(), st.just(""), st.sampled_from([S1, S2, "snap-a", "Snap-B", " x "])),
)


def _expected(steps: list[tuple[str, str, str | None]]) -> dict[str, list[str]]:
    """Oracle: requested -> sorted distinct non-empty served ids of ok/refusal calls."""
    acc: dict[str, set[str]] = {}
    for kind, model, served in steps:
        if kind != "error" and served:
            acc.setdefault(model, set()).add(served)
    return {k: sorted(v) for k, v in acc.items()}


def _call_all(gateway, steps) -> None:
    for _kind, model, _served in steps:
        try:
            gateway.complete(messages=MESSAGES, model=model, stage="spec")
        except (LLMRefusalError, ValueError):
            pass


def _served(obj) -> dict:
    return getattr(obj, "served_models", {})


def _assert_independent(answer, trace) -> None:
    """Mutating one side's dict or lists leaves the other unchanged."""
    before_trace = {k: list(v) for k, v in _served(trace).items()}
    answer.served_models.setdefault(M, []).append("mutated-on-answer")
    answer.served_models["added-on-answer"] = ["x"]
    assert _served(trace) == before_trace
    before_answer = {k: list(v) for k, v in answer.served_models.items()}
    for v in trace.served_models.values():
        v.append("mutated-on-trace")
    trace.served_models["added-on-trace"] = ["y"]
    assert answer.served_models == before_answer


# ---------------------------------------------------------------------------- trace


@pytest.mark.criterion("C3")
def test_trace_collects_sorted_distinct_served_ids(scripted_adapter, make_gateway):
    # oracle: the configured sequence s1, s2, s1, None for requested m -> {m: sorted({s1, s2})}
    adapter = scripted_adapter([("ok", S1), ("ok", S2), ("ok", S1), ("ok", None)])
    gateway = make_gateway(adapter)
    with trace_query("q") as trace:
        for _ in range(4):
            gateway.complete(messages=MESSAGES, model=M, stage="spec")
    assert _served(trace) == {M: [S2, S1]}  # "...-01-01" sorts before "...-08-07"
    assert trace.llm_calls == 4  # existing totals unchanged


@pytest.mark.criterion("C3")
def test_trace_records_a_refusal_usage_served_id_but_not_an_error(scripted_adapter,
                                                                  make_gateway):
    # oracle: the configured sequence: ok s1, refusal s2 (recorded), error (nothing)
    gateway = make_gateway(scripted_adapter([("ok", S1), ("refusal", S2), ("error", None)]))
    with trace_query("q") as trace:
        _call_all(gateway, [("ok", M, S1), ("refusal", M, S2), ("error", M, None)])
    assert _served(trace) == {M: [S2, S1]}


@pytest.mark.criterion("C3")
@given(steps=st.lists(STEP, max_size=8))
def test_trace_served_models_is_the_per_requested_union(steps, scripted_adapter,
                                                        make_gateway):
    # oracle: the union computed in the test from the scripted (kind, requested, served)
    gateway = make_gateway(scripted_adapter([(k, s) for k, _m, s in steps]))
    with trace_query("q") as trace:
        _call_all(gateway, steps)
    assert "served_models" in vars(trace)
    assert trace.served_models == _expected(steps)


# ---------------------------------------------------------------------------- answer_in_trace


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("outcome", ["ok", "refusal", "error"])
def test_answer_in_trace_copies_served_models(outcome, scripted_adapter, make_gateway):
    # oracle: the configured sequence s1, s2, s1, None, then the outcome's last call
    # (a refusal whose usage reports s2, or a permanent error) -> {m: sorted({s1, s2})}
    steps = [("ok", S1), ("ok", S2), ("ok", S1), ("ok", None)]
    if outcome == "refusal":
        steps.append(("refusal", S2))
    elif outcome == "error":
        steps.append(("error", None))
    gateway = make_gateway(scripted_adapter(steps))
    seen = []

    def produce() -> SystemAnswer:
        seen.append(current_trace())
        for _ in steps:
            gateway.complete(messages=MESSAGES, model=M, stage="spec")
        return SystemAnswer(text="done")

    answer = answer_in_trace("question", produce)
    assert _served(answer) == {M: [S2, S1]}
    assert answer.refused is (outcome == "refusal")
    assert (answer.error is not None) is (outcome == "error")
    _assert_independent(answer, seen[0])


@pytest.mark.criterion("C3")
@given(steps=st.lists(STEP, max_size=8))
def test_answer_in_trace_carries_the_union_of_its_calls(steps, scripted_adapter,
                                                        make_gateway):
    # oracle: the union computed in the test from the scripted calls the trace saw
    # (calls stop at the first refusal or error, which ends produce())
    gateway = make_gateway(scripted_adapter([(k, s) for k, _m, s in steps]))
    made: list[tuple[str, str, str | None]] = []

    def produce() -> SystemAnswer:
        for step in steps:
            made.append(step)
            gateway.complete(messages=MESSAGES, model=step[1], stage="spec")
        return SystemAnswer(text="done")

    answer = answer_in_trace("question", produce)
    assert "served_models" in vars(answer)
    assert answer.served_models == _expected(made)


# ---------------------------------------------------------------------------- full system


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("outcome", ["ok", "refusal", "error"])
def test_full_system_copies_the_captured_trace_served_models(
        outcome, capturing_sink, scripted_adapter, make_gateway, fake_pipeline):
    # oracle: the configured sequence s1, s2, s1, None (plus a refusal reporting s2 for
    # the refusal case) the injected pipeline made under its own trace
    steps = [("ok", S1), ("ok", S2), ("ok", S1), ("ok", None)]
    if outcome == "refusal":
        steps.append(("refusal", S2))
    gateway = make_gateway(scripted_adapter(steps))
    pipeline = fake_pipeline(gateway, steps, [M], outcome=outcome)
    system = XbrlRagFullSystem(pipeline=pipeline, transient_retries=0, retry_backoff_s=0.0)
    case = EvalCase(id="c1", question="What was revenue?", kind="lookup", expected_value=1.0)
    answer = system.answer(case)
    assert _served(answer) == {M: [S2, S1]}
    assert answer.refused is (outcome == "refusal")
    assert (answer.error is not None) is (outcome == "error")
    _assert_independent(answer, pipeline.traces[0])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_full_system_answers_carry_only_their_own_trace(
        capturing_sink, scripted_adapter, make_gateway, fake_pipeline):
    # oracle: each answer's configured sequence: answering a second case with a
    # different script changes the dict to that script's union (no carry-over)
    case = EvalCase(id="c1", question="What was revenue?", kind="lookup", expected_value=1.0)
    first_steps = [("ok", S1), ("ok", None)]
    second_steps = [("ok", S2), ("ok", "snap-z")]
    first = XbrlRagFullSystem(
        pipeline=fake_pipeline(make_gateway(scripted_adapter(first_steps)), first_steps, [M]),
        transient_retries=0, retry_backoff_s=0.0).answer(case)
    second = XbrlRagFullSystem(
        pipeline=fake_pipeline(make_gateway(scripted_adapter(second_steps)), second_steps,
                               [M2]),
        transient_retries=0, retry_backoff_s=0.0).answer(case)
    assert _served(first) == {M: [S1]}
    assert _served(second) == {M2: [S2, "snap-z"]}
