"""C7: a case records its largest prompt, and the run summary its largest case."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.systems.frontier_sec_tools import agent_summary

FIND = [("find_company", {"query": "AAA"})]


@pytest.mark.criterion("C7")
def test_the_largest_reported_prompt(make_arm, make_case):
    # oracle: the scripted usage — three turns report 300, 900 and 500 prompt tokens
    arm, adapter, _ = make_arm([FIND, FIND], usage=[300, 900, 500])
    answer = arm.answer(make_case())
    assert adapter.calls == 3
    assert answer.agent.get("prompt_tokens_max") == 900


@pytest.mark.criterion("C7")
def test_no_reported_usage_is_zero(make_arm, make_case):
    # oracle: requirement 7 — every turn reports 0, so no turn reported a positive number
    arm, _, _ = make_arm([FIND], usage=[0, 0])
    answer = arm.answer(make_case())
    assert answer.agent.get("prompt_tokens_max") == 0
    assert isinstance(answer.agent.get("prompt_tokens_max"), int)


@pytest.mark.criterion("C7")
def test_the_summary_takes_the_largest_and_reads_a_missing_field_as_zero():
    # oracle: hand-built records — 12 is the largest; the record without the field counts as 0
    records = [
        {"stop": "answered", "steps": 2, "prompt_tokens_max": 5},
        {"stop": "answered", "steps": 3},
        {"stop": "answered", "steps": 1, "prompt_tokens_max": 12},
    ]
    assert agent_summary(records).get("prompt_tokens_max") == 12
    assert agent_summary([{"stop": "answered", "steps": 1}]).get("prompt_tokens_max") == 0
    assert agent_summary([]).get("prompt_tokens_max") == 0


@pytest.mark.criterion("C7")
@settings(max_examples=20)
@given(usage=st.lists(st.integers(0, 50_000), min_size=1, max_size=4))
def test_any_scripted_usage(make_arm, make_case, usage):
    # oracle: the scripted usage — one tool turn per value but the last, which answers; the
    # largest positive value, or 0
    arm, adapter, _ = make_arm([FIND] * (len(usage) - 1), usage=usage)
    answer = arm.answer(make_case())
    assert adapter.calls == len(usage)
    assert answer.agent.get("prompt_tokens_max") == max([*usage, 0])


@pytest.mark.criterion("C7")
@given(values=st.lists(st.one_of(st.none(), st.integers(0, 10**6)), max_size=10))
def test_the_summary_of_any_records(values):
    # oracle: the largest present value in this test's records, missing read as 0
    records = [{"stop": "answered", "steps": 1} if v is None
               else {"stop": "answered", "steps": 1, "prompt_tokens_max": v} for v in values]
    expected = max([v for v in values if v is not None] + [0])
    assert agent_summary(records).get("prompt_tokens_max") == expected
