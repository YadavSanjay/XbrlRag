"""C3: the runner checks the split before answering anything (requirement 3)."""

from __future__ import annotations

import pytest

from src.eval.core.run_columns import MixedSplitError
from src.eval.core.runner import EvalRunner, RunnerConfig


def _runner() -> EvalRunner:
    return EvalRunner(RunnerConfig(max_workers=1, progress_every=0))


@pytest.mark.criterion("C3")
def test_mixed_run_raises_before_any_case_is_answered(make_case, fake_system):
    # oracle: requirement 3 — run_split runs first, so a dev + validation run raises and
    # the system is never called
    cases = [make_case("a", ["split:dev"]), make_case("b", ["split:validation"])]
    system = fake_system()
    with pytest.raises(MixedSplitError):
        _runner().run(cases, system)
    assert system.answered == []


@pytest.mark.criterion("C3")
def test_single_split_run_records_it_in_config(make_case, fake_system):
    # oracle: requirement 3 — report.config["split"] is the run's split; untagged is legacy
    cases = [make_case("a", ["split:heldout_filers"]), make_case("b", ["split:heldout_filers"])]
    system = fake_system()
    report = _runner().run(cases, system)
    assert report.config.get("split") == "heldout_filers"
    assert sorted(system.answered) == ["a", "b"]
    legacy = _runner().run([make_case("c", [])], fake_system())
    assert legacy.config.get("split") == "legacy"


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_outcome_does_not_depend_on_case_order(make_case, fake_system):
    # oracle: requirement 3 — the split is a property of the set of cases: reversing a
    # mixed run still raises with nothing answered, reversing a dev run still gives dev
    mixed = [make_case("a", ["split:dev"]), make_case("b", []), make_case("c", ["split:dev"])]
    for order in (mixed, list(reversed(mixed))):
        system = fake_system()
        with pytest.raises(MixedSplitError):
            _runner().run(order, system)
        assert system.answered == []
    dev = [make_case("a", ["split:dev"]), make_case("b", ["split:dev", "intent:i1"])]
    forward = _runner().run(dev, fake_system())
    backward = _runner().run(list(reversed(dev)), fake_system())
    assert forward.config.get("split") == "dev"
    assert backward.config.get("split") == "dev"
