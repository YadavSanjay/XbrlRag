"""C6: the eval runner makes its stop event the stop signal of every answer (requirement 3).

The real ``EvalRunner`` with two workers and two cases. The test system answers case B with a
real ``LLMGateway`` meeting Flex 429s; case A halts the run with ``CircuitBreakerOpen`` once
B is inside its first wait. The two threads are ordered with ``threading.Event`` waits that
have a bounded timeout; no wait is a retry sleep (those go to the recording ``sleep_fn``).
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.runner import EvalRunner, RunnerConfig
from src.eval.core.system import SystemAnswer
from src.llm.run_budget import CircuitBreakerOpen, RunHalt, RunStopped, run_stop_requested
from src.observability.trace_context import trace_query

TIMEOUT_S = 10.0


def _case(case_id: str) -> EvalCase:
    return EvalCase(id=case_id, question=f"question {case_id}", tags=["split:dev"])


class _System:
    """Case ``a`` halts the run once case ``b`` waits; case ``b`` calls the gateway."""

    name = "spec-system"

    def __init__(self, h: Any, flex_429s: int, *, halt_on_a: bool) -> None:
        self.adapter = h.ScriptAdapter([h.flex() for _ in range(flex_429s)], final="the reply")
        self.b_waiting = threading.Event()
        self.saw_stop: list[bool] = []
        self.outcomes: list[tuple[str, Any]] = []
        self.halt_on_a = halt_on_a
        self.sleeper = h.Sleeper({1: self._first_sleep} if halt_on_a else {})
        self.gateway = h.make_gateway(self.adapter, self.sleeper)
        self._call = h.call

    def _first_sleep(self) -> None:
        # B is inside its first wait: let A halt the run, then wait (bounded) for the
        # runner's stop to reach this thread's stop signal.
        self.b_waiting.set()
        stopped = False
        pause = threading.Event()
        for _ in range(int(TIMEOUT_S / 0.01)):
            if run_stop_requested():
                stopped = True
                break
            pause.wait(0.01)
        self.saw_stop.append(stopped)

    def answer(self, case: EvalCase) -> SystemAnswer:
        if case.id == "a":
            if not self.b_waiting.wait(TIMEOUT_S):
                raise AssertionError("case b never reached its Flex wait")
            raise CircuitBreakerOpen("tripped by case a")
        with trace_query(case.question):
            kind, value = self._call(self.gateway)
        self.outcomes.append((kind, value))
        if kind == "raised" and isinstance(value, RunHalt):
            raise value
        if kind == "raised":
            return SystemAnswer(error=repr(value))
        return SystemAnswer(text=value.content)


@pytest.mark.criterion("C6")
def test_a_halt_stops_another_case_waiting_on_flex(h, env_vars):
    # oracle: requirement 3 with requirement 6 — A's halt sets the run's stop; B, inside the
    # first 10 s slice of a 25 s wait, sees it before the next slice and raises RunStopped
    # without sending again; the run raises A's CircuitBreakerOpen
    system = _System(h, 3, halt_on_a=True)
    runner = EvalRunner(RunnerConfig(max_workers=2))
    with env_vars(**h.policy_env(15, 25, 300)):
        with pytest.raises(CircuitBreakerOpen, match="tripped by case a"):
            runner.run([_case("a"), _case("b")], system)
    assert system.saw_stop == [True]
    assert len(system.outcomes) == 1
    kind, value = system.outcomes[0]
    assert kind == "raised" and isinstance(value, RunStopped), value
    assert system.adapter.invoked == 1
    assert system.sleeper.sleeps == pytest.approx([10.0], abs=1e-9)  # one slice


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@pytest.mark.parametrize("flex_429s", [1, 3])
def test_runner_does_not_change_an_unhalted_answer(h, env_vars, flex_429s):
    # oracle: requirement 3 — with no halt, answering under the runner (inside its stop scope,
    # so sliced) gives the same reply, adapter calls and total wait as answering directly
    totals = []
    with env_vars(**h.policy_env(15, 25, 300)):
        direct = _System(h, flex_429s, halt_on_a=False)
        direct.answer(_case("b"))
        under_runner = _System(h, flex_429s, halt_on_a=False)
        report = EvalRunner(RunnerConfig(max_workers=2)).run([_case("b")], under_runner)
    for system in (direct, under_runner):
        assert len(system.outcomes) == 1
        kind, value = system.outcomes[0]
        assert kind == "ok", value
        totals.append((value.content, system.adapter.invoked, sum(system.sleeper.sleeps)))
    assert totals[0][0] == totals[1][0] == "the reply"
    assert totals[0][1] == totals[1][1] == flex_429s + 1
    assert totals[1][2] == pytest.approx(totals[0][2], abs=1e-6)  # float sums of slices
    waits = [min(300.0, 25.0 * 2 ** (k - 1)) for k in range(1, flex_429s + 1)]
    assert totals[0][2] == pytest.approx(sum(waits), abs=1e-9)  # closed-form waits
    assert len(report.results) == 1
    assert max(under_runner.sleeper.sleeps) <= 10.0 + 1e-9  # sliced: inside the runner's scope
    assert direct.sleeper.sleeps == pytest.approx(waits, abs=1e-9)  # one sleep per wait
