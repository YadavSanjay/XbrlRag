"""C5: the cost of one call, and the committed rates.

Oracle: requirement 6's closed form, computed in each test as ``closed_form`` from the rate
values written in the test (never from the code under test):

    (uncached * input + cached * cached_input + written * cache_write + completion * output) / 1000

with missing cached_input / cache_write falling back to input, and the tier with the largest
``above_prompt_tokens`` strictly below ``prompt_tokens`` replacing the base rates. Committed
rates: C5's table (provider pricing pages, 2026-10) and today's MODEL_PRICING (conftest).
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.llm.config import MODEL_PRICING
from src.llm.profiles import cost_usd, load_committed_settings

# Exact to 1e-12 relative (C5): a handful of float products and sums.
EXACT = 1e-12


def closed_form(base: dict, prompt: int, completion: int, cached: int = 0,
                written: int = 0) -> float:
    """Requirement 6, written out independently of the code under test."""
    rates = base
    applicable = [t for t in base.get("tiers", []) if t["above_prompt_tokens"] < prompt]
    if applicable:
        rates = max(applicable, key=lambda t: t["above_prompt_tokens"])
    cached_rate = rates.get("cached_input", rates["input"])
    write_rate = rates.get("cache_write", rates["input"])
    uncached = prompt - cached - written
    return (
        uncached * rates["input"] + cached * cached_rate + written * write_rate
        + completion * rates["output"]
    ) / 1000


FULL = {"input": 3.0, "output": 15.0, "cached_input": 0.3, "cache_write": 3.75}
BARE = {"input": 2.0, "output": 8.0}
# Two tiers, listed out of order, so "the largest threshold below the prompt" is tested.
TIERED = {
    "input": 1.0, "output": 2.0, "cached_input": 0.5, "cache_write": 1.5,
    "tiers": [
        {"above_prompt_tokens": 1000, "input": 100.0, "output": 200.0, "cached_input": 50.0,
         "cache_write": 150.0},
        {"above_prompt_tokens": 100, "input": 10.0, "output": 20.0, "cached_input": 5.0,
         "cache_write": 15.0},
    ],
}


@pytest.mark.criterion("C5")
def test_full_row_closed_form():
    # oracle: requirement 6 by hand — (6000*3 + 3000*0.3 + 1000*3.75 + 500*15) / 1000 = 30.15
    got = cost_usd(FULL, prompt_tokens=10_000, completion_tokens=500,
                   cached_prompt_tokens=3000, cache_write_tokens=1000)
    assert got == pytest.approx(30.15, rel=EXACT)  # exact up to float rounding (C5: 1e-12)


@pytest.mark.criterion("C5")
def test_missing_cache_rates_fall_back_to_input():
    # oracle: requirement 6 by hand — with no cached_input / cache_write every prompt token
    # costs input: (10000*2 + 500*8) / 1000 = 24.0, whatever the cached / written split
    for cached, written in ((0, 0), (3000, 1000), (4000, 6000)):
        got = cost_usd(BARE, prompt_tokens=10_000, completion_tokens=500,
                       cached_prompt_tokens=cached, cache_write_tokens=written)
        assert got == pytest.approx(24.0, rel=EXACT)  # exact up to float rounding
    only_cached = {"input": 2.0, "output": 8.0, "cached_input": 0.2}
    # (6000*2 + 3000*0.2 + 1000*2 + 500*8) / 1000 = 18.6: written falls back to input
    got = cost_usd(only_cached, prompt_tokens=10_000, completion_tokens=500,
                   cached_prompt_tokens=3000, cache_write_tokens=1000)
    assert got == pytest.approx(18.6, rel=EXACT)  # exact up to float rounding
    only_write = {"input": 2.0, "output": 8.0, "cache_write": 2.5}
    # (6000*2 + 3000*2 + 1000*2.5 + 500*8) / 1000 = 24.5: cached falls back to input
    got = cost_usd(only_write, prompt_tokens=10_000, completion_tokens=500,
                   cached_prompt_tokens=3000, cache_write_tokens=1000)
    assert got == pytest.approx(24.5, rel=EXACT)  # exact up to float rounding


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (99, (99 * 1.0 + 10 * 2.0) / 1000),  # below the lower threshold: base row
        (100, (100 * 1.0 + 10 * 2.0) / 1000),  # at it: still base (strictly below)
        (101, (101 * 10.0 + 10 * 20.0) / 1000),  # just above: the 100 tier
        (999, (999 * 10.0 + 10 * 20.0) / 1000),
        (1000, (1000 * 10.0 + 10 * 20.0) / 1000),  # at the upper threshold: still the 100 tier
        (1001, (1001 * 100.0 + 10 * 200.0) / 1000),  # just above: the 1000 tier
    ],
)
def test_tier_selection_at_and_around_thresholds(prompt, expected):
    # oracle: requirement 6 by hand — the tier with the largest above_prompt_tokens strictly
    # below prompt_tokens applies, else the base row
    got = cost_usd(TIERED, prompt_tokens=prompt, completion_tokens=10)
    assert got == pytest.approx(expected, rel=EXACT)  # exact up to float rounding


@pytest.mark.criterion("C5")
def test_tier_cache_rates_apply_above_threshold():
    # oracle: requirement 6 by hand — above 1000 the 1000 tier's cache rates apply:
    # (1000*100 + 600*50 + 400*150 + 10*200) / 1000 = 192.0
    got = cost_usd(TIERED, prompt_tokens=2000, completion_tokens=10,
                   cached_prompt_tokens=600, cache_write_tokens=400)
    assert got == pytest.approx(192.0, rel=EXACT)  # exact up to float rounding


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    "counts",
    [
        {"prompt_tokens": -1, "completion_tokens": 0},
        {"prompt_tokens": 10, "completion_tokens": -1},
        {"prompt_tokens": 10, "completion_tokens": 0, "cached_prompt_tokens": -1},
        {"prompt_tokens": 10, "completion_tokens": 0, "cache_write_tokens": -1},
        {"prompt_tokens": 10, "completion_tokens": 0, "cached_prompt_tokens": 11},
        {"prompt_tokens": 10, "completion_tokens": 0, "cache_write_tokens": 11},
        {"prompt_tokens": 10, "completion_tokens": 0, "cached_prompt_tokens": 6,
         "cache_write_tokens": 5},
    ],
)
def test_invalid_counts_raise(counts):
    # oracle: requirement 6 — negative counts, or cached + written > prompt, raise ValueError
    with pytest.raises(ValueError):
        cost_usd(FULL, **counts)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("model", sorted(["claude-opus-5-5", "claude-sonnet-5-5",
                                          "claude-haiku-5-5", "gpt-5.6-sol"]))
def test_committed_rates_match_the_table(llm_env, spec, model):
    # oracle: C5's table — Anthropic's and OpenAI's pricing pages (2026-10), per 1M / 1000,
    # cache writes at 1.25x input
    rates = load_committed_settings(environ={}).rates_for(model)
    for key, value in spec.committed_rates[model].items():
        assert rates[key] == pytest.approx(value, rel=EXACT)  # a decimal literal in YAML
    # 10000 prompt (3000 cached, 1000 written) + 500 completion, priced from the table
    got = cost_usd(rates, prompt_tokens=10_000, completion_tokens=500,
                   cached_prompt_tokens=3000, cache_write_tokens=1000)
    want = closed_form(spec.committed_rates[model], 10_000, 500, 3000, 1000)
    assert got == pytest.approx(want, rel=EXACT)  # exact up to float rounding


@pytest.mark.criterion("C5")
def test_committed_haiku_tier_above_100k(llm_env, spec):
    # oracle: C5 — claude-haiku-5-5 above 100,000 prompt tokens: 0.0005 / 0.0025 / 0.00005 /
    # 0.000625; at exactly 100,000 the base rates apply
    rates = load_committed_settings(environ={}).rates_for("claude-haiku-5-5")
    above = cost_usd(rates, prompt_tokens=200_000, completion_tokens=1000,
                     cached_prompt_tokens=50_000, cache_write_tokens=10_000)
    want = (140_000 * 0.0005 + 50_000 * 0.00005 + 10_000 * 0.000625 + 1000 * 0.0025) / 1000
    assert above == pytest.approx(want, rel=EXACT)  # exact up to float rounding
    at = cost_usd(rates, prompt_tokens=100_000, completion_tokens=1000)
    assert at == pytest.approx((100_000 * 0.0001 + 1000 * 0.0005) / 1000, rel=EXACT)  # base row


@pytest.mark.criterion("C5")
def test_todays_model_pricing_rows_are_kept(llm_env, spec):
    # oracle: requirement 5 — every MODEL_PRICING row on main (copied into conftest) keeps its
    # rates in pricing.yaml, and MODEL_PRICING stays importable with the same rates
    settings = load_committed_settings(environ={})
    for model, row in spec.today_pricing.items():
        rates = settings.rates_for(model)
        for key, value in row.items():
            assert rates[key] == pytest.approx(value, rel=EXACT), (model, key)  # YAML decimal
            assert MODEL_PRICING[model][key] == pytest.approx(value, rel=EXACT), (model, key)


# ------------------------------------------------------------------------------ properties

_rate = st.floats(min_value=1e-6, max_value=100.0, allow_nan=False, allow_infinity=False)


@st.composite
def _row(draw):
    row = {"input": draw(_rate), "output": draw(_rate)}
    if draw(st.booleans()):
        row["cached_input"] = draw(_rate)
    if draw(st.booleans()):
        row["cache_write"] = draw(_rate)
    return row


@st.composite
def _counts(draw):
    prompt = draw(st.integers(min_value=0, max_value=1_000_000))
    cached = draw(st.integers(min_value=0, max_value=prompt))
    written = draw(st.integers(min_value=0, max_value=prompt - cached))
    completion = draw(st.integers(min_value=0, max_value=1_000_000))
    return prompt, completion, cached, written


def _cost(row, counts):
    prompt, completion, cached, written = counts
    return cost_usd(row, prompt_tokens=prompt, completion_tokens=completion,
                    cached_prompt_tokens=cached, cache_write_tokens=written)


@pytest.mark.criterion("C5")
@given(row=_row(), a=_counts(), b=_counts())
def test_cost_is_linear_in_the_token_counts(row, a, b):
    # oracle: C5's property — cost is linear in each count: pricing two calls' tokens together
    # costs the sum of pricing them apart, and each equals requirement 6's closed form
    joint = tuple(x + y for x, y in zip(a, b, strict=True))
    # sums of up to four products: a few ulps of the largest term
    assert _cost(row, joint) == pytest.approx(_cost(row, a) + _cost(row, b), rel=1e-9, abs=1e-12)
    assert _cost(row, a) == pytest.approx(closed_form(row, *a), rel=1e-9, abs=1e-12)


@pytest.mark.criterion("C5")
@given(
    row=_row(),
    counts=_counts(),
    completion=st.integers(min_value=1, max_value=1_000_000),
)
def test_each_count_moves_the_cost_by_its_own_rate(row, counts, completion):
    # oracle: requirement 6 — adding completion tokens adds completion * output / 1000
    prompt, base_completion, cached, written = counts
    more = (prompt, base_completion + completion, cached, written)
    delta = _cost(row, more) - _cost(row, counts)
    # a difference of two sums: absolute error a few ulps of the larger operand
    assert delta == pytest.approx(completion * row["output"] / 1000, rel=1e-9, abs=1e-9)


@pytest.mark.criterion("C5")
@given(
    base=_rate,
    fraction=st.floats(min_value=0.0, max_value=1.0),
    output=_rate,
    counts=_counts(),
    data=st.data(),
)
def test_moving_tokens_to_the_cache_never_raises_cost(base, fraction, output, counts, data):
    # oracle: C5's property — with cached_input <= input, turning uncached tokens into cached
    # ones never raises the cost; it falls by moved * (input - cached_input) / 1000
    row = {"input": base, "output": output, "cached_input": base * fraction}
    prompt, completion, cached, written = counts
    assume(prompt - cached - written >= 1)
    moved = data.draw(st.integers(min_value=1, max_value=prompt - cached - written))
    before = _cost(row, counts)
    after = _cost(row, (prompt, completion, cached + moved, written))
    assert after <= before + 1e-12 * max(1.0, before)  # float rounding of equal costs
    want = moved * (row["input"] - row["cached_input"]) / 1000
    assert before - after == pytest.approx(want, rel=1e-9, abs=1e-9)  # difference of sums
