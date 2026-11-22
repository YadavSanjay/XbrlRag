"""C2: gpt-6-luna's long-context tier, over 272,000 prompt tokens.

Oracle: the OpenAI pricing page (developers.openai.com/api/docs/pricing, Standard, "Flagship
models", gpt-6-luna short and long context), read 2026-11-14, copied by hand into
conftest.PER_MILLION.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import cost_usd, load_committed_settings

MODEL = "gpt-6-luna"
# tolerance: the rates are short decimal literals; 1e-12 is exact up to float rounding of
# YAML parsing and the /1000 conversion
PRICE_ABS = 1e-12
# tolerance: a cost is a sum of four products; relative 1e-12 is exact up to float rounding
COST_REL = 1e-12


def _committed_row() -> dict:
    table = load_committed_settings(environ={}).pricing_table()
    assert MODEL in table, f"{MODEL} has no row in configs/llm/pricing.yaml"
    return table[MODEL]


def _assert_rates(got: dict, want: dict) -> None:
    assert set(got) == set(want), (sorted(got), sorted(want))
    for key, value in want.items():
        assert got[key] == pytest.approx(value, abs=PRICE_ABS), key


@pytest.mark.criterion("C2")
def test_luna_has_its_published_long_context_tier(spec):
    # oracle: the page's long-context column for gpt-6-luna — $0.20 input, $0.02 cached,
    # $0.25 30-minute write, $0.75 output per 1M over 272K input tokens — /1000 by hand
    row = _committed_row()
    tiers = row.get("tiers")
    assert isinstance(tiers, list) and len(tiers) == 1, tiers
    tier = dict(tiers[0])
    assert tier.pop("above_prompt_tokens") == spec.threshold
    _assert_rates(tier, spec.per_thousand(spec.per_million[MODEL]["long"]))


@pytest.mark.criterion("C2")
def test_luna_keeps_its_base_rates_beside_the_tier(spec):
    # oracle: the page's short-context column for gpt-6-luna — $0.10 / $0.01 / $0.125 / $0.50
    # per 1M; the row gains a tier and nothing else changes
    row = _committed_row()
    assert "tiers" in row, "gpt-6-luna has no long-context tier"
    base = {k: v for k, v in row.items() if k != "tiers"}
    _assert_rates(base, spec.per_thousand(spec.per_million[MODEL]["base"]))


@pytest.mark.criterion("C2")
def test_luna_tier_starts_strictly_above_272k(spec):
    # oracle: worked by hand from the page — exactly 272,000 prompt tokens pay the base rates,
    # (272,000 x 0.10 + 1,000 x 0.50) / 1e6 = 0.0277; 272,001 pay the long rates on the whole
    # call, (272,001 x 0.20 + 1,000 x 0.75) / 1e6 = 0.0551502
    row = _committed_row()
    at = cost_usd(row, prompt_tokens=272_000, completion_tokens=1_000)
    assert at == pytest.approx(0.0277, rel=COST_REL)
    above = cost_usd(row, prompt_tokens=272_001, completion_tokens=1_000)
    assert above == pytest.approx(0.0551502, rel=COST_REL)


@st.composite
def _long_call(draw):
    prompt = draw(st.integers(min_value=272_001, max_value=1_000_000))
    cached = draw(st.integers(min_value=0, max_value=prompt))
    written = draw(st.integers(min_value=0, max_value=prompt - cached))
    completion = draw(st.integers(min_value=0, max_value=50_000))
    return prompt, cached, written, completion


@pytest.mark.criterion("C2")
@given(call=_long_call())
def test_any_long_luna_call_costs_the_long_rates(spec, call):
    # oracle: the page's long-context rates applied by hand (conftest.hand_cost)
    prompt, cached, written, completion = call
    got = cost_usd(_committed_row(), prompt_tokens=prompt, cached_prompt_tokens=cached,
                   cache_write_tokens=written, completion_tokens=completion)
    want = spec.hand_cost(MODEL, prompt=prompt, cached=cached, written=written,
                          completion=completion)
    assert got == pytest.approx(want, rel=COST_REL)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(prompt=st.integers(min_value=272_001, max_value=1_000_000),
       completion=st.integers(min_value=0, max_value=20_000))
def test_a_long_call_costs_twice_the_input_and_one_and_a_half_the_output(prompt, completion):
    # oracle: the page's two columns — long input is 2x short ($0.20 vs $0.10) and long
    # output 1.5x ($0.75 vs $0.50) — so an uncached call over 272K costs 2x its input part
    # and 1.5x its output part at the short-context rates
    long_cost = cost_usd(_committed_row(), prompt_tokens=prompt, completion_tokens=completion)
    short_input = prompt * 0.10 / 1_000_000
    short_output = completion * 0.50 / 1_000_000
    # tolerance: relative float rounding of a two-term sum
    assert long_cost == pytest.approx(2 * short_input + 1.5 * short_output, rel=COST_REL)
