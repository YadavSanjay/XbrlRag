"""C5: an interrupted run, resumed, equals one uninterrupted run (requirements 8-10)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import ResumeRefused, run_checkpointed
from src.eval.core.runner import RunnerConfig


def _go(kit, cases, system, store, *, resume=None, run_ids=None, max_cost_usd=None,
        identity=None):
    return run_checkpointed(
        cases, [system], store=store,
        identities=[identity or kit.identity_for(cases, system.name)],
        resume=resume, run_ids=run_ids,
        config=RunnerConfig(max_workers=1, max_cost_usd=max_cost_usd),
        suite="spec_suite", suite_version="v1")


def _interrupt(kit, cases, crash_at, store, **system_kwargs):
    """The first segment, ended by a crash at *crash_at*."""
    with pytest.raises(kit.Crash):
        _go(kit, cases, kit.FakeSystem(crash_at=crash_at, **system_kwargs), store,
            run_ids=["run-1"])


def _grades(report):
    return {r.case.id: (r.grade.grade, r.passed) for r in report.results}


@pytest.mark.criterion("C5")
def test_resume_answers_only_the_cases_without_a_row(kit):
    # oracle: requirement 10 — hand count: a, b stored before the crash at c; c, d, e re-run
    cases = [kit.make_case(c) for c in "abcde"]
    store = kit.MemoryStore()
    _interrupt(kit, cases, "c", store)
    assert store.stored_ids("run-1") == {"a", "b"}
    assert store.rows["run-1"]["status"] != "OK"
    second = kit.FakeSystem()
    (report,) = _go(kit, cases, second, store, resume="run-1")
    assert second.answered == ["c", "d", "e"]
    assert report.run_id == "run-1"
    assert report.status == "OK"
    assert sorted(r.case.id for r in report.results) == list("abcde")
    assert report.n_planned == 5 and report.n_skipped == 0
    first, resumed = report.config["segments"]
    assert (first["n_restored"], resumed["n_restored"]) == (0, 2)
    assert resumed["retried"] == []
    assert store.count("finish_run") == 1


@pytest.mark.criterion("C5")
def test_a_resumed_run_keeps_its_started_at(kit):
    # oracle: requirement 9 — the stored start time, not the resume's
    cases = [kit.make_case(c) for c in "abc"]
    store = kit.MemoryStore()
    _interrupt(kit, cases, "b", store)
    # The first segment started a day ago: set in the stored row, since two starts within
    # one clock tick would read the same time and could not tell the two apart.
    started = "2026-11-02T08:00:00+00:00"
    store.rows["run-1"]["started_at"] = started
    (report,) = _go(kit, cases, kit.FakeSystem(), store, resume="run-1")
    assert report.started_at == started
    assert store.rows["run-1"]["started_at"] == started


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(n=st.integers(2, 7), crash=st.integers(0, 6), wrong=st.sets(st.integers(0, 6)))
def test_interrupted_and_resumed_equals_uninterrupted(kit, n, crash, wrong):
    # oracle: the uninterrupted run of the same deterministic system is the reference
    cases = [kit.make_case(f"c{i}") for i in range(n)]
    wrong_ids = {f"c{i}" for i in wrong}
    crash_id = f"c{min(crash, n - 1)}"
    (reference,) = _go(kit, cases, kit.FakeSystem(wrong=wrong_ids), kit.MemoryStore(),
                       run_ids=["ref"])
    store = kit.MemoryStore()
    _interrupt(kit, cases, crash_id, store, wrong=wrong_ids)
    before = store.stored_ids("run-1")
    second = kit.FakeSystem(wrong=wrong_ids)
    (resumed,) = _go(kit, cases, second, store, resume="run-1")
    assert sorted(second.answered) == sorted(c.id for c in cases if c.id not in before)
    assert len(resumed.results) == n
    assert _grades(resumed) == _grades(reference)
    assert resumed.metrics == reference.metrics
    assert store.stored_ids("run-1") == {c.id for c in cases}


@pytest.mark.criterion("C5")
def test_the_budget_counts_the_restored_cost(kit):
    # oracle: hand count — 6 cases at $0.25, 3 restored ($0.75) and a $1.25 cap: two more
    # answers reach the cap, the sixth case is never attempted
    cases = [kit.make_case(c) for c in "abcdef"]
    store = kit.MemoryStore()
    _interrupt(kit, cases, "d", store, cost=0.25)
    second = kit.FakeSystem(cost=0.25)
    (report,) = _go(kit, cases, second, store, resume="run-1", max_cost_usd=1.25)
    assert second.answered == ["d", "e"]
    assert report.status == "ABORTED"
    assert report.n_skipped == 1
    assert report.metrics.total_cost_usd == pytest.approx(1.25, abs=1e-12)  # sums of 0.25


@pytest.mark.criterion("C5")
def test_served_models_are_the_union_of_restored_and_new(kit):
    # oracle: requirement 10 — the served ids written into each segment's answers
    cases = [kit.make_case(c) for c in "abc"]
    store = kit.MemoryStore()
    _interrupt(kit, cases, "c", store, served={"model-1": ["model-1-2026-01"]})
    second = kit.FakeSystem(served={"model-1": ["model-1-2026-02"]})
    (report,) = _go(kit, cases, second, store, resume="run-1")
    assert report.config["served_models"] == {"model-1": ["model-1-2026-01", "model-1-2026-02"]}


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("field,value", [("git_sha", "fff0000"), ("model", "model-2"),
                                         ("llm_profile", "openai"),
                                         ("settings", {"cache_groups": "filing"})])
def test_a_mismatched_resume_calls_nothing_and_writes_nothing(kit, field, value):
    # oracle: requirement 8 — refused before any call or write
    cases = [kit.make_case(c) for c in "abc"]
    store = kit.MemoryStore()
    _interrupt(kit, cases, "b", store)
    calls_before = list(store.calls)
    second = kit.FakeSystem()
    identity = {**kit.identity_for(cases, "fake_system"), field: value}
    with pytest.raises(ResumeRefused):
        _go(kit, cases, second, store, resume="run-1", identity=identity)
    assert second.answered == []
    assert store.calls == calls_before


@pytest.mark.criterion("C5")
def test_a_changed_selection_or_unknown_run_is_refused(kit):
    # oracle: requirements 7-8 — another case set changes the suite hash; no run, no resume
    cases = [kit.make_case(c) for c in "abc"]
    store = kit.MemoryStore()
    _interrupt(kit, cases, "b", store)
    second = kit.FakeSystem()
    with pytest.raises(ResumeRefused):
        _go(kit, cases[:2], second, store, resume="run-1")
    with pytest.raises(ResumeRefused):
        _go(kit, cases, second, store, resume="no-such-run")
    assert second.answered == []
