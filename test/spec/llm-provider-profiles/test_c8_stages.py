"""C8: the XBRL stages and StageLLM read the active profile when they are built.

Oracle: requirement 9, with each stage's tier from XBRL_STAGE_TIERS (copied from main) and
the models from requirement 2's table. "Today" for the openai profile means main at f8ac973:
the stages ran gpt-5-mini / gpt-5-nano and sent ``temperature`` 1 (the gpt-5 family's fixed
value in SEC_TABLE_MODEL_CONFIGS).

Building a stage does no I/O (the API key is only stored; prompt templates are committed
files). The call checks drive ``StageLLM``, which takes a gateway: the fake adapter sits
behind a real ``LLMGateway``. The five stage classes take no gateway, so for them only the
model chosen at construction is checked.

A stage that raises while being built is reported with ``pytest.fail``, so the failure reads
as "did not construct" rather than as an unrelated error.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.eval.systems._stage_llm import StageLLM
from src.llm.profiles import load_committed_settings
from src.retriever.xbrl.intent_router import IntentRouter
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.query_decomposer import QueryDecomposer

# stage name in XBRL_STAGE_TIERS -> the class that runs it
STAGE_CLASSES = {
    "query_decomposer": QueryDecomposer,
    "intent_router": IntentRouter,
    "plan_generator": PlanGenerator,
    "synthesizer": XbrlRagSynthesizer,
    "faithfulness_verifier": XbrlFaithfulnessVerifier,
}
STAGE_LLM_STAGES = ("closed_book", "naive_rag")
ALL_STAGES = (*STAGE_CLASSES, *STAGE_LLM_STAGES)
# Today's models (main at f8ac973) per stage, under the openai profile.
TODAY_MODELS = {
    "query_decomposer": "gpt-5-mini",
    "intent_router": "gpt-5-mini",
    "plan_generator": "gpt-5-mini",
    "synthesizer": "gpt-5-mini",
    "faithfulness_verifier": "gpt-5-nano",
    "closed_book": "gpt-5-mini",
    "naive_rag": "gpt-5-mini",
}


def _use(llm_env, profile: str, *, openai_key: bool, anthropic_key: bool) -> None:
    llm_env.setenv("XBRLRAG_LLM_PROFILE", profile)
    if openai_key:
        llm_env.setenv("OPENAI_API_KEY", "sk-spec-offline")
    if anthropic_key:
        llm_env.setenv("ANTHROPIC_API_KEY", "sk-ant-spec-offline")


def _build(stage: str, gateway: Any = None) -> Any:
    """Builds the stage's class with no arguments (StageLLM with its stage name)."""
    try:
        if stage in STAGE_CLASSES:
            return STAGE_CLASSES[stage]()
        return StageLLM(stage, gateway)
    except Exception as exc:  # a construction failure is this test's failure
        pytest.fail(f"building {stage} raised {exc!r}")


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("stage", ALL_STAGES)
def test_anthropic_profile_with_only_its_key(llm_env, spec, stage):
    # oracle: requirement 9 — under anthropic with only ANTHROPIC_API_KEY set, the stage builds
    # and uses profiles["anthropic"][tier] for its tier
    _use(llm_env, "anthropic", openai_key=False, anthropic_key=True)
    built = _build(stage)
    assert built.model == spec.profile_table["anthropic"][spec.stage_tiers[stage]]


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("stage", ALL_STAGES)
def test_openai_profile_keeps_todays_models(llm_env, spec, stage):
    # oracle: requirement 9 — under openai with only OPENAI_API_KEY set, the model is today's,
    # which is the committed openai profile's model for the stage
    _use(llm_env, "openai", openai_key=True, anthropic_key=False)
    expected = load_committed_settings(profile="openai", environ={}).model_for_stage(stage)
    built = _build(stage)
    assert built.model == expected == TODAY_MODELS[stage]


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@pytest.mark.parametrize("stage", ALL_STAGES)
def test_profile_is_read_at_construction(llm_env, spec, stage):
    # oracle: requirement 9 — the profile is read when the stage is built, not at import:
    # switching XBRLRAG_LLM_PROFILE between builds in one process moves the model with it
    _use(llm_env, "openai", openai_key=True, anthropic_key=True)
    tier = spec.stage_tiers[stage]
    for profile in ("anthropic", "openai-5.6", "openai", "anthropic"):
        llm_env.setenv("XBRLRAG_LLM_PROFILE", profile)
        assert _build(stage).model == spec.profile_table[profile][tier], profile


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("stage", STAGE_LLM_STAGES)
def test_stage_llm_sends_no_temperature_to_claude(llm_env, spec, make_gateway, stage):
    # oracle: requirement 9 — a call to a model with temperature: false (every Claude model,
    # requirement 2) carries no temperature, on the profile's model for the stage's tier
    _use(llm_env, "anthropic", openai_key=False, anthropic_key=True)
    gateway, adapter, _ = make_gateway(prompt_tokens=10, completion_tokens=2)
    llm = _build(stage, gateway)
    llm.complete("What was AAA revenue in fiscal 2024?")
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert request["model"] == spec.profile_table["anthropic"][spec.stage_tiers[stage]]
    assert request.get("temperature") is None
    assert "temperature" not in (request.get("extra") or {})


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("stage", STAGE_LLM_STAGES)
def test_stage_llm_still_sends_temperature_to_openai(llm_env, make_gateway, stage):
    # oracle: requirement 9 — under openai the call parameters are today's: gpt-5-mini with
    # temperature 1 (SEC_TABLE_MODEL_CONFIGS on main), which the committed profile also names
    _use(llm_env, "openai", openai_key=True, anthropic_key=False)
    settings = load_committed_settings(profile="openai", environ={})
    assert settings.temperature_allowed(settings.model_for_stage(stage)) is True
    gateway, adapter, _ = make_gateway(prompt_tokens=10, completion_tokens=2)
    _build(stage, gateway).complete("What was AAA revenue in fiscal 2024?")
    assert len(adapter.requests) == 1
    assert adapter.requests[0]["model"] == TODAY_MODELS[stage]
    assert adapter.requests[0]["temperature"] == 1


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@pytest.mark.parametrize("stage", STAGE_LLM_STAGES)
def test_temperature_follows_the_models_setting_across_profiles(llm_env, spec, make_gateway, stage):
    # oracle: requirement 9 — the same StageLLM call sends a temperature exactly when the
    # profile's model allows one: Claude models (temperature: false) never, OpenAI models yes
    _use(llm_env, "openai", openai_key=True, anthropic_key=True)
    for profile in ("openai", "anthropic", "openai-5.6", "anthropic"):
        llm_env.setenv("XBRLRAG_LLM_PROFILE", profile)
        gateway, adapter, _ = make_gateway(prompt_tokens=10, completion_tokens=2)
        _build(stage, gateway).complete("q")
        request = adapter.requests[0]
        assert request["model"] == spec.profile_table[profile][spec.stage_tiers[stage]], profile
        sent = request.get("temperature") is not None
        assert sent is (profile != "anthropic"), profile
