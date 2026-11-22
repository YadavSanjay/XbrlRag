"""C6: a RunHalt keeps the partial run; a crash propagates (requirement 11)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import run_checkpointed
from src.eval.core.graders.base import Grade
from src.eval.core.runner import RunnerConfig

DETAIL = "3 consecutive LLM failures >= limit 3"  # what conftest's FakeSystem raises


def _go(kit, cases, system, store, cache_groups=None):
    return run_checkpointed(
        cases, [system], store=store, identities=[kit.identity_for(cases, system.name)],
        run_ids=["run-1"], config=RunnerConfig(max_workers=1, cache_groups=cache_groups),
        suite="spec_suite", suite_version="v1")


@pytest.mark.criterion("C6")
def test_a_halt_ends_the_run_aborted_with_its_results(kit):
    # oracle: requirement 11 — hand count: a, b graded before the halt at c
    cases = [kit.make_case(c) for c in "abcd"]
    store = kit.MemoryStore()
    (report,) = _go(kit, cases, kit.FakeSystem(halt_at="c"), store)
    assert report.status == "ABORTED"
    assert report.config["halt"] == {"type": "CircuitBreakerOpen", "detail": DETAIL}
    ids = {r.case.id for r in report.results}
    assert {"a", "b"} <= ids
    assert "c" not in ids
    assert all(r.grade.grade is not Grade.ERROR for r in report.results)
    assert store.stored_ids("run-1") == ids
    assert store.count("finish_run") == 1
    assert store.rows["run-1"]["status"] == "ABORTED"


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(n=st.integers(2, 6), at=st.integers(1, 5))
def test_a_halt_inside_a_cache_group_keeps_the_group_s_finished_cases(kit, n, at):
    # oracle: requirements 4 and 11 — one filing, one task: the cases before the halt are
    # stored and reported even though their task never finished
    cases = [kit.make_case(f"c{i}", ticker="AAA", fy=2024) for i in range(n)]
    halt = f"c{min(at, n - 1)}"
    before = {c.id for c in cases[: int(halt[1:])]}
    store = kit.MemoryStore()
    (report,) = _go(kit, cases, kit.FakeSystem(halt_at=halt), store, cache_groups="filing")
    assert {r.case.id for r in report.results} == before
    assert store.stored_ids("run-1") == before
    assert report.status == "ABORTED"


@pytest.mark.criterion("C6")
def test_a_halted_run_resumes_from_where_it_stopped(kit):
    # oracle: requirements 10-11 — the halting case has no row, so the resume answers it
    cases = [kit.make_case(c) for c in "abc"]
    store = kit.MemoryStore()
    _go(kit, cases, kit.FakeSystem(halt_at="b"), store)
    second = kit.FakeSystem()
    (report,) = run_checkpointed(
        cases, [second], store=store, identities=[kit.identity_for(cases, second.name)],
        resume="run-1", config=RunnerConfig(max_workers=1), suite="spec_suite",
        suite_version="v1")
    assert second.answered == ["b", "c"]
    assert report.status == "OK"
    assert report.metrics.n_correct == 3


@pytest.mark.criterion("C6")
def test_any_other_exception_propagates_and_the_row_is_not_ok(kit):
    # oracle: requirement 11 — a crash is not a halt
    cases = [kit.make_case(c) for c in "abc"]
    store = kit.MemoryStore()
    with pytest.raises(kit.Crash):
        _go(kit, cases, kit.FakeSystem(crash_at="b"), store)
    assert store.stored_ids("run-1") == {"a"}
    assert store.rows["run-1"]["status"] != "OK"
