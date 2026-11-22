"""C3: which profile is active.

Oracle: requirement 3's precedence, as the closed form ``expected_profile`` below:
explicit ``profile`` > non-empty expanded ``active_profile`` (XBRLRAG_LLM_PROFILE) > the
legacy switch on -> ``openai-5.6`` > ``openai``. An unknown name raises.

arm-model-from-profile moves row 4 from ``openai`` to ``openai-6.1`` (the gpt-5 family shuts
down 2026-12-11) and pins it in its own C3; here row 4 is ``FALLBACK``, either of the two.

Every environment a case uses is passed as ``environ`` **and** mirrored into ``os.environ``,
so the result does not depend on which of the two the loader reads the legacy switch from.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import LlmConfigError, load_llm_settings

PROFILE_ENV = "XBRLRAG_LLM_PROFILE"
LEGACY_ENV = "XBRLRAG_USE_REPLACEMENT_MODELS"
NAMES = ("openai", "openai-5.6", "anthropic", "mixed")
ON_WORDS = ("1", "true", "yes", "on", "TRUE", " On ")
OFF_WORDS = ("0", "false", "no", "off")
# Row 4, before and after arm-model-from-profile.
FALLBACK = "<row 4>"
FALLBACK_NAMES = ("openai", "openai-6.1")


def expected_profile(profile: str | None, env_profile: str | None, legacy: str | None) -> str:
    """Requirement 3's rows, top to bottom."""
    if profile is not None:
        return profile
    if env_profile:
        return env_profile
    if legacy is not None and legacy.strip().lower() in {"1", "true", "yes", "on"}:
        return "openai-5.6"
    return FALLBACK


def _check_active(settings, active: str, doc: dict, tier: str) -> None:
    """The active profile is *active* (row 4: either fallback name), with its tier's model."""
    if active == FALLBACK:
        assert settings.active_profile in FALLBACK_NAMES
        active = settings.active_profile
    assert settings.active_profile == active
    assert settings.model_for_tier(tier) == doc["profiles"][active][tier]


def _environ(env_profile: str | None, legacy: str | None) -> dict[str, str]:
    environ: dict[str, str] = {}
    if env_profile is not None:
        environ[PROFILE_ENV] = env_profile
    if legacy is not None:
        environ[LEGACY_ENV] = legacy
    return environ


def _load(make_doc, make_pricing, profile, env_profile, legacy):
    """Loads with *environ* mirrored into os.environ for the duration of the call."""
    environ = _environ(env_profile, legacy)
    with pytest.MonkeyPatch.context() as mp:
        for name in (PROFILE_ENV, LEGACY_ENV, "ARAG_LLM_PROFILE", "ARAG_USE_REPLACEMENT_MODELS"):
            mp.delenv(name, raising=False)
        for name, value in environ.items():
            mp.setenv(name, value)
        return load_llm_settings(make_doc(), make_pricing(), profile=profile, environ=environ)


# (profile argument, XBRLRAG_LLM_PROFILE, legacy switch) -> the active profile
ROWS = [
    # row 1 wins over everything below it
    (("anthropic", "openai-5.6", "1"), "anthropic"),
    (("openai", "anthropic", "true"), "openai"),
    (("mixed", None, None), "mixed"),
    (("openai-5.6", "", "off"), "openai-5.6"),
    # row 2 wins over rows 3 and 4
    ((None, "anthropic", "1"), "anthropic"),
    ((None, "openai", "on"), "openai"),
    ((None, "mixed", None), "mixed"),
    # row 3: empty or unset profile variable, switch on
    ((None, "", "1"), "openai-5.6"),
    ((None, None, "yes"), "openai-5.6"),
    # row 4
    ((None, None, None), FALLBACK),
    ((None, "", None), FALLBACK),
    ((None, None, "0"), FALLBACK),
    ((None, "", "off"), FALLBACK),
]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("args", "active"), ROWS)
def test_precedence_rows(make_doc, make_pricing, args, active):
    # oracle: requirement 3 — the hand-checked row for this combination
    settings = _load(make_doc, make_pricing, *args)
    _check_active(settings, active, make_doc(), "medium")


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("profile", "env_profile"),
    [("nope", None), ("nope", "anthropic"), (None, "nope"), ("gpt-5", None)],
)
def test_unknown_profile_name_raises(make_doc, make_pricing, profile, env_profile):
    # oracle: requirement 3 — an unknown profile name raises LlmConfigError
    with pytest.raises(LlmConfigError):
        _load(make_doc, make_pricing, profile, env_profile, None)


@pytest.mark.criterion("C3")
def test_active_profile_placeholders_expand_from_environ(make_doc, make_pricing):
    # oracle: requirement 1 — ${VAR:-default} expands from environ: the variable's value
    # when set, the default when unset; a literal name is used as is
    doc = make_doc()
    doc["active_profile"] = "${SPEC_PROFILE_VAR:-mixed}"
    assert load_llm_settings(doc, make_pricing(), environ={}).active_profile == "mixed"
    settings = load_llm_settings(doc, make_pricing(), environ={"SPEC_PROFILE_VAR": "anthropic"})
    assert settings.active_profile == "anthropic"
    doc["active_profile"] = "openai-5.6"
    assert load_llm_settings(doc, make_pricing(), environ={}).active_profile == "openai-5.6"


@pytest.mark.criterion("C3")
@given(
    profile=st.one_of(st.none(), st.sampled_from(NAMES)),
    env_profile=st.one_of(st.none(), st.just(""), st.sampled_from(NAMES)),
    legacy=st.one_of(st.none(), st.sampled_from(ON_WORDS + OFF_WORDS)),
)
def test_any_combination_is_the_closed_form(make_doc, make_pricing, profile, env_profile, legacy):
    # oracle: C3's property — over random combinations the active profile is the closed form
    settings = _load(make_doc, make_pricing, profile, env_profile, legacy)
    _check_active(settings, expected_profile(profile, env_profile, legacy), make_doc(), "small")
