"""C10: refused combinations fail before any call (requirements 16-17)."""

from __future__ import annotations

import importlib.util
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import run_checkpointed
from src.eval.core.runner import RunnerConfig


def _try(kit, store, system, cases, **kwargs):
    kwargs.setdefault("identities", [kit.identity_for(cases, system.name)])
    return run_checkpointed(cases, [system], store=store, config=RunnerConfig(max_workers=1),
                            suite="spec_suite", suite_version="v1", **kwargs)


def _stored_run(kit, cases):
    """A finished run "run-1", so a resume would otherwise be accepted."""
    store = kit.MemoryStore()
    _try(kit, store, kit.FakeSystem(), cases, run_ids=["run-1"])
    return store


REFUSED = [
    ("two identities", lambda ids: {"identities": ids * 2}),
    ("no identity", lambda ids: {"identities": []}),
    ("retry_errors all", lambda ids: {"resume": "run-1", "retry_errors": "all"}),
    ("retry_errors arm", lambda ids: {"resume": "run-1", "retry_errors": "arm"}),
    ("retry_errors without resume", lambda ids: {"retry_errors": "transport"}),
    ("run_ids with resume", lambda ids: {"resume": "run-1", "run_ids": ["run-2"]}),
    ("metamorphic with resume", lambda ids: {"resume": "run-1", "metamorphic": True}),
]


@pytest.mark.criterion("C10")
@pytest.mark.parametrize("label,kwargs", REFUSED, ids=[r[0] for r in REFUSED])
def test_refused_combinations_call_and_write_nothing(kit, label, kwargs):
    # oracle: requirement 16 — each listed combination is a ValueError before any call
    cases = [kit.make_case(c) for c in "ab"]
    store = _stored_run(kit, cases)
    calls_before = list(store.calls)
    system = kit.FakeSystem()
    with pytest.raises(ValueError):
        _try(kit, store, system, cases, **kwargs(
            [kit.identity_for(cases, system.name)]))
    assert system.answered == []
    assert store.calls == calls_before


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@given(value=st.text(min_size=1, max_size=12).filter(lambda v: v != "transport"))
def test_only_transport_is_a_retry_choice(kit, value):
    # oracle: requirement 14 — retry_errors takes None or "transport" only
    cases = [kit.make_case(c) for c in "ab"]
    store = _stored_run(kit, cases)
    system = kit.FakeSystem()
    with pytest.raises(ValueError):
        _try(kit, store, system, cases, resume="run-1", retry_errors=value)
    assert system.answered == []
    (report,) = _try(kit, store, system, cases, resume="run-1", retry_errors="transport")
    assert report.run_id == "run-1"


# ---------------------------------------------------------------------------- run_suite.py


@pytest.fixture(scope="module")
def run_suite(kit):
    spec = importlib.util.spec_from_file_location("run_suite_erc_under_test", kit.RUN_SUITE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_suite_erc_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _parse_error(run_suite, capsys, tmp_path, argv):
    # A suite path that does not exist: if the parser lets a combination through, the run
    # fails on loading the suite at once, before any system is built or any call is made.
    missing = str(tmp_path / "no-such-suite.json")
    with pytest.raises(SystemExit) as exit_info:
        run_suite.main([*argv, "--suite-path", missing])
    assert exit_info.value.code == 2
    return capsys.readouterr().err.split("error:", 1)[-1]  # the message, not the usage line


CLI_REFUSED = [
    (["--retry-errors", "transport"], ["--retry-errors", "--resume"]),
    (["--resume", "eval-000000000001", "--no-store"], ["--resume", "--no-store"]),
    (["--resume", "eval-000000000001", "--rerun-failed", "last"],
     ["--resume", "--rerun-failed"]),
    (["--resume", "eval-000000000001", "--keyfree"], ["--resume", "--keyfree"]),
    (["--resume", "eval-000000000001", "--perturb-twins", "2", "--max-cost-usd", "1"],
     ["--resume", "--perturb-twins"]),
]


@pytest.mark.criterion("C10")
@pytest.mark.parametrize("argv,flags", CLI_REFUSED, ids=[" ".join(a) for a, _ in CLI_REFUSED])
def test_run_suite_refuses_combinations(run_suite, capsys, tmp_path, argv, flags):
    # oracle: requirement 17 — exit 2 with a message naming the flags
    message = _parse_error(run_suite, capsys, tmp_path, argv)
    for flag in flags:
        assert flag in message
    assert "unrecognized arguments" not in message


@pytest.mark.criterion("C10")
def test_run_suite_retry_errors_takes_only_transport(run_suite, capsys, tmp_path):
    # oracle: requirement 17 — argparse's choice check names the flag and the bad value
    message = _parse_error(run_suite, capsys, tmp_path,
                           ["--resume", "eval-000000000001", "--retry-errors", "all"])
    assert "--retry-errors" in message
    assert "invalid choice" in message
