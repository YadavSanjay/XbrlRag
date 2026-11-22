"""C8: cost, latency, failures, RunHalt and per-filing serialization, for both arms."""

from __future__ import annotations

import threading

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.run_budget import RunBudget, RunHalt

A = "AAA/2024/10-K/canonical.htm"
B = "BBB/2024/10-K/canonical.htm"
ARMS = ["make_resolved", "make_multi"]


def _arm(request, fixture_name: str):
    return request.getfixturevalue(fixture_name)


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("arm_name", ARMS)
def test_cost_is_the_router_plus_the_answer(
    request, make_case, router_cost, answer_cost, arm_name
):
    # oracle: the conftest's closed form at the pricing table's rates
    arm, _, _, _, _ = _arm(request, arm_name)()
    answer = arm.answer(make_case())
    expected = router_cost() + answer_cost()
    # tolerance: float sum of a few rates
    assert answer.cost_usd == pytest.approx(expected, rel=1e-9)


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("arm_name", ARMS)
def test_each_answer_has_its_own_trace(request, make_case, arm_name):
    # oracle: requirement 16 — one trace per answer
    arm, _, _, _, _ = _arm(request, arm_name)()
    first, second = arm.answer(make_case()), arm.answer(make_case())
    assert first.query_id and second.query_id
    assert first.query_id != second.query_id
    assert first.latency_ms >= 0


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("arm_name", ARMS)
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"router_error": ValueError("router broke")}, id="router"),
        pytest.param({"resolver_error": ValueError("resolver broke")}, id="filing-resolver"),
        pytest.param({"store_error": ValueError("store broke")}, id="store"),
        pytest.param({"provider_error": ValueError("provider broke")}, id="provider"),
    ],
)
def test_a_failure_is_an_error_not_an_exclusion(request, make_case, arm_name, kwargs):
    # oracle: requirement 16 — a failed case is reported, and is not mistaken for a scope cut
    arm, _, _, _, _ = _arm(request, arm_name)(**kwargs)
    answer = arm.answer(make_case())
    assert answer.error
    assert not answer.error.startswith("excluded: ")
    assert answer.numeric_values == []


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("arm_name", ARMS)
def test_run_halt_propagates_from_the_router_call(request, make_case, arm_name):
    # oracle: requirement 16 and the repo's RAG rules — a budget of no calls stops the run
    arm, _, adapter, _, _ = _arm(request, arm_name)(router_budget=RunBudget(max_llm_calls=0))
    with pytest.raises(RunHalt):
        arm.answer(make_case())
    assert adapter.calls == 0


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("arm_name", ARMS)
def test_run_halt_propagates_from_the_answer_call(request, make_case, arm_name):
    # oracle: requirement 16 — the same, one call later
    arm, router_adapter, _, _, _ = _arm(request, arm_name)(run_budget=RunBudget(max_llm_calls=0))
    with pytest.raises(RunHalt):
        arm.answer(make_case())
    assert router_adapter.calls == 1


def _run_together(arm, cases) -> list:
    """Starts the answers together; a deadlocking arm fails the join, not the run."""
    start = threading.Barrier(len(cases))
    answers: list = [None] * len(cases)

    def work(i: int) -> None:
        start.wait(timeout=10)
        answers[i] = arm.answer(cases[i])

    threads = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(len(cases))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    return answers


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("arm_name", ARMS)
def test_calls_on_one_filing_set_never_overlap(
    request, make_case, doc, overlap_adapter, answer_reply, arm_name
):
    # oracle: requirements 5 and 9 — the second question reads the cache the first one wrote
    marker = "Zqmark AAA"
    adapter = overlap_adapter(answer_reply(), (marker,), overlap_wait_s=0.3)
    arm, _, _, _, _ = _arm(request, arm_name)(adapter=adapter, objects={A: doc(marker)})
    cases = [
        make_case(f"Question {i} about AAA in fiscal 2024?", case_id=f"c{i}") for i in range(3)
    ]
    answers = _run_together(arm, cases)
    assert all(a is not None and a.error is None for a in answers)
    assert adapter.calls == 3
    assert adapter.max_per_filing[marker] == 1


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("arm_name", ARMS)
def test_calls_on_different_filing_sets_run_together(
    request, make_case, doc, overlap_adapter, router_reply, answer_reply, arm_name
):
    # oracle: requirements 5 and 9 — only two calls in flight together can pass the barrier
    marks = ("Zqmark AAA", "Zqmark BBB")
    barrier = threading.Barrier(2, timeout=20)
    adapter = overlap_adapter(answer_reply(), marks, barrier=barrier)
    arm, _, _, _, _ = _arm(request, arm_name)(
        adapter=adapter,
        router_table={
            "about AAA": router_reply(["AAA"], ["fiscal 2024"]),
            "about BBB": router_reply(["BBB"], ["fiscal 2024"]),
        },
        keys={("AAA", 2024): A, ("BBB", 2024): B},
        objects={A: doc(marks[0]), B: doc(marks[1])},
    )
    answers = _run_together(
        arm,
        [make_case("Question about AAA?", case_id="a"),
         make_case("Question about BBB?", ticker="BBB", case_id="b")],
    )
    assert all(a is not None and a.error is None for a in answers)
    assert adapter.passed_barrier == 2


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(
    arm_index=st.integers(0, 1),
    prompt=st.integers(1, 5000),
    completion=st.integers(1, 2000),
    router_prompt=st.integers(1, 2000),
    router_completion=st.integers(1, 1000),
)
def test_doubling_every_token_doubles_the_cost(
    make_resolved, make_multi, make_case, router_cost, answer_cost, arm_index, prompt, completion,
    router_prompt, router_completion
):
    # oracle: pricing is linear in tokens and the fakes report no cached tokens; the first
    # value is also the closed form
    make = (make_resolved, make_multi)[arm_index]

    def cost(scale: int) -> float:
        arm, _, _, _, _ = make(
            prompt_tokens=prompt * scale, completion_tokens=completion * scale,
            router_prompt_tokens=router_prompt * scale,
            router_completion_tokens=router_completion * scale,
        )
        return arm.answer(make_case()).cost_usd

    single = cost(1)
    expected = router_cost(router_prompt, router_completion) + answer_cost(prompt, completion)
    # tolerance: float sum of a few rates
    assert single == pytest.approx(expected, rel=1e-9)
    assert cost(2) == pytest.approx(2 * single, rel=1e-9)
