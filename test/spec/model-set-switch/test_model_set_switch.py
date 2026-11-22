"""Spec tests for model-set-switch: a config feature switch between the current OpenAI models
and their replacements (docs/specs/model-set-switch.md).

Pure config functions — no network, no LLM, no DB.
"""

from __future__ import annotations

import importlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm import config
from src.llm.config import (
    MODEL_PRICING,
    SEC_TABLE_MODEL_CONFIGS,
    openai_model_set,
    replacement_models_enabled,
    xbrl_stage_models,
)

ENV_KEY = "XBRLRAG_USE_REPLACEMENT_MODELS"

# Oracle (C1): XBRL_QUERY_MODEL_TIERS and MODEL_TIERS["openai"] on main when the spec was
# written (2026-10-17), copied here.
CURRENT_STAGES = {
    "query_decomposer": "gpt-5-mini",
    "intent_router": "gpt-5-mini",
    "plan_generator": "gpt-5-mini",
    "synthesizer": "gpt-5-mini",
    "faithfulness_verifier": "gpt-5-nano",
    # added by baseline-arms (eval comparison arms, medium tier)
    "closed_book": "gpt-5-mini",
    "naive_rag": "gpt-5-mini",
}
CURRENT_SET = {"small": "gpt-5-nano", "medium": "gpt-5-mini", "frontier": "gpt-5"}

# arm-model-from-profile: the current set is the default profile's, which moves from openai
# (above) to openai-6.1 (docs/specs/openai-6-1-profile.md requirement 2) because the gpt-5
# family shuts down 2026-12-11. That spec pins the new set; the switch-off checks here hold
# on both sides.
SET_61 = {"small": "gpt-6-luna", "medium": "gpt-6.1-terra", "frontier": "gpt-6.1-sol"}
CURRENT_SETS = (CURRENT_SET, SET_61)


def _stages_of(models: dict[str, str]) -> dict[str, str]:
    """CURRENT_STAGES with each model replaced by the same tier's model of *models*."""
    tier_of = {model: tier for tier, model in CURRENT_SET.items()}
    return {stage: models[tier_of[model]] for stage, model in CURRENT_STAGES.items()}

# Oracle (C2): OpenAI deprecations page — gpt-5 -> gpt-5.6-sol, gpt-5-mini -> gpt-5.6-terra,
# gpt-5-nano -> gpt-5.6-luna.
REPLACEMENT_SET = {"small": "gpt-5.6-luna", "medium": "gpt-5.6-terra", "frontier": "gpt-5.6-sol"}
REPLACEMENT_OF = {
    "gpt-5": "gpt-5.6-sol", "gpt-5-mini": "gpt-5.6-terra", "gpt-5-nano": "gpt-5.6-luna",
}

# Oracle (C4): OpenAI pricing page, Standard short-context, read 2026-10-17; USD per 1M
# tokens divided by 1000 to give the per-1K rates MODEL_PRICING stores.
REPLACEMENT_RATES = {
    "gpt-5.6-sol": {"input": 4.00 / 1000, "cached_input": 0.40 / 1000, "output": 20.00 / 1000},
    "gpt-5.6-terra": {"input": 2.00 / 1000, "cached_input": 0.20 / 1000, "output": 12.00 / 1000},
    "gpt-5.6-luna": {"input": 0.20 / 1000, "cached_input": 0.02 / 1000, "output": 1.20 / 1000},
}

ON_WORDS = ["1", "true", "yes", "on"]
OFF_WORDS = ["0", "false", "no", "off"]


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_switch_off_keeps_todays_models():
    # oracle: CURRENT_STAGES / CURRENT_SET, copied from main at spec time, or the same
    # stages on SET_61 after arm-model-from-profile
    current = openai_model_set(False)
    assert current in CURRENT_SETS
    assert xbrl_stage_models(False) == _stages_of(current)


@pytest.mark.criterion("C1")
@given(st.sampled_from(sorted(CURRENT_STAGES)), st.booleans())
def test_returned_dicts_are_fresh_copies(stage, use_replacement):
    # oracle: property — mutating a returned dict never changes a later result
    first = xbrl_stage_models(use_replacement)
    assert stage in first
    expected = dict(first)
    first[stage] = "mutated"
    assert xbrl_stage_models(use_replacement) == expected
    tiers = openai_model_set(use_replacement)
    assert set(tiers) == {"small", "medium", "frontier"}
    tiers["small"] = "mutated"
    assert openai_model_set(use_replacement)["small"] != "mutated"


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_switch_on_selects_openais_named_replacements():
    # oracle: OpenAI deprecations page mapping (REPLACEMENT_SET)
    assert openai_model_set(True) == REPLACEMENT_SET
    assert xbrl_stage_models(True) == {
        "query_decomposer": "gpt-5.6-terra",
        "intent_router": "gpt-5.6-terra",
        "plan_generator": "gpt-5.6-terra",
        "synthesizer": "gpt-5.6-terra",
        "faithfulness_verifier": "gpt-5.6-luna",
        "closed_book": "gpt-5.6-terra",
        "naive_rag": "gpt-5.6-terra",
    }


@pytest.mark.criterion("C2")
@given(st.sampled_from(sorted(CURRENT_STAGES)))
def test_every_stage_keeps_its_tier_across_sets(stage):
    # oracle: property — switching sets maps each stage's model to the replacement of the
    # same tier (REPLACEMENT_OF), and both models belong to their set's tier map
    off, on = xbrl_stage_models(False), xbrl_stage_models(True)
    assert stage in off and stage in on
    if openai_model_set(False) == CURRENT_SET:  # before arm-model-from-profile
        assert off[stage] in REPLACEMENT_OF
        assert on[stage] == REPLACEMENT_OF[off[stage]]
    tier_off = {model: tier for tier, model in openai_model_set(False).items()}
    tier_on = {model: tier for tier, model in openai_model_set(True).items()}
    assert tier_off[off[stage]] == tier_on[on[stage]]


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_switch_is_off_by_default_and_env_words_are_read():
    # oracle: requirement — off without the key; documented on/off words
    assert replacement_models_enabled({}) is False
    assert replacement_models_enabled({"OTHER": "1"}) is False
    assert replacement_models_enabled({ENV_KEY: "true"}) is True
    assert replacement_models_enabled({ENV_KEY: " ON "}) is True
    assert replacement_models_enabled({ENV_KEY: "0"}) is False
    assert replacement_models_enabled({ENV_KEY: "No"}) is False


@pytest.mark.criterion("C3")
def test_unknown_env_value_raises():
    # oracle: requirement — fail loud on a value the switch does not understand
    for value in ("", "enabled", "2", "truee", "y"):
        with pytest.raises(ValueError):
            replacement_models_enabled({ENV_KEY: value})


@pytest.mark.criterion("C3")
@given(
    st.sampled_from(ON_WORDS + OFF_WORDS),
    st.sampled_from([str.lower, str.upper, str.title]),
    st.text(alphabet=" \t", max_size=3),
    st.text(alphabet=" \t", max_size=3),
)
def test_case_and_whitespace_do_not_change_the_reading(word, case, left, right):
    # oracle: property — the reading depends only on the stripped, case-folded word
    expected = word in ON_WORDS
    assert replacement_models_enabled({ENV_KEY: f"{left}{case(word)}{right}"}) is expected


@pytest.mark.criterion("C3")
@given(st.text(max_size=12))
def test_any_other_value_raises(value):
    # oracle: property — every value outside the vocabulary raises
    if value.strip().lower() in ON_WORDS + OFF_WORDS:
        assert replacement_models_enabled({ENV_KEY: value}) is (value.strip().lower() in ON_WORDS)
        return
    with pytest.raises(ValueError):
        replacement_models_enabled({ENV_KEY: value})


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_replacement_models_are_priced_and_configured():
    # oracle: OpenAI pricing page (REPLACEMENT_RATES); requirement for the config entries
    assert openai_model_set(True) == REPLACEMENT_SET
    assert openai_model_set(False) in CURRENT_SETS
    assert set(REPLACEMENT_RATES) == set(REPLACEMENT_SET.values())
    for model, rates in REPLACEMENT_RATES.items():
        assert model in MODEL_PRICING, model
        for key, value in rates.items():
            # tolerance: the rates are exact decimal list prices; rel covers float division
            assert MODEL_PRICING[model][key] == pytest.approx(value, rel=1e-9), (model, key)


@pytest.mark.criterion("C4")
@given(st.booleans())
def test_every_model_in_either_set_has_pricing_and_call_config(use_replacement):
    # oracle: property — no model a switch can select is missing a config key
    stage_models = set(xbrl_stage_models(use_replacement).values())
    tier_models = set(openai_model_set(use_replacement).values())
    assert stage_models and tier_models
    assert stage_models <= tier_models
    for model in tier_models:
        assert {"input", "output", "cached_input"} <= set(MODEL_PRICING.get(model, {})), model
        assert SEC_TABLE_MODEL_CONFIGS.get(model, {}).get("model") == model, model


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_module_constants_follow_the_switch_at_import():
    # oracle: requirement — constants equal the functions for the active setting
    enabled = replacement_models_enabled()
    assert config.XBRL_QUERY_MODEL_TIERS == xbrl_stage_models(enabled)
    assert config.MODEL_TIERS["openai"] == openai_model_set(enabled)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_env_override_moves_constants_on_reload(monkeypatch):
    # oracle: metamorphic — reloading the config with the override on, then off, moves the
    # module constants between the two sets
    try:
        monkeypatch.setenv(ENV_KEY, "1")
        on = importlib.reload(config)
        assert on.XBRL_QUERY_MODEL_TIERS == on.xbrl_stage_models(True)
        assert on.MODEL_TIERS["openai"] == on.openai_model_set(True)
        monkeypatch.setenv(ENV_KEY, "0")
        off = importlib.reload(config)
        assert off.XBRL_QUERY_MODEL_TIERS == off.xbrl_stage_models(False)
        assert off.MODEL_TIERS["openai"] == off.openai_model_set(False)
        assert off.XBRL_QUERY_MODEL_TIERS != on.xbrl_stage_models(True)
    finally:
        monkeypatch.delenv(ENV_KEY, raising=False)
        importlib.reload(config)
