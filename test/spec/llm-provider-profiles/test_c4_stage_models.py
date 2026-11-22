"""C4: stage -> model is the active profile's model for the stage's tier.

Oracle: requirements 2 and 4 — ``profiles[profile][XBRL_STAGE_TIERS[stage]]``, with the
stage tiers copied from main (conftest ``STAGE_TIERS``) and the profiles from requirement 2.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.config import XBRL_STAGE_TIERS
from src.llm.profiles import LlmConfigError, load_committed_settings, load_llm_settings

PROFILES = ("openai", "openai-5.6", "anthropic")
STAGES = (
    "query_decomposer",
    "intent_router",
    "plan_generator",
    "synthesizer",
    "faithfulness_verifier",
    "closed_book",
    "naive_rag",
)
PRICED_MODELS = (
    "gpt-5-nano", "gpt-5-mini", "gpt-5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
    "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5",
)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("profile", PROFILES)
def test_every_stage_gets_its_tiers_model(llm_env, spec, profile):
    # oracle: requirements 2 and 4 — profiles[profile][XBRL_STAGE_TIERS[stage]], for every
    # stage in XBRL_STAGE_TIERS (whose tiers are unchanged from main)
    assert dict(XBRL_STAGE_TIERS) == spec.stage_tiers
    settings = load_committed_settings(profile=profile, environ={})
    for stage, tier in spec.stage_tiers.items():
        assert settings.model_for_stage(stage) == spec.profile_table[profile][tier], stage


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("profile", PROFILES)
def test_model_for_tier_is_the_tables_model(llm_env, spec, profile):
    # oracle: requirement 2's table
    settings = load_committed_settings(profile=profile, environ={})
    for tier, model in spec.profile_table[profile].items():
        assert settings.model_for_tier(tier) == model


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("stage", ["long_context_x", "retriever", "", "synthesiser"])
def test_unknown_stage_raises(llm_env, stage):
    # oracle: requirement 4 — a stage outside XBRL_STAGE_TIERS raises LlmConfigError
    settings = load_committed_settings(profile="anthropic", environ={})
    with pytest.raises(LlmConfigError):
        settings.model_for_stage(stage)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("tier", ["large", "", "tiny", "haiku"])
def test_unknown_tier_raises(make_doc, make_pricing, tier):
    # oracle: requirement 1 — the tiers are small, medium and frontier; any other raises
    settings = load_llm_settings(make_doc(), make_pricing(), profile="openai", environ={})
    with pytest.raises(LlmConfigError):
        settings.model_for_tier(tier)


@pytest.mark.criterion("C4")
@given(
    small=st.sampled_from(PRICED_MODELS),
    medium=st.sampled_from(PRICED_MODELS),
    frontier=st.sampled_from(PRICED_MODELS),
    stage=st.sampled_from(STAGES),
)
def test_any_profile_maps_each_stage_through_its_tier(make_doc, make_pricing, spec, small, medium,
                                                      frontier, stage):
    # oracle: requirement 4 — for an arbitrary profile, model_for_stage(stage) is that
    # profile's model for XBRL_STAGE_TIERS[stage]
    doc = make_doc()
    row = {"small": small, "medium": medium, "frontier": frontier}
    doc["profiles"]["spec-random"] = dict(row)
    settings = load_llm_settings(doc, make_pricing(), profile="spec-random", environ={})
    assert settings.model_for_stage(stage) == row[spec.stage_tiers[stage]]
    assert settings.model_for_tier("frontier") == frontier
