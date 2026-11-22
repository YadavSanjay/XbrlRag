"""C4: DeepSeekAdapter.build_params produces a DeepSeek Chat Completions request.

Oracle: DeepSeek's Chat Completions reference (retrieved 2026-10-29: the output budget is
``max_tokens``, reasoning is set by ``reasoning_effort``) and requirement 4. The configured
efforts come from requirement 1 (deepseek-flash low, deepseek-v4-pro high).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.deepseek_adapter import DeepSeekAdapter


def _adapter() -> DeepSeekAdapter:
    return DeepSeekAdapter(api_key="sk-spec", base_url="https://api.deepseek.com")


MESSAGES = [
    {"role": "system", "content": "Answer in JSON.", "cache_point": True},
    {"role": "user", "content": "What was AAA revenue in fiscal 2024?"},
]
STRIPPED = [
    {"role": "system", "content": "Answer in JSON."},
    {"role": "user", "content": "What was AAA revenue in fiscal 2024?"},
]


@pytest.mark.criterion("C4")
def test_spec_point_case_is_exactly_the_chat_request():
    # oracle: C4's point case — model, messages without cache_point, max_tokens 900,
    # json_object, and deepseek-flash's configured effort "low"; nothing else
    params = _adapter().build_params(
        messages=[dict(m) for m in MESSAGES],
        model="deepseek-flash",
        max_completion_tokens=900,
        temperature=None,
        response_format="json",
        reasoning_effort=None,
    )
    assert params == {
        "model": "deepseek-flash",
        "messages": STRIPPED,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
    }


@pytest.mark.criterion("C4")
def test_callers_effort_temperature_and_format_pass_through():
    # oracle: requirement 4 — the caller's reasoning_effort wins and is sent unchanged;
    # temperature only when given; a dict response_format passes through; a model outside
    # llm.yaml with no caller effort sends none
    adapter = _adapter()
    high = adapter.build_params(
        messages=[dict(m) for m in MESSAGES], model="deepseek-flash",
        max_completion_tokens=900, response_format="json", reasoning_effort="high",
    )
    assert high["reasoning_effort"] == "high"
    medium = adapter.build_params(
        messages=[dict(m) for m in STRIPPED], model="deepseek-v4-pro",
        max_completion_tokens=50, reasoning_effort="medium",
    )
    assert medium["reasoning_effort"] == "medium"
    configured = adapter.build_params(
        messages=[dict(m) for m in STRIPPED], model="deepseek-v4-pro", max_completion_tokens=50,
    )
    assert configured["reasoning_effort"] == "high"
    assert "temperature" not in configured
    assert "response_format" not in configured
    schema = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    warm = adapter.build_params(
        messages=[dict(m) for m in STRIPPED], model="deepseek-unlisted-x",
        max_completion_tokens=10, temperature=0.25, response_format=schema,
    )
    assert warm == {
        "model": "deepseek-unlisted-x",
        "messages": STRIPPED,
        "max_tokens": 10,
        "temperature": 0.25,
        "response_format": schema,
    }


ROLES = st.sampled_from(["system", "user", "assistant"])
MESSAGE = st.builds(
    lambda role, content, cache: (
        {"role": role, "content": content} if cache is None
        else {"role": role, "content": content, "cache_point": cache}
    ),
    ROLES,
    st.text(max_size=40),
    st.sampled_from([None, True, False]),
)


@pytest.mark.criterion("C4")
@given(
    messages=st.lists(MESSAGE, min_size=1, max_size=6),
    budget=st.integers(min_value=1, max_value=200_000),
    model=st.sampled_from(["deepseek-flash", "deepseek-v4-pro"]),
)
def test_any_messages_and_budget_give_a_chat_request(messages, budget, model):
    # oracle: C4's property — no input, no max_completion_tokens, max_tokens == budget, and the
    # messages are the inputs with cache_point removed, in order
    expected = [{k: v for k, v in m.items() if k != "cache_point"} for m in messages]
    params = _adapter().build_params(
        messages=[dict(m) for m in messages], model=model, max_completion_tokens=budget,
    )
    assert "input" not in params
    assert "max_completion_tokens" not in params
    assert params["max_tokens"] == budget
    assert params["model"] == model
    assert params["messages"] == expected


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_adding_a_cache_point_changes_nothing_but_the_flag():
    # oracle: requirement 4 — the request is the same Chat Completions request whether or not
    # a message carries "cache_point": True (DeepSeek caches automatically)
    adapter = _adapter()
    plain = adapter.build_params(
        messages=[dict(m) for m in STRIPPED], model="deepseek-flash", max_completion_tokens=300,
        response_format="json",
    )
    for index in range(len(STRIPPED)):
        flagged = [dict(m) for m in STRIPPED]
        flagged[index]["cache_point"] = True
        params = adapter.build_params(
            messages=flagged, model="deepseek-flash", max_completion_tokens=300,
            response_format="json",
        )
        assert params == plain, index
    assert plain["messages"] == STRIPPED
    assert plain["max_tokens"] == 300
