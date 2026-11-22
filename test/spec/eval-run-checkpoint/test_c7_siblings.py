"""C7: --with-system siblings checkpoint and resume together (requirement 12)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import ResumeRefused, run_checkpointed
from src.eval.core.runner import RunnerConfig

NAMES = ("arm_a", "arm_b")


def _cases(kit, n=4):
    # two filings, so the schedule has two cache groups
    return [kit.make_case(f"c{i}", ticker="AAA" if i % 2 == 0 else "BBB", fy=2024)
            for i in range(n)]


def _go(kit, cases, systems, store, **kwargs):
    return run_checkpointed(
        cases, systems, store=store,
        identities=[kit.identity_for(cases, s.name) for s in systems],
        config=RunnerConfig(max_workers=1, cache_groups="filing"),
        suite="spec_suite", suite_version="v1", **kwargs)


@pytest.mark.criterion("C7")
def test_each_sibling_answers_exactly_its_missing_cases(kit):
    # oracle: requirement 12 — hand count. Order: c0,c2 (AAA) then c1,c3 (BBB), each case to
    # arm_a then arm_b. The power cut at arm_b's c2 leaves arm_a {c0,c2}, arm_b {c0}
    cases = _cases(kit)
    store = kit.MemoryStore()
    power = kit.Power()
    a = kit.FakeSystem("arm_a", power=power)
    b = kit.FakeSystem("arm_b", crash_at="c2", power=power)
    with pytest.raises(kit.Crash):
        _go(kit, cases, [a, b], store, run_ids=["run-a", "run-b"])
    assert store.stored_ids("run-a") == {"c0", "c2"}
    assert store.stored_ids("run-b") == {"c0"}
    a2, b2 = kit.FakeSystem("arm_a"), kit.FakeSystem("arm_b")
    ra, rb = _go(kit, cases, [a2, b2], store, resume="run-a")
    assert sorted(a2.answered) == ["c1", "c3"]
    assert sorted(b2.answered) == ["c1", "c2", "c3"]
    assert (ra.run_id, rb.run_id) == ("run-a", "run-b")
    for report in (ra, rb):
        assert report.status == "OK"
        assert sorted(r.case.id for r in report.results) == ["c0", "c1", "c2", "c3"]
    assert store.rows["run-b"]["config"]["schedule_run_ids"] == {"arm_a": "run-a",
                                                                  "arm_b": "run-b"}
    assert len(store.rows["run-b"]["config"]["segments"]) == 2


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(n=st.integers(2, 6), stop=st.integers(1, 12))
def test_siblings_resumed_equal_siblings_uninterrupted(kit, n, stop):
    # oracle: the uninterrupted two-arm run is the reference; arm_b answers c0 wrong
    cases = _cases(kit, n)
    stop = min(stop, 2 * n)
    ref = _go(kit, cases, [kit.FakeSystem("arm_a"), kit.FakeSystem("arm_b", wrong={"c0"})],
              kit.MemoryStore(), run_ids=["ra", "rb"])
    store = kit.MemoryStore()
    power = kit.Power(cut_at_call=stop)  # the stop-th answer of the run is never finished
    first = [kit.FakeSystem("arm_a", power=power),
             kit.FakeSystem("arm_b", wrong={"c0"}, power=power)]
    with pytest.raises(kit.Crash):
        _go(kit, cases, first, store, run_ids=["run-a", "run-b"])
    stored = {"arm_a": store.stored_ids("run-a"), "arm_b": store.stored_ids("run-b")}
    second = [kit.FakeSystem("arm_a"), kit.FakeSystem("arm_b", wrong={"c0"})]
    resumed = _go(kit, cases, second, store, resume="run-a")
    for system, got, want in zip(second, resumed, ref, strict=True):
        assert sorted(system.answered) == sorted(
            c.id for c in cases if c.id not in stored[system.name])
        assert got.metrics == want.metrics
        assert {r.case.id: r.grade.grade for r in got.results} == {
            r.case.id: r.grade.grade for r in want.results}


@pytest.mark.criterion("C7")
def test_a_resume_must_bring_every_sibling(kit):
    # oracle: requirement 8 — the systems must be exactly the stored schedule
    cases = _cases(kit)
    store = kit.MemoryStore()
    power = kit.Power()
    with pytest.raises(kit.Crash):
        _go(kit, cases, [kit.FakeSystem("arm_a", power=power),
                         kit.FakeSystem("arm_b", crash_at="c2", power=power)],
            store, run_ids=["run-a", "run-b"])
    alone = kit.FakeSystem("arm_a")
    with pytest.raises(ResumeRefused):
        _go(kit, cases, [alone], store, resume="run-a")
    assert alone.answered == []
