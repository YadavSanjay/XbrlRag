"""C1: an arm built with no model takes the active profile's frontier model, at construction.

Oracle: requirement 1's table (``FRONTIER`` in amp_tables.py), written from the committed
profile rows as the earlier specs state them. Profiles whose frontier is the old literal
default (``gpt-5.6-sol``) are left out of the point checks, where they could not tell the
old behaviour from the new. That an explicit model wins and that an unpriced one (``""``
included) raises are graduated tests already (long-context-arm C6, long-context-arms C1,
frontier-sec-tools C17), true before and after this change, so they are not repeated here.
"""

from __future__ import annotations

import pytest
from amp_tables import ARM_NAMES, BUILD_NAMES, DISTINGUISHING, FRONTIER
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.systems import build_system


def _case() -> EvalCase:
    return EvalCase(id="c1", question="What was AAA total revenue in fiscal 2024?",
                    kind="lookup", ticker="AAA", fy=2024)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("profile", DISTINGUISHING)
@pytest.mark.parametrize("name", ARM_NAMES)
def test_each_arm_defaults_to_the_profiles_frontier(env, build_arm, name, profile):
    # oracle: requirement 1's table — the arm's model is FRONTIER[profile]
    with env(profile):
        arm, _ = build_arm(name)
    assert arm.model == FRONTIER[profile]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("profile", DISTINGUISHING)
@pytest.mark.parametrize("name", BUILD_NAMES)
def test_build_system_defaults_to_the_profiles_frontier(env, name, profile):
    # oracle: requirement 1 — build_system(name) without a model does the same
    with env(profile):
        system = build_system(name)
    assert system.model == FRONTIER[profile]


@pytest.mark.criterion("C1")
def test_with_nothing_chosen_the_default_is_gpt_6_1_sol(env, build_arm):
    # oracle: requirement 1's table, first row — the fallback profile openai-6.1
    for name in ARM_NAMES:
        with env(None):
            arm, _ = build_arm(name)
        assert arm.model == "gpt-6.1-sol", name


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("profile", DISTINGUISHING)
def test_the_answer_call_is_on_the_default_model(env, build_arm, profile):
    # oracle: requirement 1 — the model of the arm's answer calls is the default it was built
    # with, even when the profile changes before the call
    with env(profile):
        arm, adapter = build_arm("long_context_oracle")
    with env("openai-5.6"):
        answer = arm.answer(_case())
    assert answer.error is None
    assert [p.get("model") for p in adapter.params] == [FRONTIER[profile]]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@pytest.mark.parametrize("name", ARM_NAMES)
def test_the_model_is_fixed_at_construction(env, build_arm, name):
    # oracle: requirement 1 — an arm keeps the model of the profile it was built under; a
    # second arm built under another profile has that profile's model
    with env("anthropic"):
        first, _ = build_arm(name)
    with env("deepseek"):
        second, _ = build_arm(name)
    assert first.model == FRONTIER["anthropic"]
    assert second.model == FRONTIER["deepseek"]


@pytest.mark.criterion("C1")
@given(profile=st.sampled_from(DISTINGUISHING), name=st.sampled_from(ARM_NAMES))
def test_any_profile_and_arm(env, build_arm, profile, name):
    # oracle: requirement 1's table, over every distinguishing profile and every arm
    with env(profile):
        arm, _ = build_arm(name)
    assert arm.model == FRONTIER[profile]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    pair=st.lists(st.sampled_from(DISTINGUISHING), min_size=2, max_size=2, unique=True),
    name=st.sampled_from(ARM_NAMES),
)
def test_two_profiles_give_their_two_models(env, build_arm, pair, name):
    # oracle: requirement 1 — the same arm under two profiles has the two table entries
    models = []
    for profile in pair:
        with env(profile):
            arm, _ = build_arm(name)
        models.append(arm.model)
    assert models == [FRONTIER[p] for p in pair]
