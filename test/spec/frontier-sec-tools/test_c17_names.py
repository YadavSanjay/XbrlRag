"""C17: the name, the build, and the constructor's checks."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sec_payloads import AS_OF

from src.eval.core.system import SystemUnderTest
from src.eval.sec_tools.toolbox import tools_fingerprint
from src.eval.systems import build_system, system_names
from src.eval.systems.frontier_sec_tools import FrontierSecToolsSystem
from src.llm.config import MODEL_PRICING

NAMES = (
    "xbrlrag_full",
    "closed_book",
    "naive_rag_plain",
    "naive_rag_filtered",
    "long_context_oracle",
    "long_context_resolved",
    "long_context_multi",
)


def _build(**kwargs):
    assert "frontier_sec_tools" in system_names()
    return build_system("frontier_sec_tools", **kwargs)


@pytest.mark.criterion("C17")
def test_the_name_follows_the_existing_names():
    # oracle: requirement 29 — no exact tuple, so that html_rag can add its own in either order
    names = system_names()
    assert names[: len(NAMES)] == NAMES
    assert "frontier_sec_tools" in names[len(NAMES) :]
    assert set(names[len(NAMES) :]) <= {"frontier_sec_tools", "html_rag"}


@pytest.mark.criterion("C17")
def test_the_arm_builds_offline(offline_env):
    # oracle: requirement 29 — every backend is a closed port, and no SEC setting is present;
    # a build that reached anything would fail. answer() is never called
    system = _build()
    assert isinstance(system, SystemUnderTest)
    assert isinstance(system, FrontierSecToolsSystem)
    assert system.name == "frontier_sec_tools"
    # arm-model-from-profile replaces the literal default gpt-5.6-sol with the active
    # profile's frontier model and pins that in its own C1; on either side it is priced.
    assert system.model in MODEL_PRICING


@pytest.mark.criterion("C17")
def test_the_model_passes_through(offline_env):
    # oracle: requirement 29
    assert _build(model="claude-opus-5-5").model == "claude-opus-5-5"


@pytest.mark.criterion("C17")
def test_an_unpriced_model_is_refused(offline_env):
    # oracle: the run's cost cap needs a price, as for the long-context arms
    with pytest.raises(ValueError, match="(?i)pric"):
        FrontierSecToolsSystem(model="no-such-model")


@pytest.mark.criterion("C17")
@pytest.mark.parametrize(
    "kwargs",
    [{"max_steps": 0}, {"max_tool_calls": 0}, {"max_cost_usd": 0.0}, {"max_input_tokens": 0},
     {"max_input_tokens": 250001}],
)
def test_a_limit_out_of_range_is_refused(offline_env, kwargs):
    # oracle: requirement 22, and the Interface's Raises
    with pytest.raises(ValueError):
        FrontierSecToolsSystem(**kwargs)


@pytest.mark.criterion("C17")
def test_run_config(make_arm):
    # oracle: requirement 29 — what is published with the results
    arm, _, _ = make_arm(max_steps=7, max_tool_calls=30, max_cost_usd=1.5)
    config = arm.run_config()
    assert config["model"] == "gpt-5.6-sol"
    # re-spec (frontier-tools-v2 requirement 11): the default prompt is v2
    assert config["prompt_version"] == "v2"
    assert config["tools_fingerprint"] == tools_fingerprint()
    assert config["max_steps"] == 7
    assert config["max_tool_calls"] == 30
    # tolerance: the value given, unchanged
    assert config["max_cost_usd"] == pytest.approx(1.5, abs=1e-12)
    assert config["max_input_tokens"] == 250000
    assert config["sec_snapshot"]["as_of"] == AS_OF
    assert config["sec_snapshot"]["urls"] == 0


@pytest.mark.criterion("C17")
def test_the_defaults_are_the_specs(make_arm):
    # oracle: requirement 22's defaults
    config = make_arm()[0].run_config()
    assert (config["max_steps"], config["max_tool_calls"]) == (20, 60)
    # tolerance: the default, unchanged
    assert config["max_cost_usd"] == pytest.approx(2.0, abs=1e-12)


@pytest.mark.criterion("C17")
def test_the_snapshot_grows_with_a_run(make_arm, make_case):
    # oracle: requirements 10, 29 — one URL read, one held, one fetched
    arm, _, _ = make_arm([[("find_company", {"query": "AAA"})]])
    arm.answer(make_case())
    snapshot = arm.run_config()["sec_snapshot"]
    assert snapshot["urls"] == 1
    assert snapshot["fetched"] == 1


@pytest.mark.criterion("C17")
@given(steps=st.integers(1, 50), calls=st.integers(1, 200))
def test_any_positive_limits_are_accepted_and_reported(make_arm, steps, calls):
    # oracle: requirement 29 — run_config reports what the arm was given
    arm, _, _ = make_arm(max_steps=steps, max_tool_calls=calls)
    config = arm.run_config()
    assert (config["max_steps"], config["max_tool_calls"]) == (steps, calls)
