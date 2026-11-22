"""C6: every eval run records its service tier; run_suite --service-tier sets it for a run.

Oracle: requirements 6 and 7. The system under test is the fake of conftest; the runner is
the real ``EvalRunner``. ``run_suite.main`` is driven with its ``_run`` step replaced by a
recorder (a script-level seam: the parsed arguments and the run's environment are the
subject, not the suite run itself).
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.runner import EvalRunner, RunnerConfig
from src.llm.profiles import LlmConfigError


def _run(spec, systems):
    runner = EvalRunner(RunnerConfig(max_workers=1, progress_every=0))
    return runner.run_many(spec.make_cases(["c1", "c2"]), systems, suite="spec")


@pytest.mark.criterion("C6")
def test_the_tier_is_recorded_and_none_when_unset(spec):
    # oracle: requirement 6 — config["service_tier"] is the tier at the run's start; None
    # when unset
    with spec.tier_env("flex"):
        (flex,) = _run(spec, [spec.fake_system()])
    with spec.tier_env(None):
        (unset,) = _run(spec, [spec.fake_system()])
    assert flex.config.get("service_tier") == "flex"
    assert "service_tier" in unset.config
    assert unset.config["service_tier"] is None
    assert len(flex.results) == 2


@pytest.mark.criterion("C6")
def test_every_report_of_a_shared_schedule_records_it(spec):
    # oracle: requirement 6 — run_many puts the tier on every report it creates
    with spec.tier_env("priority"):
        reports = _run(spec, [spec.fake_system("arm_a"), spec.fake_system("arm_b")])
    assert [r.config.get("service_tier") for r in reports] == ["priority", "priority"]


@pytest.mark.criterion("C6")
def test_an_invalid_tier_fails_before_any_case(spec):
    # oracle: requirement 6 — LlmConfigError before any case is answered
    system = spec.fake_system()
    with spec.tier_env("auto"):
        (ok,) = _run(spec, [spec.fake_system()])
    assert ok.config.get("service_tier") == "auto"
    with spec.tier_env("bogus"), pytest.raises(LlmConfigError):
        _run(spec, [system])
    assert system.answered == []


@pytest.mark.criterion("C6")
@given(tier=st.sampled_from([None, "auto", "default", "flex", "priority"]))
def test_any_tier_is_recorded_as_read(spec, tier):
    # oracle: requirement 6 — the recorded value equals the variable's meaning at start
    with spec.tier_env(tier):
        (report,) = _run(spec, [spec.fake_system()])
    assert "service_tier" in report.config
    assert report.config["service_tier"] == tier


# ---------------------------------------------------------------------------- run_suite


def _load_run_suite(spec):
    loader = importlib.util.spec_from_file_location("run_suite_o61_under_test", spec.run_suite)
    module = importlib.util.module_from_spec(loader)
    sys.modules["run_suite_o61_under_test"] = module
    loader.loader.exec_module(module)
    return module


def _main(spec, argv):
    """Runs run_suite.main with _run recording the tier it sees; returns (code, seen)."""
    module = _load_run_suite(spec)
    seen: list[str | None] = []

    def recording_run(args, llm_profile):
        seen.append(os.environ.get(spec.tier_env_name))
        return 0

    module._run = recording_run
    try:
        code = module.main(argv)
    except SystemExit as exc:
        code = exc.code
    return code, seen


@pytest.mark.criterion("C6")
def test_run_suite_flag_sets_the_tier_for_the_run_and_restores_it(spec):
    # oracle: requirement 7 — --service-tier flex runs with the variable flex and restores
    # the previous value (unset, or priority) afterwards
    with spec.tier_env(None):
        code, seen = _main(spec, ["--llm-profile", "openai", "--service-tier", "flex"])
        assert (code, seen) == (0, ["flex"])
        assert os.environ.get(spec.tier_env_name) is None
    with spec.tier_env("priority"):
        code, seen = _main(spec, ["--llm-profile", "openai", "--service-tier", "default"])
        assert (code, seen) == (0, ["default"])
        assert os.environ.get(spec.tier_env_name) == "priority"
        code, seen = _main(spec, ["--llm-profile", "openai"])
        assert (code, seen) == (0, ["priority"])


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("bad", ["bogus", "FLEX", "batch"])
def test_run_suite_rejects_an_unknown_tier_before_running(spec, bad):
    # oracle: requirement 7 — any other value is a usage error (exit status 2), before any run
    code, seen = _main(spec, ["--llm-profile", "openai", "--service-tier", "flex"])
    assert (code, seen) == (0, ["flex"])
    code, seen = _main(spec, ["--llm-profile", "openai", "--service-tier", bad])
    assert code == 2
    assert seen == []
