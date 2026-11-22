"""C4: an ``xbrlrag_full`` eval run stops at a halt and resumes (requirement 5).

The real ``XbrlRagFullSystem`` over the real pipeline, the real runner and ``run_checkpointed``,
an in-memory ``RunStore``, one worker; every case is on one filing and the run groups by
filing, so the cases form one task answered in order and a halt ends it there. The halt is
a Flex-cap ``FlexWaitExceeded`` met by one case: the stage's adapter answers every earlier
case and meets a Flex 429 on that case's call.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import run_checkpointed
from src.eval.core.graders.base import Grade
from src.eval.core.runner import RunnerConfig
from src.eval.systems.xbrlrag_full import XbrlRagFullSystem


def _system(h, *, halt_case: int | None = None, stage: str = "decomposer"):
    """The arm over a kit whose *stage* meets a Flex 429 on case number *halt_case* (0-based)."""
    scripts = {}
    if halt_case is not None:
        reply = h.decomposer_reply() if stage == "decomposer" else h.VERIFIER_REPLY
        scripts[stage] = [reply] * halt_case + [h.flex()]
    kit = h.Kit(scripts=scripts)
    return XbrlRagFullSystem(pipeline=kit.pipeline, transient_retries=0), kit


def _run(h, cases, system, store, resume=None):
    kwargs = {"run_ids": ["run-1"]} if resume is None else {"resume": resume}
    with h.env_vars(**h.flex_cap(1.0)):
        (report,) = run_checkpointed(
            cases, [system], store=store, identities=[h.identity_for(cases, system.name)],
            config=RunnerConfig(max_workers=1, cache_groups="filing"), suite="spec_suite",
            suite_version="v1", **kwargs)
    return report


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("stage", ["decomposer", "verifier"])
def test_a_halt_in_case_b_ends_the_run_aborted_with_only_a_stored(h, stage):
    # oracle: eval-run-checkpoint requirement 11 — hand count: a is graded before b halts;
    # b has no row and no ERROR grade; c is never answered
    cases = [h.make_case(c) for c in "abc"]
    store = h.MemoryStore()
    system, _ = _system(h, halt_case=1, stage=stage)
    report = _run(h, cases, system, store)
    assert report.status == "ABORTED"
    assert report.config["halt"]["type"] == "FlexWaitExceeded"
    assert {r.case.id for r in report.results} == {"a"}
    assert store.stored_ids("run-1") == {"a"}
    assert all(r.grade.grade is not Grade.ERROR for r in report.results)


@pytest.mark.criterion("C4")
def test_the_resume_answers_exactly_the_halted_and_unanswered_cases(h):
    # oracle: eval-run-checkpoint requirements 10-11 — b (halted, no row) and c (never reached)
    # are answered on resume, a is restored; the run then finishes OK with each case once
    cases = [h.make_case(c) for c in "abc"]
    store = h.MemoryStore()
    first, _ = _system(h, halt_case=1, stage="verifier")
    _run(h, cases, first, store)
    second, kit = _system(h)
    report = _run(h, cases, second, store, resume="run-1")
    assert kit.adapters["decomposer"].invoked == 2  # b and c, one decomposer call each
    assert len(kit.lookup.steps) == 2
    assert report.status == "OK"
    assert sorted(r.case.id for r in report.results) == ["a", "b", "c"]
    assert store.stored_ids("run-1") == {"a", "b", "c"}


@pytest.mark.criterion("C4")
@settings(max_examples=12)
@given(n=st.integers(2, 5), at=st.integers(1, 4),
       stage=st.sampled_from(("decomposer", "verifier")))
def test_the_cases_before_the_halt_are_kept_and_the_rest_resumed(h, n, at, stage):
    # oracle: eval-run-checkpoint requirements 10-11 — with one worker, cases run in order; the
    # cases before the halting one are stored, the resume answers exactly the others
    halt = min(at, n - 1)
    cases = [h.make_case(f"c{i}") for i in range(n)]
    before = {f"c{i}" for i in range(halt)}
    store = h.MemoryStore()
    first, _ = _system(h, halt_case=halt, stage=stage)
    report = _run(h, cases, first, store)
    assert report.status == "ABORTED"
    assert store.stored_ids("run-1") == before
    second, kit = _system(h)
    resumed = _run(h, cases, second, store, resume="run-1")
    assert kit.adapters["decomposer"].invoked == n - halt
    assert resumed.status == "OK"
    assert sorted(r.case.id for r in resumed.results) == sorted(c.id for c in cases)
