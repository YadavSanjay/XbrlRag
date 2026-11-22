"""C9: compatibility — the old model-set helpers, the import-time stage table, run_suite.

Oracle: requirement 10, with requirement 2's table for the profiles.

``XBRL_QUERY_MODEL_TIERS`` is built when ``src.llm.config`` is imported, so it is checked in
a child Python process per environment: setting a variable here after import would change
nothing, and reloading or patching the config module is not allowed in spec tests.

``run_suite.main`` runs a one-case synthetic suite on the closed-book arm with
``XBRLRAG_RUN_MAX_LLM_CALLS=0``: the run budget halts the case before any provider call, so
nothing leaves the process, and ``--no-store`` keeps the database out of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.config import openai_model_set
from src.llm.profiles import load_committed_settings

TIERS = ("small", "medium", "frontier")
# arm-model-from-profile makes openai-6.1 the fallback profile (requirement 3, row 4) and the
# legacy "current" set, and pins both in its own spec; these tests accept either side.
# The openai-6.1 row is docs/specs/openai-6-1-profile.md requirement 2.
PROFILE_61 = {"small": "gpt-6-luna", "medium": "gpt-6.1-terra", "frontier": "gpt-6.1-sol"}
FALLBACK_NAMES = ("openai", "openai-6.1")
_RESULT_TAG = "C9RESULT "
_CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from src.llm import config
print("C9RESULT " + json.dumps(dict(config.XBRL_QUERY_MODEL_TIERS)))
"""


def _import_time_tiers(repo_root, env_updates: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in {"XBRLRAG_LLM_PROFILE", "XBRLRAG_USE_REPLACEMENT_MODELS",
                        "ARAG_LLM_PROFILE", "ARAG_USE_REPLACEMENT_MODELS"}}
    env.update(env_updates)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(repo_root)],
        cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(_RESULT_TAG)]
    assert len(lines) == 1, proc.stdout[-3000:]
    return json.loads(lines[0][len(_RESULT_TAG):])


def _row(spec, profile: str) -> dict[str, str]:
    return PROFILE_61 if profile == "openai-6.1" else spec.profile_table[profile]


def _expected_stage_models(spec, profile: str) -> dict[str, str]:
    return {stage: _row(spec, profile)[tier] for stage, tier in spec.stage_tiers.items()}


def _current_name(spec) -> str:
    """The fallback profile the legacy current set equals, before or after the change."""
    current = openai_model_set(False)
    names = [name for name in FALLBACK_NAMES if _row(spec, name) == current]
    assert names, current
    return names[0]


@pytest.mark.criterion("C9")
def test_openai_model_sets_equal_the_openai_profiles(llm_env, spec):
    # oracle: requirement 10 — openai_model_set(False) / (True) equal the openai / openai-5.6
    # profiles (requirement 2's table)
    for use_replacement, name in ((False, _current_name(spec)), (True, "openai-5.6")):
        settings = load_committed_settings(profile=name, environ={})
        assert openai_model_set(use_replacement) == {t: settings.model_for_tier(t) for t in TIERS}
        assert openai_model_set(use_replacement) == _row(spec, name)


@pytest.mark.criterion("C9")
@given(use_replacement=st.booleans(), tier=st.sampled_from(TIERS))
def test_any_tier_of_either_set_is_the_profiles(spec, use_replacement, tier):
    # oracle: requirement 10 — tier by tier, the legacy set and the matching profile agree
    name = "openai-5.6" if use_replacement else _current_name(spec)
    settings = load_committed_settings(profile=name, environ={})
    assert openai_model_set(use_replacement)[tier] == settings.model_for_tier(tier)
    assert settings.model_for_tier(tier) == _row(spec, name)[tier]


@pytest.mark.criterion("C9")
def test_import_time_stage_models_follow_the_anthropic_profile(spec):
    # oracle: requirement 10 — XBRL_QUERY_MODEL_TIERS equals the active profile's stage models
    # at import; under XBRLRAG_LLM_PROFILE=anthropic that is requirement 2's Claude row
    got = _import_time_tiers(spec.repo_root, {"XBRLRAG_LLM_PROFILE": "anthropic"})
    assert got == _expected_stage_models(spec, "anthropic")


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_import_time_stage_models_follow_every_way_of_choosing(spec):
    # oracle: requirement 10 with requirement 3 — each way of choosing a profile at import
    # gives that profile's stage models
    cases = [
        ({}, None),
        ({"XBRLRAG_USE_REPLACEMENT_MODELS": "1"}, "openai-5.6"),
        ({"XBRLRAG_LLM_PROFILE": "openai-5.6"}, "openai-5.6"),
        ({"XBRLRAG_LLM_PROFILE": "anthropic", "XBRLRAG_USE_REPLACEMENT_MODELS": "1"}, "anthropic"),
        ({"XBRLRAG_LLM_PROFILE": "", "XBRLRAG_USE_REPLACEMENT_MODELS": "0"}, None),
    ]
    for env_updates, profile in cases:
        got = _import_time_tiers(spec.repo_root, env_updates)
        if profile is None:  # row 4: the fallback, either side of arm-model-from-profile
            assert got in [_expected_stage_models(spec, n) for n in FALLBACK_NAMES], env_updates
            continue
        assert got == _expected_stage_models(spec, profile), env_updates


def _run(run_suite_module, argv: list[str]) -> int | str | None:
    """Runs ``main``; an argparse or explicit exit is returned as its code."""
    try:
        return run_suite_module.main(argv)
    except SystemExit as exc:
        return exc.code


def _offline_run_env(llm_env) -> None:
    llm_env.setenv("XBRLRAG_RUN_MAX_LLM_CALLS", "0")
    llm_env.setenv("OPENAI_API_KEY", "sk-spec-offline")
    llm_env.setenv("ANTHROPIC_API_KEY", "sk-ant-spec-offline")
    llm_env.setenv("XBRLRAG_LLM_PROFILE", "")
    llm_env.setenv("XBRL_DB_HOST", "127.0.0.1")
    llm_env.setenv("XBRL_DB_PORT", "9")


def _help_text(run_suite_module, capsys) -> str:
    capsys.readouterr()
    _run(run_suite_module, ["--help"])
    return capsys.readouterr().out


@pytest.mark.criterion("C9")
def test_run_suite_records_the_llm_profile(llm_env, run_suite_module, tiny_suite, tmp_path, capsys):
    # oracle: requirement 10 — run_suite.py --llm-profile anthropic records
    # "llm_profile": "anthropic" in the run's config
    assert "--llm-profile" in _help_text(run_suite_module, capsys)
    _offline_run_env(llm_env)
    out = tmp_path / "report.json"
    rc = _run(run_suite_module, ["--suite-path", str(tiny_suite), "--system", "closed_book",
                                 "--llm-profile", "anthropic", "--no-store", "--workers", "1",
                                 "--out", str(out)])
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["config"]["llm_profile"] == "anthropic"


@pytest.mark.criterion("C9")
def test_run_suite_rejects_an_unknown_profile(llm_env, run_suite_module, tiny_suite, tmp_path,
                                              capsys):
    # oracle: requirement 10 — an unknown --llm-profile name exits with an error (and the
    # option itself is known, so the error is about the name)
    assert "--llm-profile" in _help_text(run_suite_module, capsys)
    _offline_run_env(llm_env)
    out = tmp_path / "report.json"
    rc = _run(run_suite_module, ["--suite-path", str(tiny_suite), "--system", "closed_book",
                                 "--llm-profile", "no-such-profile", "--no-store", "--workers", "1",
                                 "--out", str(out)])
    assert rc not in (0, None)
    assert not out.exists()


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_run_suite_records_whichever_profile_is_given(llm_env, run_suite_module, tiny_suite,
                                                       tmp_path, capsys):
    # oracle: requirement 10 — the recorded profile is the one passed, for each committed name
    assert "--llm-profile" in _help_text(run_suite_module, capsys)
    _offline_run_env(llm_env)
    for name in ("openai", "openai-5.6", "anthropic"):
        out = tmp_path / f"report-{name}.json"
        rc = _run(run_suite_module, ["--suite-path", str(tiny_suite), "--system", "closed_book",
                                     "--llm-profile", name, "--no-store", "--workers", "1",
                                     "--out", str(out)])
        assert rc == 0, name
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["config"]["llm_profile"] == name
