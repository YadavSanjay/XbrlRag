"""C3: openai_service_tier reads XBRLRAG_OPENAI_SERVICE_TIER as requirement 3 says.

Oracle: requirement 3; OpenAI's API reference lists the request's ``service_tier`` values
``auto``, ``default``, ``flex`` and ``priority``, and its flex-processing guide requests Flex
with ``service_tier: "flex"``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import LlmConfigError
from src.llm.request_options import openai_service_tier

VALID = ("auto", "default", "flex", "priority")


@pytest.mark.criterion("C3")
def test_unset_or_blank_is_none_and_a_value_is_returned(spec):
    # oracle: requirement 3 — unset / "" / whitespace -> None; "flex" -> "flex"
    name = spec.tier_env_name
    assert openai_service_tier({name: "flex"}) == "flex"
    assert openai_service_tier({}) is None
    assert openai_service_tier({name: ""}) is None
    assert openai_service_tier({name: "  "}) is None
    assert openai_service_tier({"OTHER": "flex"}) is None


@pytest.mark.criterion("C3")
def test_each_valid_tier_and_surrounding_whitespace(spec):
    # oracle: requirement 3 — each of the four values is returned as given, stripped
    name = spec.tier_env_name
    for tier in VALID:
        assert openai_service_tier({name: tier}) == tier
    assert openai_service_tier({name: " flex "}) == "flex"
    assert openai_service_tier({name: "priority\n"}) == "priority"


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("bad", ["FLEX", "batch", "scale", "standard", "flex,auto", "0"])
def test_any_other_value_raises(spec, bad):
    # oracle: requirement 3 — anything outside the four (case-sensitive) is LlmConfigError
    assert openai_service_tier({spec.tier_env_name: "flex"}) == "flex"
    with pytest.raises(LlmConfigError):
        openai_service_tier({spec.tier_env_name: bad})


@pytest.mark.criterion("C3")
def test_reads_os_environ_when_no_mapping_is_given(spec):
    # oracle: requirement 3 — environ=None reads os.environ, at call time
    with spec.tier_env("priority"):
        assert openai_service_tier() == "priority"
    with spec.tier_env("flex"):
        assert openai_service_tier() == "flex"
    with spec.tier_env(None):
        assert openai_service_tier() is None


@pytest.mark.criterion("C3")
@given(value=st.one_of(
    st.sampled_from(VALID),
    st.tuples(st.sampled_from(VALID), st.sampled_from(["", " ", "\t", "\n "]))
      .map(lambda t: t[1] + t[0] + t[1]),
    st.sampled_from(["", " ", "\t\n"]),
    st.text(max_size=12),
))
def test_any_string_maps_by_the_rule(spec, value):
    # oracle: requirement 3 — stripped value if one of the four; None if blank; else raises
    stripped = value.strip()
    env = {spec.tier_env_name: value}
    if stripped in VALID:
        assert openai_service_tier(env) == stripped
    elif not stripped:
        assert openai_service_tier(env) is None
        assert openai_service_tier({spec.tier_env_name: "auto"}) == "auto"
    else:
        with pytest.raises(LlmConfigError):
            openai_service_tier(env)
        assert openai_service_tier({spec.tier_env_name: "default"}) == "default"
