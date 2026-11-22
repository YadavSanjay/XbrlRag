"""C1: the committed gpt-6.1 prices (and gpt-6-luna's cache write) and the costs they give.

Oracle: the OpenAI pricing page (developers.openai.com/api/docs/pricing, Standard, read
2026-11-03), as transcribed in docs/eval/heldout_v2/measurement_plan.md §5 and §12 item 1
and copied by hand into conftest.PER_MILLION (USD per 1M tokens).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.config import MODEL_PRICING
from src.llm.profiles import cost_usd, load_committed_settings

# tolerance: the rates are short decimal literals (~1e-4 each); 1e-12 is exact up to
# float rounding of YAML parsing and the /1000 conversion
PRICE_ABS = 1e-12
# tolerance: a cost is a sum of a few products of exact-ish floats; relative 1e-12 is exact
# up to float rounding
COST_REL = 1e-12

NEW_MODELS = ("gpt-6.1-sol", "gpt-6.1-terra")


def _per_thousand(values: tuple[float, float, float, float]) -> dict[str, float]:
    inp, read, write, out = values
    return {"input": inp / 1000, "cached_input": read / 1000, "cache_write": write / 1000,
            "output": out / 1000}


def _committed_rates(model: str) -> dict:
    table = load_committed_settings(environ={}).pricing_table()
    assert model in table, f"{model} has no row in configs/llm/pricing.yaml"
    return table[model]


def _assert_rows_equal(got: dict, want: dict) -> None:
    assert set(got) == set(want), (sorted(got), sorted(want))
    for key, value in want.items():
        assert got[key] == pytest.approx(value, abs=PRICE_ABS), key


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model", NEW_MODELS)
def test_new_rows_and_their_long_context_tier(spec, model):
    # oracle: requirement 1's table — base rates plus one tier above 272,000 prompt tokens
    rates = _committed_rates(model)
    base = {k: v for k, v in rates.items() if k != "tiers"}
    _assert_rows_equal(base, _per_thousand(spec.per_million[model]["base"]))
    tiers = rates.get("tiers")
    assert isinstance(tiers, list) and len(tiers) == 1, tiers
    tier = dict(tiers[0])
    assert tier.pop("above_prompt_tokens") == spec.long_prompt_threshold
    _assert_rows_equal(tier, _per_thousand(spec.per_million[model]["long"]))


@pytest.mark.criterion("C1")
def test_luna_gains_its_cache_write_and_keeps_the_rest(spec):
    # oracle: requirement 1 — gpt-6-luna 0.10 / 0.01 / 0.125 / 0.50 per 1M. llm-pricing-refresh
    # C2 adds its long-context tier (OpenAI pricing page, read 2026-11-14), so the base rates
    # are checked beside the tier, which that spec's own tests check
    rates = _committed_rates("gpt-6-luna")
    base = {k: v for k, v in rates.items() if k != "tiers"}
    _assert_rows_equal(base, _per_thousand(spec.per_million["gpt-6-luna"]["base"]))


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model", [*NEW_MODELS, "gpt-6-luna"])
def test_model_pricing_exposes_the_same_rates(spec, model):
    # oracle: requirement 1 — MODEL_PRICING (src/llm/config.py) exposes pricing.yaml's rates
    assert model in MODEL_PRICING, f"{model} missing from MODEL_PRICING"
    row = MODEL_PRICING[model]
    for key, value in _per_thousand(spec.per_million[model]["base"]).items():
        assert row.get(key) == pytest.approx(value, abs=PRICE_ABS), key


@pytest.mark.criterion("C1")
def test_sol_point_costs(spec):
    # oracle: C1's point cases, worked by hand from the per-1M table:
    # (30,000 x 2 + 60,000 x 0.10 + 10,000 x 2.50 + 2,000 x 10) / 1e6 = 0.111 and
    # (50,000 x 4 + 200,000 x 0.20 + 50,000 x 5 + 4,000 x 15) / 1e6 = 0.55
    rates = _committed_rates("gpt-6.1-sol")
    short = cost_usd(rates, prompt_tokens=100_000, cached_prompt_tokens=60_000,
                     cache_write_tokens=10_000, completion_tokens=2_000)
    assert short == pytest.approx(0.111, rel=COST_REL)
    long = cost_usd(rates, prompt_tokens=300_000, cached_prompt_tokens=200_000,
                    cache_write_tokens=50_000, completion_tokens=4_000)
    assert long == pytest.approx(0.55, rel=COST_REL)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model", NEW_MODELS)
def test_tier_boundary_is_strictly_above_272k(spec, model):
    # oracle: requirement 1 — a prompt over 272,000 tokens pays the tier's rates on the whole
    # call; exactly 272,000 pays the base rates (worked by hand per side)
    rates = _committed_rates(model)
    base_in, _, _, base_out = spec.per_million[model]["base"]
    long_in, _, _, long_out = spec.per_million[model]["long"]
    at = cost_usd(rates, prompt_tokens=272_000, completion_tokens=1_000)
    assert at == pytest.approx((272_000 * base_in + 1_000 * base_out) / 1e6, rel=COST_REL)
    above = cost_usd(rates, prompt_tokens=272_001, completion_tokens=1_000)
    assert above == pytest.approx((272_001 * long_in + 1_000 * long_out) / 1e6, rel=COST_REL)


@st.composite
def _counts(draw):
    prompt = draw(st.integers(min_value=0, max_value=600_000))
    cached = draw(st.integers(min_value=0, max_value=prompt))
    written = draw(st.integers(min_value=0, max_value=prompt - cached))
    completion = draw(st.integers(min_value=0, max_value=50_000))
    return prompt, cached, written, completion


@pytest.mark.criterion("C1")
@given(model=st.sampled_from([*NEW_MODELS, "gpt-6-luna"]), counts=_counts())
def test_any_call_costs_the_tables_rates(spec, model, counts):
    # oracle: the per-1M table applied by hand (conftest.hand_cost), tier by prompt > 272,000
    prompt, cached, written, completion = counts
    rates = _committed_rates(model)
    got = cost_usd(rates, prompt_tokens=prompt, cached_prompt_tokens=cached,
                   cache_write_tokens=written, completion_tokens=completion)
    want = spec.hand_cost(model, prompt=prompt, cached=cached, written=written,
                          completion=completion)
    # tolerance: relative float rounding, plus an absolute floor for a zero-token call
    assert got == pytest.approx(want, rel=COST_REL, abs=1e-15)
    assert got > 0 or (prompt == 0 and completion == 0)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(model=st.sampled_from(NEW_MODELS),
       prompt=st.integers(min_value=1, max_value=136_000),
       completion=st.integers(min_value=0, max_value=20_000),
       share=st.floats(min_value=0.0, max_value=1.0))
def test_doubling_counts_within_a_tier_doubles_the_cost(spec, model, prompt, completion, share):
    # oracle: cost is linear in the token counts within one tier (prompt and 2 x prompt both
    # at most 272,000 here), so doubling every count doubles it
    rates = _committed_rates(model)
    cached = int(prompt * share)
    one = cost_usd(rates, prompt_tokens=prompt, cached_prompt_tokens=cached,
                   completion_tokens=completion)
    two = cost_usd(rates, prompt_tokens=2 * prompt, cached_prompt_tokens=2 * cached,
                   completion_tokens=2 * completion)
    assert two == pytest.approx(2 * one, rel=COST_REL)
    assert one == pytest.approx(spec.hand_cost(model, prompt=prompt, cached=cached, written=0,
                                               completion=completion), rel=COST_REL)
