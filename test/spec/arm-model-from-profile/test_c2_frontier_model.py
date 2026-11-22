"""C2: ``frontier_model`` is the active (or given) profile's frontier model.

Oracle: requirement 1's table (``FRONTIER`` in amp_tables.py) and requirement 2.
"""

from __future__ import annotations

import pytest
from amp_tables import FRONTIER
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import LlmConfigError, load_committed_settings
from src.llm.stage_models import frontier_model


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("profile", sorted(FRONTIER))
def test_the_environments_profile(env, profile):
    # oracle: requirement 2 — with settings None, the profile active now (XBRLRAG_LLM_PROFILE)
    with env(profile):
        assert frontier_model() == FRONTIER[profile]


@pytest.mark.criterion("C2")
def test_nothing_chosen_is_the_fallbacks_frontier(env):
    # oracle: requirements 2 and 3 — no profile anywhere: openai-6.1's frontier, gpt-6.1-sol
    with env(None):
        assert frontier_model() == "gpt-6.1-sol"


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("profile", sorted(FRONTIER))
def test_given_settings_win_over_the_environment(env, profile):
    # oracle: requirement 2 — the given settings' frontier, whatever the environment says
    settings = load_committed_settings(profile=profile, environ={})
    with env("anthropic" if profile != "anthropic" else "deepseek"):
        assert frontier_model(settings) == FRONTIER[profile]


@pytest.mark.criterion("C2")
def test_an_unknown_profile_raises(env):
    # oracle: requirement 2 — an unknown profile raises LlmConfigError, as resolve_stage does
    with env("no-such-profile"), pytest.raises(LlmConfigError):
        frontier_model()


@pytest.mark.criterion("C2")
@given(profile=st.sampled_from(sorted(FRONTIER)), via_env=st.booleans())
def test_every_committed_profile(env, profile, via_env):
    # oracle: requirement 1's table, through the environment or through given settings
    if via_env:
        with env(profile):
            got = frontier_model()
    else:
        got = frontier_model(load_committed_settings(profile=profile, environ={}))
    assert got == FRONTIER[profile]
