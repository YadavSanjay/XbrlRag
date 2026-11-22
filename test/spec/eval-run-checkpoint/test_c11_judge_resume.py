"""C11: the judge's errors and cost survive a resume exactly (requirements 1 and 10).

The claim judge is real, on a real gateway whose only provider is conftest's fake adapter.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import run_checkpointed
from src.eval.core.runner import RunnerConfig


def _go(kit, cases, system, store, judge, *, resume=None, run_ids=None, max_cost_usd=None):
    return run_checkpointed(
        cases, [system], store=store, identities=[kit.identity_for(cases, system.name)],
        resume=resume, run_ids=run_ids, judge=judge, generator_profile=kit.GENERATOR_PROFILE,
        config=RunnerConfig(max_workers=1, max_cost_usd=max_cost_usd),
        suite="spec_suite", suite_version="v1")


def _interrupted(kit, cases, crash_at, malformed):
    """A first segment that judged the cases before *crash_at*, then died."""
    store = kit.MemoryStore()
    judge, _ = kit.make_judge(malformed)
    with pytest.raises(kit.Crash):
        _go(kit, cases, kit.FakeSystem(crash_at=crash_at), store, judge, run_ids=["run-1"])
    return store


@pytest.mark.criterion("C11")
def test_judge_errors_and_cost_cover_every_segment(kit):
    # oracle: hand count — four text cases each judged once in all (t1, t2 before the crash at
    # t3; t3, t4 after it), one call each at the pricing.yaml rate; t2's reply is malformed
    cases = [kit.make_text_case(c) for c in ("t1", "t2", "t3", "t4")]
    unit = kit.judge_call_cost()
    store = _interrupted(kit, cases, "t3", {"question t2"})
    judge, adapter = kit.make_judge({"question t2"})
    (report,) = _go(kit, cases, kit.FakeSystem(), store, judge, resume="run-1")
    assert adapter.calls == 2  # this segment judged only t3 and t4
    assert set(report.config["judge_errors"]) == {"t2"}
    assert report.config["judge_errors"]["t2"].startswith("judge_error: ")
    # tolerance: four products of pricing.yaml rates summed; rel=1e-9 absorbs float addition
    assert report.config["judge_cost_usd"] == pytest.approx(4 * unit, rel=1e-9)
    for result in report.results:
        assert result.judge_cost_usd == pytest.approx(unit, rel=1e-9)  # tolerance: as above
    restored_t2 = next(r for r in report.results if r.case.id == "t2")
    assert restored_t2.judge_error == report.config["judge_errors"]["t2"]


@pytest.mark.criterion("C11")
def test_the_budget_counts_the_restored_judge_cost(kit):
    # oracle: hand count — t1, t2 restored with 2u of judge spend and a 2.5u cap: t3 reaches
    # 3u, so t4 is never attempted. Without the restored 2u, t4 would run (2u < 2.5u)
    cases = [kit.make_text_case(c) for c in ("t1", "t2", "t3", "t4")]
    unit = kit.judge_call_cost()
    store = _interrupted(kit, cases, "t3", set())
    judge, _ = kit.make_judge()
    second = kit.FakeSystem()
    (report,) = _go(kit, cases, second, store, judge, resume="run-1", max_cost_usd=2.5 * unit)
    assert second.answered == ["t3"]
    assert report.status == "ABORTED"
    assert report.n_skipped == 1


@pytest.mark.criterion("C11")
@pytest.mark.metamorphic
@settings(max_examples=25)
@given(n=st.integers(2, 5), crash=st.integers(0, 4), malformed=st.sets(st.integers(0, 4)))
def test_judged_resume_equals_an_uninterrupted_judged_run(kit, n, crash, malformed):
    # oracle: the uninterrupted run with the same judge replies is the reference
    cases = [kit.make_text_case(f"t{i}") for i in range(n)]
    bad = {f"question t{i}" for i in malformed}
    judge, _ = kit.make_judge(bad)
    (reference,) = _go(kit, cases, kit.FakeSystem(), kit.MemoryStore(), judge, run_ids=["ref"])
    store = _interrupted(kit, cases, f"t{min(crash, n - 1)}", bad)
    judge, _ = kit.make_judge(bad)
    (resumed,) = _go(kit, cases, kit.FakeSystem(), store, judge, resume="run-1")
    assert resumed.config["judge_errors"] == reference.config["judge_errors"]
    # tolerance: the same per-call costs summed in another order; rel=1e-9
    assert resumed.config["judge_cost_usd"] == pytest.approx(
        reference.config["judge_cost_usd"], rel=1e-9)
    assert resumed.config["judge_summary"] == reference.config["judge_summary"]
