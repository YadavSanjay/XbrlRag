"""C4: run_suite's ``--model`` help names no model; the frontier arm records its default.

Oracle: requirements 1 and 4, with requirement 1's table (``FRONTIER`` in amp_tables.py).
``--help`` only parses arguments: nothing is built and nothing runs.
"""

from __future__ import annotations

import re

import pytest
from amp_tables import COMMITTED_MODELS, DISTINGUISHING, FRONTIER
from hypothesis import given
from hypothesis import strategies as st


def _model_help(run_suite_module, capsys) -> str:
    """The ``--model`` entry of ``run_suite.py --help``, whitespace-normalised."""
    capsys.readouterr()
    with pytest.raises(SystemExit):
        run_suite_module.main(["--help"])
    lines = capsys.readouterr().out.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.strip().startswith("--model ")]
    assert len(starts) == 1, lines
    entry = [lines[starts[0]].strip().removeprefix("--model MODEL")]
    for line in lines[starts[0] + 1:]:
        if not line.strip() or line.strip().startswith("-"):
            break
        entry.append(line.strip())
    return " ".join(" ".join(entry).split())


@pytest.mark.criterion("C4")
def test_model_help_names_no_model(run_suite_module, capsys):
    # oracle: requirement 4 — no model ID in the help; the default is the profile's frontier
    entry = _model_help(run_suite_module, capsys)
    assert "gpt-5.6-sol" not in entry
    for model in COMMITTED_MODELS:
        assert not re.search(rf"(?<![\w.-]){re.escape(model)}(?![\w.-])", entry), model
    assert "frontier" in entry
    assert "profile" in entry


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("profile", DISTINGUISHING)
def test_the_frontier_arm_records_its_default(env, build_arm, profile):
    # oracle: requirement 1 — run_config()["model"] is the table's entry for the profile the
    # arm was built under, even when the profile changes afterwards
    with env(profile):
        arm, _ = build_arm("frontier_sec_tools")
    with env("openai-5.6"):
        config = arm.run_config()
    assert config["model"] == FRONTIER[profile]


@pytest.mark.criterion("C4")
@given(profile=st.sampled_from(DISTINGUISHING))
def test_any_profile_is_recorded(env, build_arm, profile):
    # oracle: requirement 1's table, over every distinguishing profile
    with env(profile):
        arm, _ = build_arm("frontier_sec_tools")
        assert arm.run_config()["model"] == FRONTIER[profile]
