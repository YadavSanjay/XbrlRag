"""C5: the long-context stages' calls carry a 15-minute request timeout.

Oracle: requirement 5; OpenAI's flex-processing guide (raise the client timeout to 15
minutes, 900 s, for Flex) as cited in the measurement plan §8. The provider is the fake
adapter of conftest (the network boundary); the gateway is the real one.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.model_ref import resolve_model_ref
from src.llm.gateway.tools import ToolSpec
from src.llm.request_options import request_timeout_s

TOOL = ToolSpec("lookup", "Looks a fact up.", {"type": "object", "properties": {}})
SHORT_STAGES = ("synthesizer", "plan_generator", "eval_judge", "closed_book", "naive_rag",
                "html_rag", "faithfulness_verifier", "")


@pytest.mark.criterion("C5")
def test_long_context_stages_get_900_seconds_and_others_none(spec):
    # oracle: requirement 5 — 900.0 for long_context, long_context_multi and
    # frontier_sec_tools; None for every other stage
    for stage in spec.long_context_stages:
        assert request_timeout_s(stage) == spec.long_context_timeout_s, stage
    for stage in SHORT_STAGES:
        assert request_timeout_s(stage) is None, stage


def _complete(gateway, spec, stage):
    gateway.complete(messages=[dict(m) for m in spec.messages],
                     model_ref=resolve_model_ref(spec.gateway_model), stage=stage)


@pytest.mark.criterion("C5")
def test_the_gateway_passes_the_timeout_to_invoke(spec):
    # oracle: requirement 5 — complete(stage="long_context") and
    # complete_tools(stage="frontier_sec_tools") reach invoke with timeout 900.0;
    # complete(stage="synthesizer") reaches it with no timeout key
    adapter = spec.capturing_adapter()
    gateway = spec.make_gateway({"openai": adapter})
    _complete(gateway, spec, "long_context")
    gateway.complete_tools(messages=[dict(m) for m in spec.messages], tools=[TOOL],
                           model_ref=resolve_model_ref(spec.gateway_model),
                           stage="frontier_sec_tools")
    _complete(gateway, spec, "synthesizer")
    _complete(gateway, spec, "long_context_multi")
    first, tools, short, multi = adapter.invoked
    assert first.get("timeout") == spec.long_context_timeout_s
    assert tools.get("timeout") == spec.long_context_timeout_s
    assert "timeout" not in short
    assert multi.get("timeout") == spec.long_context_timeout_s
    assert {k: v for k, v in first.items() if k != "timeout"} == short


@pytest.mark.criterion("C5")
@given(stage=st.one_of(st.sampled_from(["long_context", "long_context_multi",
                                         "frontier_sec_tools", *SHORT_STAGES]),
                       st.text(max_size=20)))
def test_any_stage_gets_the_timeout_exactly_when_it_is_long_context(spec, stage):
    # oracle: requirement 5 — "timeout" in params iff the stage is one of the three, then
    # 900.0; the other params do not depend on the stage name
    adapter = spec.capturing_adapter()
    gateway = spec.make_gateway({"openai": adapter})
    _complete(gateway, spec, "long_context")
    _complete(gateway, spec, stage)
    reference, got = adapter.invoked
    assert reference.get("timeout") == spec.long_context_timeout_s
    if stage in spec.long_context_stages:
        assert got.get("timeout") == spec.long_context_timeout_s
    else:
        assert "timeout" not in got
    assert {k: v for k, v in got.items() if k != "timeout"} == {
        k: v for k, v in reference.items() if k != "timeout"}
