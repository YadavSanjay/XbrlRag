"""C2: the run row at start, one write per case as it is graded, the row at the end
(requirements 3-5)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import run_checkpointed
from src.eval.core.runner import RunnerConfig


def _run(kit, cases, system, store, *, cache_groups=None, run_id="run-1"):
    return run_checkpointed(
        cases, [system], store=store,
        identities=[kit.identity_for(cases, system.name)], run_ids=[run_id],
        config=RunnerConfig(max_workers=1, cache_groups=cache_groups),
        suite="spec_suite", suite_version="v1")


@pytest.mark.criterion("C2")
def test_running_row_exists_before_the_first_answer(kit):
    # oracle: requirement 3 — at the first answer the store holds one RUNNING row, no cases
    store = kit.MemoryStore()
    seen: list[tuple] = []

    def look(_name, case):
        if not seen:
            row = store.load_run_row("run-1")
            seen.append((row and row["status"], len(store.stored_ids("run-1"))))

    cases = [kit.make_case(c) for c in ("a", "b", "c")]
    system = kit.FakeSystem(on_answer=look)
    _run(kit, cases, system, store)
    assert seen == [("RUNNING", 0)]
    assert store.count("start_run") == 1


@pytest.mark.criterion("C2")
def test_the_start_config_carries_identity_schedule_and_first_segment(kit):
    # oracle: requirements 3 and 9 — written at start, read back at the first answer
    store = kit.MemoryStore()
    configs: list[dict] = []
    cases = [kit.make_case(c) for c in ("a", "b")]
    identity = kit.identity_for(cases, "fake_system")

    def look(_name, _case):
        if not configs:
            configs.append(store.load_run_row("run-1")["config"])

    _run(kit, cases, kit.FakeSystem(on_answer=look), store)
    (config,) = configs
    assert config["resume_identity"] == identity
    assert config["schedule_run_ids"] == {"fake_system": "run-1"}
    (segment,) = config["segments"]
    assert segment["n_restored"] == 0
    assert segment["retried"] == []
    assert {"started_at", "n_restored", "retried", "max_cost_usd", "max_workers"} <= set(segment)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("cache_groups", [None, "filing"])
def test_kth_answer_sees_k_minus_one_rows(kit, cache_groups):
    # oracle: requirement 4 — hand count; all five cases share one filing, so with "filing"
    # they are one task, which today reports its results only when the task ends
    store = kit.MemoryStore()
    counts: list[int] = []
    cases = [kit.make_case(f"c{i}", ticker="AAA", fy=2024) for i in range(5)]
    system = kit.FakeSystem(on_answer=lambda _n, _c: counts.append(len(store.stored_ids("run-1"))))
    _run(kit, cases, system, store, cache_groups=cache_groups)
    assert counts == [0, 1, 2, 3, 4]
    assert store.count("write_case") == 5


@pytest.mark.criterion("C2")
@given(n=st.integers(1, 7), wrong=st.sets(st.integers(0, 6)))
def test_every_case_written_once_and_the_row_finalised_ok(kit, n, wrong):
    # oracle: requirements 4-5 — n cases give n writes, one finish, status OK, and the stored
    # rows are exactly the report's cases
    store = kit.MemoryStore()
    cases = [kit.make_case(f"c{i}") for i in range(n)]
    system = kit.FakeSystem(wrong={f"c{i}" for i in wrong})
    (report,) = _run(kit, cases, system, store)
    assert report.status == "OK"
    assert store.count("write_case") == n
    assert store.count("finish_run") == 1
    assert store.rows["run-1"]["status"] == "OK"
    assert store.stored_ids("run-1") == {c.id for c in cases}
    assert sorted(r.case.id for r in report.results) == sorted(c.id for c in cases)
    assert report.metrics.n_incorrect == len({i for i in wrong if i < n})


@pytest.mark.criterion("C2")
def test_a_failed_write_is_counted_and_the_run_goes_on(kit):
    # oracle: requirement 4 — one of four writes fails: counted once, all four graded
    store = kit.MemoryStore(fail_writes={"b"})
    cases = [kit.make_case(c) for c in ("a", "b", "c", "d")]
    (report,) = _run(kit, cases, kit.FakeSystem(), store)
    assert report.status == "OK"
    assert report.config["checkpoint_write_failures"] == 1
    assert len(report.results) == 4


@pytest.mark.criterion("C2")
def test_a_store_that_cannot_start_stops_the_run_before_any_answer(kit):
    # oracle: requirement 3 — a run asked to be stored is never run unstored
    store = kit.MemoryStore()
    store.fail_start = True
    system = kit.FakeSystem()
    cases = [kit.make_case(c) for c in ("a", "b")]
    with pytest.raises(ConnectionError):  # the store's own exception, propagated
        _run(kit, cases, system, store)
    assert store.count("start_run") == 1
    assert system.answered == []
    assert store.count("write_case") == 0
