"""C1: the provider-neutral cache point (requirement 1).

A message dict may carry ``"cache_point": True``; ``cache_point_count`` counts them, the
gateway passes messages to the adapter unchanged, and more than 3 cache points raise
``ValueError`` before any provider call. The adapter is a fake behind a real ``LLMGateway``.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.base import cache_point_count
from src.observability.trace_context import trace_query


def _messages(n_user: int, marked: set[int]) -> list[dict]:
    """A system message then *n_user* user messages; user message i is marked if i in *marked*."""
    out: list[dict] = [{"role": "system", "content": "You answer from the filing."}]
    for i in range(n_user):
        message: dict = {"role": "user", "content": f"part {i}"}
        if i in marked:
            message["cache_point"] = True
        out.append(message)
    return out


@pytest.mark.criterion("C1")
def test_cache_point_count_counts_marked_messages():
    # oracle: requirement 1, hand count — only "cache_point": True marks a message; a
    # False marker or no marker does not
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "filing", "cache_point": True},
        {"role": "user", "content": "notes", "cache_point": False},
        {"role": "assistant", "content": "earlier answer", "cache_point": True},
        {"role": "user", "content": "question"},
    ]
    assert cache_point_count(messages) == 2
    assert cache_point_count([{"role": "user", "content": "q"}]) == 0
    assert cache_point_count([]) == 0


@pytest.mark.criterion("C1")
def test_four_cache_points_raise_before_any_provider_call(make_gateway, recording_adapter):
    # oracle: requirement 1 — more than 3 cache points raise ValueError before any provider
    # call; hand-built request with 4 marked messages
    messages = _messages(5, {0, 1, 2, 3})
    assert cache_point_count(messages) == 4
    adapter = recording_adapter()
    gateway = make_gateway(adapter)
    with trace_query("c1-cap"):
        with pytest.raises(ValueError):
            gateway.complete(messages=messages, model="gpt-5.6-sol", stage="spec")
    assert adapter.invoked == 0
    assert adapter.parsed == 0
    assert adapter.built == []


@pytest.mark.criterion("C1")
def test_one_cache_point_reaches_the_adapter_unchanged(make_gateway, recording_adapter):
    # oracle: requirement 1 — the gateway passes the neutral messages to the adapter
    # unchanged, marker included; hand-built request with 1 marked message
    messages = _messages(2, {0})
    expected = copy.deepcopy(messages)
    assert cache_point_count(messages) == 1
    adapter = recording_adapter()
    gateway = make_gateway(adapter)
    with trace_query("c1-one"):
        gateway.complete(messages=messages, model="gpt-5.6-sol", stage="spec")
    assert adapter.invoked == 1
    assert len(adapter.built) == 1
    assert adapter.built[0]["messages"] == expected
    assert adapter.built[0]["messages"][1]["cache_point"] is True


@pytest.mark.criterion("C1")
@given(
    n_user=st.integers(min_value=3, max_value=6),
    marked=st.sets(st.integers(min_value=0, max_value=5), max_size=3),
    model=st.sampled_from(["gpt-5.6-sol", "gpt-5-mini", "claude-opus-5-5"]),
)
def test_up_to_three_cache_points_pass_through(make_gateway, recording_adapter, n_user,
                                               marked, model):
    # oracle: requirement 1 — for 0-3 cache points the count is the number marked and the
    # adapter receives exactly the messages sent
    marked = {i for i in marked if i < n_user}
    messages = _messages(n_user, marked)
    expected = copy.deepcopy(messages)
    assert cache_point_count(messages) == len(marked)
    adapter = recording_adapter()
    gateway = make_gateway(adapter)
    with trace_query("c1-prop"):
        gateway.complete(messages=messages, model=model, stage="spec")
    assert adapter.invoked == 1
    assert adapter.built[0]["messages"] == expected
