"""C6: the judge is never from the generator's family; its version is stamped."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge import ClaimJudge, judge_version, model_family

_LLM_YAML = Path(__file__).resolve().parents[3] / "configs" / "llm" / "llm.yaml"
_PROVIDER = {
    name: row["provider"]
    for name, row in yaml.safe_load(_LLM_YAML.read_text(encoding="utf-8"))["models"].items()
}
_MODELS = sorted(_PROVIDER)


@pytest.mark.criterion("C6")
def test_model_family_examples():
    # oracle: the provider column of configs/llm/llm.yaml
    assert model_family("gpt-5.6-sol") == "openai"
    assert model_family("gpt-5-nano") == "openai"
    assert model_family("claude-opus-5-5") == "anthropic"
    assert model_family("deepseek-flash") == "deepseek"
    with pytest.raises(ValueError):
        model_family("llama-3-70b-instruct")


@pytest.mark.criterion("C6")
@pytest.mark.parametrize(
    ("judge_model", "generator"),
    [
        ("deepseek-flash", "deepseek-v4-pro"),
        ("gpt-5", "gpt-5.6-sol"),
        ("claude-haiku-5-5", "claude-opus-5-5"),
        ("llama-3-70b-instruct", "gpt-5.6-sol"),
        ("claude-opus-5-5", "llama-3-70b-instruct"),
    ],
)
def test_same_or_unknown_family_is_refused(judge_model, generator):
    # oracle: requirement 6 — same family, or a family that can't be checked, raises
    with pytest.raises(ValueError):
        ClaimJudge(model=judge_model, generator_model=generator)


@pytest.mark.criterion("C6")
def test_cross_family_builds_and_is_stamped(make_judge):
    # oracle: requirement 6 — the stamp is "<prompt version>:<model id>", default v1
    assert judge_version("v1", "claude-opus-5-5") == "v1:claude-opus-5-5"
    judge, adapter = make_judge(model="claude-opus-5-5", generator_model="deepseek-flash")
    assert judge.judge_version == "v1:claude-opus-5-5"
    other, _ = make_judge(model="gpt-5.6-sol", generator_model="claude-sonnet-5-5")
    assert other.judge_version == "v1:gpt-5.6-sol"
    assert adapter.calls == 0


@pytest.mark.criterion("C6")
@given(st.sampled_from(_MODELS))
def test_family_is_the_llm_yaml_provider(model):
    # oracle: configs/llm/llm.yaml, read independently here
    assert model_family(model) == _PROVIDER[model]


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(st.sampled_from(_MODELS), st.sampled_from(_MODELS))
def test_construction_raises_exactly_for_same_provider(judge_model, generator):
    # oracle: requirement 6 against llm.yaml's provider column
    if _PROVIDER[judge_model] == _PROVIDER[generator]:
        with pytest.raises(ValueError):
            ClaimJudge(model=judge_model, generator_model=generator)
    else:
        judge = ClaimJudge(model=judge_model, generator_model=generator)
        assert judge.judge_version == f"v1:{judge_model}"


@pytest.mark.criterion("C6")
@given(st.text(min_size=1, max_size=12), st.text(min_size=1, max_size=30))
def test_judge_version_format(prompt_version, model):
    # oracle: requirement 6's definition, f"{prompt_version}:{model}"
    assert judge_version(prompt_version, model) == f"{prompt_version}:{model}"
