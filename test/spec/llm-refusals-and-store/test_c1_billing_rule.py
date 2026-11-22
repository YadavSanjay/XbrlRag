"""C1: the refusal billing rule (requirement 1).

Oracle: requirement 1 and Anthropic's "Refusals and fallback" page, section "How refusals
are billed" (read 2026-10-21): billed when output was already streamed, or when the
category is bio, frontier_llm or reasoning_extraction.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.anthropic_adapter import refusal_is_billed

BILLED = ("bio", "frontier_llm", "reasoning_extraction")
NOT_BILLED = ("cyber", "general_harms", None, "some_future_category")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("category", BILLED)
def test_billed_categories_before_any_output(category):
    # oracle: requirement 1 / Anthropic's refusals page — these three categories are billed
    # even with 0 completion tokens
    assert refusal_is_billed(category, 0) is True


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("category", NOT_BILLED)
def test_other_categories_are_billed_only_after_output(category):
    # oracle: requirement 1 / Anthropic's refusals page — any other category (or None)
    # before output is not billed; the same category with streamed output is
    assert refusal_is_billed(category, 0) is False
    assert refusal_is_billed(category, 1) is True


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("category", BILLED + NOT_BILLED)
@pytest.mark.parametrize("completion_tokens", [1, 2, 37, 4096])
def test_any_category_with_streamed_output_is_billed(category, completion_tokens):
    # oracle: requirement 1 — a mid-stream refusal (completion_tokens > 0) is always billed
    assert refusal_is_billed(category, completion_tokens) is True


_CATEGORY = st.one_of(
    st.none(),
    st.sampled_from(BILLED + ("cyber", "general_harms", "BIO", "bio ", "")),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", max_size=24),
)


@pytest.mark.criterion("C1")
@given(category=_CATEGORY, completion_tokens=st.integers(min_value=0, max_value=200_000))
def test_billing_rule_closed_form(category, completion_tokens):
    # oracle: requirement 1, closed form — completion_tokens > 0 or category in the three
    # billed categories (exact match: case and spacing variants are other categories)
    expected = completion_tokens > 0 or category in BILLED
    assert refusal_is_billed(category, completion_tokens) is expected
