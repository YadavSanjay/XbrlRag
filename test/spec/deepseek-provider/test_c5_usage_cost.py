"""C5: a DeepSeek-shaped reply is parsed and costed.

Oracle: requirement 5, worked by hand. Replies are plain objects (conftest ``reply``); rates
are the committed ``pricing.yaml`` rows, which C1 pins to requirement 2's table, and the
expected cost uses that table (conftest ``DEEPSEEK_RATES``).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.deepseek_adapter import DeepSeekAdapter
from src.llm.profiles import cost_usd, load_committed_settings

# tolerance: C5 states abs=1e-12; the hand sum has ~4 terms of ~1e-4, so float error ~1e-19
POINT_ABS = 1e-12
# tolerance: a few float roundings on a sum of up to ~1e3 USD; 1e-12 relative is far above
# double-precision error and far below any rate difference
PROPERTY_REL = 1e-12


def _committed_rates(model: str) -> dict:
    table = load_committed_settings(environ={}).pricing_table()
    assert model in table, f"{model} has no row in the committed pricing.yaml"
    return table[model]


@pytest.mark.criterion("C5")
def test_spec_point_reply_is_parsed_and_costed(spec):
    # oracle: C5's point case — counts as given; cost 0.6*0.0003 + 0.4*0.000006 + 0.5*0.0012
    # = 0.00018 + 0.0000024 + 0.0006 = 0.0007824
    rates = _committed_rates("deepseek-flash")
    adapter = DeepSeekAdapter(api_key="sk-spec", base_url="https://api.deepseek.com")
    raw = spec.reply(prompt_tokens=1000, completion_tokens=500, cached_tokens=400,
                     reasoning_tokens=120)
    parsed = adapter.parse(raw, stage="spec")
    assert parsed.prompt_tokens == 1000
    assert parsed.completion_tokens == 500
    assert parsed.cached_prompt_tokens == 400
    assert parsed.reasoning_tokens == 120
    assert parsed.cache_write_tokens == 0
    assert parsed.content == '{"ok": true}'
    cost = cost_usd(rates, prompt_tokens=parsed.prompt_tokens,
                    completion_tokens=parsed.completion_tokens,
                    cached_prompt_tokens=parsed.cached_prompt_tokens)
    assert cost == pytest.approx(0.0007824, abs=POINT_ABS)


@st.composite
def _counts(draw):
    prompt = draw(st.integers(min_value=0, max_value=1_000_000))
    cached = draw(st.integers(min_value=0, max_value=prompt))
    completion = draw(st.integers(min_value=0, max_value=200_000))
    reasoning = draw(st.integers(min_value=0, max_value=completion))
    return prompt, cached, completion, reasoning


@pytest.mark.criterion("C5")
@given(counts=_counts(), model=st.sampled_from(["deepseek-flash", "deepseek-v4-pro"]))
def test_any_counts_cost_the_closed_form(spec, counts, model):
    # oracle: C5's property — ((prompt - cached) * input + cached * cached_input
    # + completion * output) / 1000 with requirement 2's rates; the reply's counts parse as given
    prompt, cached, completion, reasoning = counts
    rates = _committed_rates(model)
    parsed = DeepSeekAdapter(api_key="sk-spec", base_url="https://api.deepseek.com").parse(
        spec.reply(prompt_tokens=prompt, completion_tokens=completion, cached_tokens=cached,
                   reasoning_tokens=reasoning, model=model),
        stage="spec",
    )
    assert (parsed.prompt_tokens, parsed.cached_prompt_tokens) == (prompt, cached)
    assert (parsed.completion_tokens, parsed.reasoning_tokens) == (completion, reasoning)
    table = spec.rates[model]
    expected = ((prompt - cached) * table["input"] + cached * table["cached_input"]
                + completion * table["output"]) / 1000
    got = cost_usd(rates, prompt_tokens=prompt, completion_tokens=completion,
                   cached_prompt_tokens=cached)
    assert got == pytest.approx(expected, rel=PROPERTY_REL, abs=POINT_ABS)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_moving_prompt_tokens_into_the_cache_saves_the_rate_difference(spec):
    # oracle: requirement 5 — each prompt token moved from uncached to cached lowers the cost
    # by (input - cached_input) / 1000
    rates = _committed_rates("deepseek-v4-pro")
    table = spec.rates["deepseek-v4-pro"]
    base = cost_usd(rates, prompt_tokens=10_000, completion_tokens=1_000, cached_prompt_tokens=0)
    for cached in (1, 2_500, 10_000):
        moved = cost_usd(rates, prompt_tokens=10_000, completion_tokens=1_000,
                         cached_prompt_tokens=cached)
        saving = cached * (table["input"] - table["cached_input"]) / 1000
        assert base - moved == pytest.approx(saving, rel=PROPERTY_REL, abs=POINT_ABS), cached
