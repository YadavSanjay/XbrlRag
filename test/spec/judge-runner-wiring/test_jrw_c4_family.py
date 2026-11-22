"""C4: a judge from the generator profile's family is refused, before anything runs."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.judge_wiring import check_judge_family, run_graded
from src.eval.core.runner import RunnerConfig
from src.eval.core.system import SystemAnswer

_LLM_YAML = Path(__file__).resolve().parents[3] / "configs" / "llm" / "llm.yaml"
with open(_LLM_YAML, encoding="utf-8") as _f:
    _CONFIG = yaml.safe_load(_f)
MODELS = sorted(_CONFIG["models"])
PROFILES = sorted(_CONFIG["profiles"])


def _profile_families(profile: str) -> set[str]:
    tiers = _CONFIG["profiles"][profile]
    return {_CONFIG["models"][tiers[t]]["provider"] for t in ("small", "medium", "frontier")}


@pytest.mark.criterion("C4")
def test_point_cases():
    # oracle: configs/llm/llm.yaml — claude-* are anthropic, gpt-* openai, deepseek-* deepseek
    with pytest.raises(ValueError):
        check_judge_family("claude-haiku-5-5", "anthropic")
    assert check_judge_family("claude-haiku-5-5", "deepseek") is None
    assert check_judge_family("claude-haiku-5-5", "openai-5.6") is None
    with pytest.raises(ValueError):
        check_judge_family("gpt-5.6-luna", "openai")
    with pytest.raises(ValueError):
        check_judge_family("gpt-5.6-luna", "openai-5.6")
    assert check_judge_family("gpt-5.6-luna", "anthropic") is None
    with pytest.raises(ValueError):
        check_judge_family("claude-haiku-5-5", "no-such-profile")
    with pytest.raises(ValueError):
        check_judge_family("no-such-model", "deepseek")


@pytest.mark.criterion("C4")
def test_run_refused_before_any_call(make_judge, make_case, fake_system):
    # oracle: requirement 7 — a same-family profile, or a judge without a profile, raises
    # ValueError with the system never called and no provider call
    cases = [make_case("t1", kind="narrative", expected_text="Ref.", key_points=["a"]),
             make_case("l1", expected_value=1.0)]
    cfg = RunnerConfig(max_workers=1, progress_every=0)

    judge, adapter = make_judge()  # claude-opus-5-5, anthropic
    system = fake_system({"t1": SystemAnswer(text="Answer.")})
    with pytest.raises(ValueError):
        run_graded(cases, system, judge=judge, generator_profile="anthropic", config=cfg)
    assert system.answered == []
    assert adapter.calls == 0

    system = fake_system({"t1": SystemAnswer(text="Answer.")})
    with pytest.raises(ValueError):
        run_graded(cases, system, judge=judge, generator_profile=None, config=cfg)
    assert system.answered == []
    assert adapter.calls == 0

    # the cross-family profile runs, as the control
    system = fake_system({"t1": SystemAnswer(text="Answer.")})
    report = run_graded(cases, system, judge=judge, generator_profile="deepseek", config=cfg)
    assert sorted(system.answered) == ["l1", "t1"]
    assert adapter.calls == 1
    assert len(report.results) == 2


@pytest.mark.criterion("C4")
@given(model=st.sampled_from(MODELS), profile=st.sampled_from(PROFILES))
def test_refused_exactly_on_a_shared_family(model, profile):
    # oracle: requirement 6 over configs/llm/llm.yaml, read here — raises iff the model's
    # provider is a provider of one of the profile's three models
    clash = _CONFIG["models"][model]["provider"] in _profile_families(profile)
    if clash:
        with pytest.raises(ValueError):
            check_judge_family(model, profile)
    else:
        assert check_judge_family(model, profile) is None
