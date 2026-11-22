"""C1: the runner calls an optional ``plan_cases`` once per system before answering (req. 1).

The runner, graders and checkpoint logic are real; the systems and the run store are fakes
(conftest.py) that log every ``plan_cases`` and ``answer`` call into one event list.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import run_checkpointed
from src.eval.core.runner import EvalRunner, RunnerConfig
from src.llm.run_budget import CircuitBreakerOpen, RunHalt

IDS = list("abcde")


def _cases(kit, ids=IDS):
    tickers = {"a": "AAA", "b": "BBB", "c": "AAA", "d": "CCC", "e": "BBB"}
    return [kit.make_case(i, ticker=tickers.get(i, "AAA")) for i in ids]


def _planned_before_answers(log) -> bool:
    first = log.first_answer_index()
    return all(i < first for i in log.plan_indexes())


@pytest.mark.criterion("C1")
def test_a_plain_run_plans_every_case_in_suite_order(kit):
    # oracle: requirement 1 — the constructed case list a..e, in that order, once
    log = kit.EventLog()
    system = kit.PlanningSystem("sys", log)
    report = EvalRunner(RunnerConfig(max_workers=3)).run(_cases(kit), system)
    assert log.plans("sys") == [IDS]
    assert _planned_before_answers(log)
    assert sorted(log.answered("sys")) == IDS
    assert sorted(r.case.id for r in report.results) == IDS
    plain_log = kit.EventLog()  # a system without the hook runs as before
    plain = EvalRunner(RunnerConfig(max_workers=3)).run(
        _cases(kit), kit.PlainSystem("plain", plain_log))
    assert sorted(plain_log.answered("plain")) == IDS
    assert sorted(r.case.id for r in plain.results) == IDS


@pytest.mark.criterion("C1")
def test_a_narrowed_run_plans_only_the_cases_passed(kit):
    # oracle: requirement 1 — a narrowed selection (b, d) is exactly what is planned
    log = kit.EventLog()
    system = kit.PlanningSystem("sys", log)
    EvalRunner(RunnerConfig(max_workers=2)).run(_cases(kit, ["b", "d"]), system)
    assert log.plans("sys") == [["b", "d"]]
    assert _planned_before_answers(log)


@pytest.mark.criterion("C1")
def test_a_resume_from_a_prior_report_plans_the_outstanding_cases(kit):
    # oracle: requirement 1 — a and c are restored from the prior report; b, d, e remain
    prior = EvalRunner(RunnerConfig(max_workers=1)).run(
        _cases(kit, ["a", "c"]), kit.PlainSystem("sys", kit.EventLog()))
    log = kit.EventLog()
    system = kit.PlanningSystem("sys", log)
    report = EvalRunner(RunnerConfig(max_workers=2)).run(_cases(kit), system, prior=prior)
    assert log.plans("sys") == [["b", "d", "e"]]
    assert _planned_before_answers(log)
    assert sorted(log.answered("sys")) == ["b", "d", "e"]
    assert sorted(r.case.id for r in report.results) == IDS


@pytest.mark.criterion("C1")
def test_a_single_system_checkpointed_resume_plans_the_outstanding_cases(kit):
    # oracle: requirement 1 — the outstanding set is the suite minus what the store holds
    # for the run after the crash at c, in suite order (read from the store, not the code)
    cases = _cases(kit)
    store = kit.RunMemoryStore()
    crashed = kit.PlainSystem("sys", kit.EventLog(), crash_at="c")
    with pytest.raises(kit.Crash):
        run_checkpointed(cases, [crashed], store=store,
                         identities=[kit.identity_for(cases, "sys")],
                         run_ids=["run-1"], config=RunnerConfig(max_workers=1))
    expected = [i for i in IDS if i not in store.stored_ids("run-1")]
    assert expected == ["c", "d", "e"]

    log = kit.EventLog()
    resumed = kit.PlanningSystem("sys", log)
    (report,) = run_checkpointed(cases, [resumed], store=store,
                                 identities=[kit.identity_for(cases, "sys")],
                                 resume="run-1", config=RunnerConfig(max_workers=2))
    assert log.plans("sys") == [expected]
    assert _planned_before_answers(log)
    assert sorted(log.answered("sys")) == expected
    assert sorted(r.case.id for r in report.results) == IDS


# A joint schedule: an Anthropic oracle arm on AAA (asked twice) and BBB, CCC (asked once).
_JOINT = [("j1", "AAA", 2024), ("j2", "BBB", 2024), ("j3", "AAA", 2024), ("j4", "CCC", 2024)]


def _joint_run(kit, path, systems, cases):
    if path == "run_many":
        return EvalRunner(RunnerConfig(max_workers=2, cache_groups="filing")).run_many(
            cases, systems)
    return run_checkpointed(cases, systems, store=kit.RunMemoryStore(),
                            identities=[kit.identity_for(cases, s.name) for s in systems],
                            config=RunnerConfig(max_workers=2, cache_groups="filing"))


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("path", ["run_many", "run_checkpointed"])
def test_a_joint_schedule_plans_no_system_and_keeps_every_mark(kit, path):
    # oracle: requirement 1 — with two systems scheduled together neither hook is called,
    # so the oracle marks all four filing messages; the same cases in a single-system run
    # of each are planned (BBB and CCC single-use, so unmarked on Anthropic)
    filers = sorted({(t, y) for _, t, y in _JOINT})
    cases = [kit.make_case(i, ticker=t, fy=y) for i, t, y in _JOINT]
    ids = [i for i, _, _ in _JOINT]

    oracle, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, filers)
    log = kit.EventLog()
    other = kit.PlanningSystem("other", log)
    reports = _joint_run(kit, path, [oracle, other], cases)
    assert log.plans("other") == []
    assert sorted(log.answered("other")) == sorted(ids)
    assert adapter.calls == 4
    assert {i: adapter.marked(kit.question_of(i)) for i in ids} == dict.fromkeys(ids, True)
    assert [sorted(r.case.id for r in rep.results) for rep in reports] == [sorted(ids)] * 2

    alone, alone_adapter = kit.make_arm(kit.ANTHROPIC_MODEL, filers)
    _joint_run(kit, path, [alone], cases)
    assert {i: alone_adapter.marked(kit.question_of(i)) for i in ids} == {
        "j1": True, "j2": False, "j3": True, "j4": False}
    solo_log = kit.EventLog()
    _joint_run(kit, path, [kit.PlanningSystem("other", solo_log)], cases)
    assert solo_log.plans("other") == [ids]


@pytest.mark.criterion("C1")
def test_a_raising_hook_does_not_abort_the_run(kit):
    # oracle: requirement 1 — the hook was called, raised, and every case a..e was still
    # answered and graded
    log = kit.EventLog()
    system = kit.PlanningSystem("sys", log, plan_error=ValueError("plan failed"))
    report = EvalRunner(RunnerConfig(max_workers=2)).run(_cases(kit), system)
    assert log.plans("sys") == [IDS]
    assert sorted(log.answered("sys")) == IDS
    assert sorted(r.case.id for r in report.results) == IDS
    assert report.status == "OK"


@pytest.mark.criterion("C1")
def test_a_run_halt_from_the_hook_propagates_before_any_answer(kit):
    # oracle: requirement 1 — a RunHalt is not swallowed; no case is answered
    log = kit.EventLog()
    system = kit.PlanningSystem(
        "sys", log, plan_error=CircuitBreakerOpen("3 consecutive LLM failures >= limit 3"))
    with pytest.raises(RunHalt):
        EvalRunner(RunnerConfig(max_workers=2)).run(_cases(kit), system)
    assert log.plans("sys") == [IDS]
    assert log.answered("sys") == []


@pytest.mark.criterion("C1")
@given(
    selected=st.lists(st.sampled_from(list("abcdefgh")), min_size=1, max_size=8, unique=True),
    restored_mask=st.lists(st.booleans(), min_size=8, max_size=8),
)
def test_the_plan_is_the_outstanding_set_in_suite_order(kit, selected, restored_mask):
    # oracle: the test's own construction — planned == selected minus restored, in the
    # order the selection was passed
    cases = _cases(kit, selected)
    restored = [c for c, r in zip(cases, restored_mask, strict=False) if r]
    prior = None
    if restored:
        prior = EvalRunner(RunnerConfig(max_workers=1)).run(
            restored, kit.PlainSystem("sys", kit.EventLog()))
    log = kit.EventLog()
    system = kit.PlanningSystem("sys", log)
    EvalRunner(RunnerConfig(max_workers=2)).run(cases, system, prior=prior)
    restored_ids = {c.id for c in restored}
    outstanding = [c.id for c in cases if c.id not in restored_ids]
    assert outstanding == [i for i in selected if i not in restored_ids]
    if outstanding:
        assert log.plans("sys") == [outstanding]
    else:
        assert len(log.plans("sys")) <= 1
    assert _planned_before_answers(log)
    assert sorted(log.answered("sys")) == sorted(outstanding)
