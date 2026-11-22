"""C1: claude-sonnet-5-5's cache reads cost $0.10 per 1M tokens, 0.05x input.

Oracle: the Anthropic pricing page (platform.claude.com/docs/en/about-claude/pricing, "Model
pricing", Claude Sonnet 5.5), read 2026-11-14, copied by hand into conftest.PER_MILLION.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import cost_usd, load_committed_settings

MODEL = "claude-sonnet-5-5"
# tolerance: the rates are short decimal literals; 1e-12 is exact up to float rounding of
# YAML parsing and the /1000 conversion
PRICE_ABS = 1e-12
# tolerance: a cost is a sum of four products; relative 1e-12 is exact up to float rounding
COST_REL = 1e-12


def _committed_row() -> dict:
    table = load_committed_settings(environ={}).pricing_table()
    assert MODEL in table, f"{MODEL} has no row in configs/llm/pricing.yaml"
    return table[MODEL]


@pytest.mark.criterion("C1")
def test_sonnet_row_matches_the_published_rates(spec):
    # oracle: the page's Sonnet 5.5 row — $2 input, $0.10 cache hit, $2.50 5m write, $10
    # output per MTok — divided by 1000 by hand in conftest.per_thousand
    row = _committed_row()
    want = spec.per_thousand(spec.per_million[MODEL]["base"])
    assert "tiers" not in row
    assert set(row) == set(want), (sorted(row), sorted(want))
    for key, value in want.items():
        assert row[key] == pytest.approx(value, abs=PRICE_ABS), key


@pytest.mark.criterion("C1")
def test_sonnet_cache_read_is_a_twentieth_of_input():
    # oracle: the page's footnote 2 for Sonnet 5.5 — a cache hit costs 0.05x the base input
    # price ($0.10 against $2.00 per MTok)
    row = _committed_row()
    assert row["cached_input"] == pytest.approx(0.05 * row["input"], abs=PRICE_ABS)


@pytest.mark.criterion("C1")
def test_sonnet_point_cost():
    # oracle: worked by hand from the page — 10,000 prompt tokens of which 3,000 cached and
    # 2,000 written, 500 completion: (5,000 x 2 + 3,000 x 0.10 + 2,000 x 2.50 + 500 x 10) / 1e6
    # = 0.0203 USD
    got = cost_usd(_committed_row(), prompt_tokens=10_000, cached_prompt_tokens=3_000,
                   cache_write_tokens=2_000, completion_tokens=500)
    assert got == pytest.approx(0.0203, rel=COST_REL)


@st.composite
def _cached_call(draw):
    prompt = draw(st.integers(min_value=1, max_value=900_000))
    cached = draw(st.integers(min_value=1, max_value=prompt))
    written = draw(st.integers(min_value=0, max_value=prompt - cached))
    completion = draw(st.integers(min_value=0, max_value=50_000))
    return prompt, cached, written, completion


@pytest.mark.criterion("C1")
@given(call=_cached_call())
def test_any_cached_sonnet_call_costs_the_published_rates(spec, call):
    # oracle: the page's per-MTok rates applied by hand (conftest.hand_cost); Sonnet 5.5 has
    # no long-context tier (the page's "Long context pricing": only Haiku 5.5 has one)
    prompt, cached, written, completion = call
    got = cost_usd(_committed_row(), prompt_tokens=prompt, cached_prompt_tokens=cached,
                   cache_write_tokens=written, completion_tokens=completion)
    want = spec.hand_cost(MODEL, prompt=prompt, cached=cached, written=written,
                          completion=completion)
    assert got == pytest.approx(want, rel=COST_REL)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(prompt=st.integers(min_value=1_000, max_value=500_000),
       completion=st.integers(min_value=0, max_value=20_000),
       moved=st.integers(min_value=1, max_value=1_000))
def test_reading_tokens_from_cache_saves_ninety_five_percent_of_input(prompt, completion, moved):
    # oracle: cache reads cost 0.05x input, so turning `moved` uncached prompt tokens into
    # cache reads lowers the cost by exactly moved x 0.95 x the $2.00/MTok input rate
    row = _committed_row()
    before = cost_usd(row, prompt_tokens=prompt, cached_prompt_tokens=0, completion_tokens=completion)
    after = cost_usd(row, prompt_tokens=prompt, cached_prompt_tokens=moved,
                     completion_tokens=completion)
    # tolerance: a difference of two sums of products; absolute 1e-12 USD is float rounding
    assert before - after == pytest.approx(moved * 0.95 * 2.00 / 1_000_000, abs=1e-12)
