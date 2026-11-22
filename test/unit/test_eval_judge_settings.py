"""The judge configuration (configs/eval/judge.yaml) and the eval prompt registry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from prompts.prompt_registry import get_eval_registry
from src.eval.core.graders.judge import (
    JUDGE_CONFIG_FILE,
    JUDGE_STAGE,
    ClaimJudge,
    load_judge_settings,
    model_family,
)


def test_committed_config_loads_and_judges_are_cross_family():
    settings = load_judge_settings()
    assert settings.prompt_version == "v2"
    # The development judge grades DeepSeek-generated answers, so it is never DeepSeek.
    assert model_family(settings.development_model) != "deepseek"
    ClaimJudge(model=settings.development_model, generator_model="deepseek-flash")
    for model in settings.milestone_models:
        ClaimJudge(model=model, generator_model="claude-opus-5-5")


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "judge.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_unknown_key_and_unknown_model_fail_loud(tmp_path):
    base = yaml.safe_load(JUDGE_CONFIG_FILE.read_text(encoding="utf-8"))
    with pytest.raises(ValueError):
        load_judge_settings(_write(tmp_path, {**base, "judge_model": "gpt-5"}))
    with pytest.raises(ValueError):
        load_judge_settings(_write(tmp_path, {**base, "development_model": "llama-3"}))


def test_eval_registry_resolves_the_judge_prompt():
    prompt = get_eval_registry().resolve(JUDGE_STAGE, provider="anthropic", series="x")
    assert prompt.ref == "_base/eval_judge/v2"
    assert "{payload}" in prompt.user_text
    # The system prompt has no placeholders: it formats to itself with doubled braces undone.
    assert '{"claims"' in prompt.system.format()
