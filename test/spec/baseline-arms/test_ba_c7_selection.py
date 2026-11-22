"""C7: system selection, the stages' model tier and their prompt registration."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prompts.prompt_registry import get_xbrl_registry
from src.eval.core.system import SystemUnderTest
from src.eval.systems import build_system, system_names
from src.eval.systems.closed_book import ClosedBookSystem
from src.eval.systems.naive_rag import NaiveRagSystem
from src.llm import config as llm_config

NAMES = (
    "xbrlrag_full",
    "closed_book",
    "naive_rag_plain",
    "naive_rag_filtered",
    "long_context",  # added by long-context-arm (requirement 7)
)
ARM_STAGES = ("closed_book", "naive_rag")
# long-context-arms: the arm is stored as long_context_oracle; long_context still builds it.
LONG_CONTEXT_NAMES = ("long_context", "long_context_oracle")


@pytest.mark.criterion("C7")
def test_system_names_exact():
    # oracle: C7's literal tuple, extended by long-context-arm C5. long-context-arms renames the
    # fifth and adds two, and pins the exact tuple in its own C1; this holds on both sides.
    assert system_names()[:4] == NAMES[:4]
    assert system_names()[4] in LONG_CONTEXT_NAMES


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("name", NAMES)
def test_build_system_by_name_offline(offline_env, name):
    # oracle: requirement 10 — every endpoint points at a closed local port, so a build
    # that reached a backend would fail here; answer() is never called
    system = build_system(name)
    assert isinstance(system, SystemUnderTest)
    assert system.name in (LONG_CONTEXT_NAMES if name == "long_context" else (name,))


@pytest.mark.criterion("C7")
def test_unknown_name_raises():
    # oracle: requirement 10 / C7 — anything outside system_names() is rejected
    with pytest.raises(ValueError):
        build_system("naive_rag")
    with pytest.raises(ValueError):
        build_system("openai_longcontext")


@pytest.mark.criterion("C7")
@given(name=st.text(min_size=0, max_size=30).filter(lambda s: s not in NAMES))
def test_any_other_name_raises(name):
    # oracle: C7 — the accepted set is exactly NAMES
    with pytest.raises(ValueError):
        build_system(name)


@pytest.mark.criterion("C7")
def test_arm_names_from_constructors(offline_env):
    # oracle: the Interface section — names by class and variant; constructing does no I/O
    assert ClosedBookSystem().name == "closed_book"
    assert NaiveRagSystem().name == "naive_rag_plain"
    assert NaiveRagSystem(filtered=True).name == "naive_rag_filtered"


@pytest.mark.criterion("C7")
def test_stages_on_the_synthesizer_medium_tier():
    # oracle: requirement 7 and the Interface note "XBRL_STAGE_TIERS gains closed_book and
    # naive_rag at medium"; the synthesizer is on medium
    tiers = llm_config.XBRL_STAGE_TIERS
    for stage in ARM_STAGES:
        assert stage in tiers
        assert tiers[stage] == "medium"
        assert tiers[stage] == tiers["synthesizer"]
    assert "closed_book" in llm_config.XBRL_QUERY_MODEL_TIERS
    assert "naive_rag" in llm_config.XBRL_QUERY_MODEL_TIERS


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@pytest.mark.parametrize("use_replacement", [False, True])
def test_stage_models_match_synthesizer_in_either_model_set(use_replacement):
    # oracle: requirement 7 — switching the model set moves both arms with the synthesizer
    # (xbrl_stage_models takes the set switch; C7 writes it without the argument)
    models = llm_config.xbrl_stage_models(use_replacement)
    for stage in ARM_STAGES:
        assert stage in models
        assert models[stage] == models["synthesizer"]


@pytest.mark.criterion("C7")
def test_prompt_registry_resolves_both_stages_at_v1():
    # oracle: requirement 7 — both stages registered, resolved by default at v1
    registry = get_xbrl_registry()
    for stage in ARM_STAGES:
        assert stage in registry.stages()
        resolved = registry.resolve(stage)
        assert resolved.stage == stage
        assert resolved.version == "v1"
        assert resolved.system_text.strip() != ""
        assert resolved.user_text.strip() != ""


@pytest.mark.criterion("C7")
@pytest.mark.parametrize(
    "arm, stage", [("closed_book", "closed_book"), ("naive_rag", "naive_rag")]
)
def test_calls_use_the_stage_and_the_synthesizer_model(make_gateway, make_embedder, make_qdrant,
                                                       point, make_case, reply, arm, stage):
    # oracle: requirement 7 — the call is made under the arm's stage, on the same model as
    # the synthesizer stage (whatever the active model set is)
    gateway, adapter = make_gateway(reply(3.0))
    if arm == "closed_book":
        system = ClosedBookSystem(gateway=gateway)
    else:
        system = NaiveRagSystem(
            gateway=gateway,
            embedder=make_embedder(),
            vector_db_client=make_qdrant([point("chunk-1", "Revenue was 3 dollars.")]),
        )
    system.answer(make_case())
    assert adapter.calls == 1
    assert adapter.stages == [stage]
    assert adapter.params[0]["model"] == llm_config.XBRL_QUERY_MODEL_TIERS["synthesizer"]
