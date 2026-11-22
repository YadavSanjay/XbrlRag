"""C7: gpt-6-luna is the milestone judge, and the judge is never the generator's family.

Oracle: requirement 8; measurement plan decision 10 (gpt-6-luna replaces gpt-5.6-luna as the
milestone judge, claude-haiku-5-5 stays the development judge); the rule stated in
configs/eval/judge.yaml.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge import load_judge_settings
from src.eval.core.judge_wiring import check_judge_family
from src.llm.profiles import load_committed_settings

# The committed profiles after this change, and the providers of their tier models
# (requirement 2 and the unchanged profiles), written by hand.
PROFILE_PROVIDERS = {
    "openai": {"openai"},
    "openai-5.6": {"openai"},
    "openai-6.1": {"openai"},
    "anthropic": {"anthropic"},
    "deepseek": {"deepseek"},
}
# The judge models after this change and their providers (requirement 8).
JUDGE_PROVIDERS = {"claude-haiku-5-5": "anthropic", "gpt-6-luna": "openai"}


def _committed_ready():
    settings = load_committed_settings(environ={})
    assert "openai-6.1" in settings.profile_names, settings.profile_names
    assert settings.has_model("gpt-6-luna"), "gpt-6-luna has no row in llm.yaml"
    return settings


@pytest.mark.criterion("C7")
def test_judge_config_names_gpt_6_luna_as_the_milestone_judge():
    # oracle: requirement 8 — milestone_models exactly (gpt-6-luna,), development unchanged
    settings = load_judge_settings()
    assert settings.milestone_models == ("gpt-6-luna",)
    assert settings.development_model == "claude-haiku-5-5"
    assert _committed_ready().provider_for("gpt-6-luna") == "openai"


@pytest.mark.criterion("C7")
def test_family_rule_for_the_new_judge():
    # oracle: requirement 8 — gpt-6-luna may judge the anthropic profile, never an openai one
    _committed_ready()
    assert check_judge_family("gpt-6-luna", "anthropic") is None
    for profile in ("openai", "openai-5.6", "openai-6.1"):
        with pytest.raises(ValueError, match="family"):
            check_judge_family("gpt-6-luna", profile)


@pytest.mark.criterion("C7")
@given(profile=st.sampled_from(sorted(PROFILE_PROVIDERS)),
       judge=st.sampled_from(sorted(JUDGE_PROVIDERS)))
def test_any_profile_and_judge_follow_the_family_rule(profile, judge):
    # oracle: the rule in judge.yaml — allowed exactly when the judge's provider is not
    # among the profile's providers (tables above, written by hand)
    _committed_ready()
    configured = load_judge_settings()
    assert judge in (configured.development_model, *configured.milestone_models)
    if JUDGE_PROVIDERS[judge] in PROFILE_PROVIDERS[profile]:
        with pytest.raises(ValueError, match="family"):
            check_judge_family(judge, profile)
    else:
        assert check_judge_family(judge, profile) is None
