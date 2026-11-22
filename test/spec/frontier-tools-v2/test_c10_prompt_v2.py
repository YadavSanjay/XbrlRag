"""C10: the arm resolves prompt v2 by default; v1 still resolves when asked for."""

from __future__ import annotations

import pytest

from prompts.prompt_registry import get_xbrl_registry
from src.llm.gateway import resolve_model_ref

STAGE = "frontier_sec_tools"
# Every model the arm runs on in the v2 comparison, and the conftest's default.
MODELS = ["gpt-5.6-sol", "gpt-6.1-sol", "claude-sonnet-5-5", "claude-opus-5-5",
          "deepseek-v4-pro", "deepseek-flash"]


def _default(model: str):
    """The registry's default resolution for *model*, or the lookup error as a value."""
    ref = resolve_model_ref(model)
    try:
        return get_xbrl_registry().resolve(STAGE, provider=ref.provider, series=ref.series)
    except (FileNotFoundError, KeyError) as exc:
        return exc


@pytest.mark.criterion("C10")
def test_the_arm_resolves_v2(make_arm):
    # oracle: requirement 11 — version v2, at _base/frontier_sec_tools/v2, published in
    # run_config
    arm, _, _ = make_arm()
    assert arm.prompt.version == "v2"
    assert arm.prompt.ref == "_base/frontier_sec_tools/v2"
    assert arm.run_config().get("prompt_version") == "v2"


@pytest.mark.criterion("C10")
def test_v1_still_resolves_when_asked_for(make_arm):
    # oracle: requirement 11 — v1 stays on disk; the default is no longer it
    v1 = get_xbrl_registry().resolve(STAGE, provider="openai", series="gpt-5", version="v1")
    assert v1.ref == "_base/frontier_sec_tools/v1"
    default = _default("gpt-5.6-sol")
    assert not isinstance(default, Exception), repr(default)
    assert default.version == "v2"


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@pytest.mark.parametrize("model", MODELS)
def test_the_default_differs_from_v1_for_every_model(model):
    # oracle: metamorphic — for every provider and series, the default resolution is v2 and its
    # content hash differs from v1's resolved for the same model
    ref = resolve_model_ref(model)
    v1 = get_xbrl_registry().resolve(STAGE, provider=ref.provider, series=ref.series,
                                     version="v1")
    default = _default(model)
    assert not isinstance(default, Exception), repr(default)
    assert default.version == "v2"
    assert default.ref.endswith(f"{STAGE}/v2")
    assert default.content_hash != v1.content_hash
