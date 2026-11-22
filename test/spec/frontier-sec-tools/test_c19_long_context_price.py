"""C19: gpt-5.6-sol's price above 272K prompt tokens."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.config import MODEL_PRICING
from src.llm.profiles import cost_usd

FIND = [("find_company", {"query": "AAA"})]
THRESHOLD = 272_000
# OpenAI pricing page, Standard, read 2026-10-31, per 1M tokens / 1000.
BASE = {"input": 0.004, "output": 0.02, "cached_input": 0.0004, "cache_write": 0.005}
LONG = {"input": 0.008, "output": 0.03, "cached_input": 0.0008, "cache_write": 0.01}


def closed_form(rates, prompt, completion, cached=0, written=0):
    uncached = prompt - cached - written
    return (
        uncached * rates["input"]
        + cached * rates["cached_input"]
        + written * rates["cache_write"]
        + completion * rates["output"]
    ) / 1000


@pytest.mark.criterion("C19")
def test_the_row_holds_the_tier_and_its_base_rates():
    # oracle: requirement 36, the page's rates above
    row = MODEL_PRICING["gpt-5.6-sol"]
    assert row.get("tiers") == [{"above_prompt_tokens": THRESHOLD, **LONG}]
    assert {key: row[key] for key in BASE} == BASE


@pytest.mark.criterion("C19")
def test_the_tier_starts_just_past_the_threshold():
    # oracle: hand arithmetic. At 272,000: 1088 + 20 = 1108 / 1000 = 1.108.
    # At 272,001: 2176.008 + 30 = 2206.008 / 1000 = 2.206008, the whole prompt at the long rate
    row = MODEL_PRICING["gpt-5.6-sol"]
    at = cost_usd(row, prompt_tokens=THRESHOLD, completion_tokens=1000)
    past = cost_usd(row, prompt_tokens=THRESHOLD + 1, completion_tokens=1000)
    # tolerance: float products of a few rates
    assert at == pytest.approx(1.108, rel=1e-9)
    assert past == pytest.approx(2.206008, rel=1e-9)


@pytest.mark.criterion("C19")
def test_cached_and_written_tokens_take_the_long_rates():
    # oracle: hand arithmetic: 100,000 at 0.008 + 150,000 at 0.0008 + 50,000 at 0.01 + 2,000 at
    # 0.03 is 800 + 120 + 500 + 60 = 1480, over 1000
    cost = cost_usd(
        MODEL_PRICING["gpt-5.6-sol"],
        prompt_tokens=300_000,
        completion_tokens=2000,
        cached_prompt_tokens=150_000,
        cache_write_tokens=50_000,
    )
    # tolerance: float products of a few rates
    assert cost == pytest.approx(1.48, rel=1e-9)


@pytest.mark.criterion("C19")
def test_the_arm_pays_the_tier_on_each_turn(make_arm, make_case):
    # oracle: requirement 36. Two calls of 300,000 prompt and 500 completion tokens, each
    # (2400 + 15) / 1000 = 2.415
    arm, _, _ = make_arm([FIND], prompt_tokens=300_000, completion_tokens=500)
    answer = arm.answer(make_case())
    assert answer.error is None
    # tolerance: float sum of a few rate products
    assert answer.cost_usd == pytest.approx(4.83, rel=1e-9)


@pytest.mark.criterion("C19")
def test_a_long_turn_spends_the_cost_budget(make_arm, make_case):
    # oracle: requirement 22. One long turn costs 2.415, past max_cost_usd 2.00, so the tools stop
    # after it and one final call follows. At the base rates (1.21) a second turn would run first
    arm, adapter, _ = make_arm(
        [FIND] * 3, prompt_tokens=300_000, completion_tokens=500, max_cost_usd=2.0
    )
    answer = arm.answer(make_case())
    # re-spec (frontier-tools-v2 requirement 9): the 300,000-token turn is also above the
    # 250,000-token window, which closes the tools with stop "window"; still one final call
    assert answer.agent["stop"] == "window"
    assert adapter.calls == 2


@pytest.mark.criterion("C19")
@given(
    prompt=st.integers(THRESHOLD + 1, 2_000_000),
    completion=st.integers(0, 100_000),
    cached_share=st.floats(0, 0.5),
    written_share=st.floats(0, 0.5),
)
def test_above_the_threshold_the_cost_is_the_closed_form(
    prompt, completion, cached_share, written_share
):
    # oracle: the closed form at the page's long rates, on any counts
    cached, written = int(prompt * cached_share), int(prompt * written_share)
    cost = cost_usd(
        MODEL_PRICING["gpt-5.6-sol"],
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_prompt_tokens=cached,
        cache_write_tokens=written,
    )
    # tolerance: float products of the same rates
    assert cost == pytest.approx(closed_form(LONG, prompt, completion, cached, written), rel=1e-9)
