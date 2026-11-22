"""C7: the cheapest passing judge outside the generator's families."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import CalibrationResult, choose_judge

ANS = "a" * 64
LAB = "b" * 64


def _result(model, *, cost, passed, version="v1", ans=ANS, lab=LAB, family=None, providers=None):
    fam = family if family is not None else providers[model]
    return CalibrationResult(
        judge_version=f"{version}:{model}",
        model=model,
        family=fam,
        n_answers=100,
        n_judge_errors=0,
        dimensions={},
        threshold=0.7,
        min_answers=100,
        passed=passed,
        cost_usd=cost,
        answer_set_sha256=ans,
        label_set_sha256=lab,
    )


@pytest.mark.criterion("C7")
def test_point_choices():
    # oracle: requirement 11 and configs/llm/llm.yaml - deepseek and openai-6.1 profiles
    # don't use Anthropic, so the cheaper passing haiku wins; the anthropic profile
    # excludes it, leaving gpt-6-luna; the cheap failing nano is never chosen
    cheap = _result("gpt-5-nano", cost=0.01, passed=False, family="openai")
    mid = _result("claude-haiku-5-5", cost=0.05, passed=True, family="anthropic")
    dear = _result("gpt-6-luna", cost=0.20, passed=True, family="openai")
    results = [cheap, mid, dear]
    assert choose_judge(results, generator_profile="deepseek") is mid
    assert choose_judge(results, generator_profile="openai-6.1") is mid
    assert choose_judge(results, generator_profile="anthropic") is dear
    assert choose_judge([cheap, mid], generator_profile="anthropic") is None
    # equal cost: the smaller judge_version wins
    sonnet = _result("claude-sonnet-5-5", cost=0.05, passed=True, family="anthropic")
    assert choose_judge([sonnet, mid], generator_profile="deepseek") is mid


@pytest.mark.criterion("C7")
def test_inconsistent_candidates_raise():
    # oracle: requirement 11 - one labelled set calibrates every candidate
    mid = _result("claude-haiku-5-5", cost=0.05, passed=True, family="anthropic")
    for other in (
        _result("gpt-6-luna", cost=0.2, passed=True, family="openai", ans="c" * 64),
        _result("gpt-6-luna", cost=0.2, passed=True, family="openai", lab="d" * 64),
        _result("claude-haiku-5-5", cost=0.2, passed=True, family="anthropic"),
    ):
        with pytest.raises(ValueError):
            choose_judge([mid, other], generator_profile="deepseek")
    with pytest.raises(ValueError):
        choose_judge([mid], generator_profile="no-such-profile")


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(st.data())
def test_minimum_cost_eligible(env, data):
    # oracle: requirement 11 closed form over configs/llm/llm.yaml read in the test
    cfg = env.llm_yaml()
    providers = {m: row["provider"] for m, row in cfg["models"].items()}
    profiles = cfg["profiles"]
    pick = st.sampled_from(sorted(providers))
    models = data.draw(st.lists(pick, min_size=1, max_size=6, unique=True))
    results = [
        _result(
            m,
            cost=data.draw(st.sampled_from([0.01, 0.02, 0.05, 0.1])),
            passed=data.draw(st.booleans()),
            providers=providers,
        )
        for m in models
    ]
    profile = data.draw(st.sampled_from(sorted(profiles)))
    banned = {providers[profiles[profile][t]] for t in ("small", "medium", "frontier")}
    eligible = [r for r in results if r.passed and providers[r.model] not in banned]
    expected = min(eligible, key=lambda r: (r.cost_usd, r.judge_version)) if eligible else None
    assert choose_judge(results, generator_profile=profile) is expected
    shuffled = data.draw(st.permutations(results))
    assert choose_judge(shuffled, generator_profile=profile) is expected
    # adding a failing candidate cheaper than all changes nothing
    spare = [m for m in sorted(providers) if m not in models]
    if spare:
        loser = _result(spare[0], cost=0.0, passed=False, providers=providers)
        assert choose_judge([loser, *results], generator_profile=profile) is expected
