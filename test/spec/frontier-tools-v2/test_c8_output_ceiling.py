"""C8: each model turn asks for the model's own per-call output ceiling."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.eval.systems import frontier_sec_tools

FIND = [("find_company", {"query": "AAA"})]
LLM_YAML = Path(__file__).resolve().parents[3] / "configs" / "llm" / "llm.yaml"
# configs/llm/llm.yaml's max_output_tokens, read by hand on 2026-11-06.
EXPECTED = {
    "deepseek-v4-pro": 16_000,
    "claude-sonnet-5-5": 16_000,
    "claude-opus-5-5": 16_000,
    "deepseek-flash": 8_000,
}


@pytest.mark.criterion("C8")
@pytest.mark.parametrize(("model", "expected"), sorted(EXPECTED.items()))
def test_each_turn_asks_for_the_models_ceiling(make_arm, make_case, model, expected):
    # oracle: configs/llm/llm.yaml max_output_tokens (EXPECTED); run_config records the value
    # sent, with chars_per_token 1.75
    arm, adapter, _ = make_arm([FIND], model=model)
    arm.answer(make_case())
    assert adapter.calls == 2
    assert [p["max_completion_tokens"] for p in adapter.params] == [expected, expected]
    config = arm.run_config()
    assert config.get("max_completion_tokens") == expected
    # tolerance: a constant, compared exactly up to float representation
    assert config.get("chars_per_token") == pytest.approx(1.75, abs=1e-12)


@pytest.mark.criterion("C8")
def test_the_arms_default_is_sixteen_thousand():
    # oracle: requirement 8
    assert frontier_sec_tools.MAX_COMPLETION_TOKENS == 16_000


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@pytest.mark.parametrize("model", sorted(EXPECTED))
def test_the_value_sent_is_the_yaml_ceiling_on_every_turn_and_in_run_config(make_arm, make_case,
                                                                            model):
    # oracle: metamorphic — the committed llm.yaml row caps the arm's 16,000: every turn of a
    # longer case, the final tool_choice="none" turn included, sends min(16,000, ceiling), and
    # run_config reports the same number
    ceiling = yaml.safe_load(LLM_YAML.read_text(encoding="utf-8"))["models"][model][
        "max_output_tokens"]
    wanted = min(16_000, int(ceiling))
    arm, adapter, _ = make_arm([FIND] * 5, model=model, max_steps=2)
    arm.answer(make_case())
    assert [p["tool_choice"] for p in adapter.params] == ["auto", "auto", "none"]
    assert {p["max_completion_tokens"] for p in adapter.params} == {wanted}
    assert arm.run_config().get("max_completion_tokens") == wanted
    assert wanted == EXPECTED[model]
