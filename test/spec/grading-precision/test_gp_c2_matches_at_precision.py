"""C2: comparing a number with a key under a rule (docs/specs/grading-precision.md, req. 2).

Each rule comes from ``precision_rule`` on a real dev case (C1 pins those rules), so the
tests never build a ``PrecisionRule`` themselves. Every expected verdict is a decimal
rounding done by hand (in the comments) or, in the property tests, with Python's
``decimal`` module in the test itself.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.precision import matches_at_precision, precision_rule


@pytest.fixture(scope="session")
def rules(dev, dev_record):
    """Named rules: amount at 10**k, ratio, percent, cents, exact and rel_tol 1e-4."""

    def amount(k: int):
        record = dev_record("intc_opinc")
        record["key"]["inputs"][0]["decimals"] = -k
        return precision_rule(EvalCase.from_dict(record))

    def named(name: str):
        if name == "ratio":
            return precision_rule(dev("tsm_rd_ratio"))
        if name == "percent":
            return precision_rule(dev("msft_tax_yoy"))
        if name == "cents":
            return precision_rule(dev("adbe_eps"))
        if name == "exact":
            record = dev_record("nvda_eps")
            record["unit"] = "USD"
            record["key"]["inputs"][0].update(decimals="INF", unit="USD")
            return precision_rule(EvalCase.from_dict(record))
        if name == "rel":
            goodwill = dev("bigtech_goodwill_top3")
            return precision_rule(goodwill, goodwill.expected_rows[0])
        raise KeyError(name)

    return amount, named


@pytest.mark.criterion("C2")
def test_maintainer_examples(rules):
    # oracle: the approved examples. 1,234,000,000 at decimals -6: 1.2e9 rounds to
    # 1,200,000,000 at the million, not 1,234,000,000. Ratio 0.140746 to 3 s.f. is 0.141:
    # 0.1407 -> 0.141 passes, 0.14 -> 0.140 fails. Percent 0.1407 is 14.07% -> 14.1 at
    # 0.1 point: 14.1 passes, 14 -> 14.0 fails.
    amount, named = rules
    m6 = amount(6)
    assert matches_at_precision(1_234_000_000.0, 1_234_000_000.0, m6) is True
    assert matches_at_precision(1.2e9, 1_234_000_000.0, m6) is False
    assert matches_at_precision(0.1407, 0.140746, named("ratio")) is True
    assert matches_at_precision(0.14, 0.140746, named("ratio")) is False
    assert matches_at_precision(14.1, 0.1407, named("percent")) is True
    assert matches_at_precision(14.0, 0.1407, named("percent")) is False


@pytest.mark.criterion("C2")
def test_real_keys(rules):
    # oracle: TSM FY2024 R&D / revenue = 204,181.8 / 2,894,307.7 (TWD millions)
    # = 0.0705460 (2,894,307.7 x 0.07 = 202,601.5; remainder 1,580.3 / 2,894,307.7 =
    # 0.000546); 3 s.f. -> 0.0705 = 7.05%. 0.07055 is the key at 1e-5 (more precise than
    # the rule, rounded correctly at its own place) -> passes although it rounds to 0.0706.
    _, named = rules
    ratio = named("ratio")
    tsm = 204_181_800_000 / 2_894_307_700_000
    for ok in (7.05, 0.0705, 0.07055, 7.055):
        assert matches_at_precision(ok, tsm, ratio) is True, ok
    for bad in (7.1, 0.0706, 0.071, 7.06):
        assert matches_at_precision(bad, tsm, ratio) is False, bad
    # ADBE FY2025 diluted EPS 16.70: "16.7" drops a zero only; 16.71 is one cent off.
    cents = named("cents")
    assert matches_at_precision(16.7, 16.7, cents) is True
    assert matches_at_precision(16.71, 16.7, cents) is False
    # An exact (INF) fact: 1.19 vs 1.2.
    exact = named("exact")
    assert matches_at_precision(1.19, 1.19, exact) is True
    assert matches_at_precision(1.2, 1.19, exact) is False
    assert matches_at_precision(1.194, 1.19, exact) is False
    # rel_tol 1e-4 on 182,637,000,000: 182.64e9 is 3e6 off (1.6e-5); 182.6e9 is 37e6
    # off (2.0e-4).
    rel = named("rel")
    assert matches_at_precision(182.64e9, 182_637_000_000.0, rel) is True
    assert matches_at_precision(182.6e9, 182_637_000_000.0, rel) is False
    # float noise: 3.00 - 2.72 computed in binary is 0.2799999999999998; 0.28 at cents.
    assert matches_at_precision(0.28, 3.0 - 2.72, cents) is True
    assert matches_at_precision(0.27, 3.0 - 2.72, cents) is False


def _round(x: Decimal, q: Decimal) -> Decimal:
    return x.quantize(q, rounding=ROUND_HALF_UP)


@pytest.mark.criterion("C2")
@given(m=st.integers(min_value=1, max_value=10**6), k=st.integers(min_value=0, max_value=9),
       neg=st.booleans())
def test_reported_key_passes_and_one_quantum_beyond_fails(rules, m, k, neg):
    # oracle: requirement 2 — a reported key T = m * 10**k at decimals -k is already a
    # multiple of its quantum, so T passes and T +/- 10**k (one quantum) round elsewhere;
    # less than half a quantum away rounds back to T
    amount, _ = rules
    rule = amount(k)
    q = 10**k
    key = float((-1 if neg else 1) * m * q)
    assert matches_at_precision(key, key, rule) is True
    assert matches_at_precision(key + q, key, rule) is False
    assert matches_at_precision(key - q, key, rule) is False
    assert matches_at_precision(key + 0.4 * q, key, rule) is True
    assert matches_at_precision(key - 0.4 * q, key, rule) is True


@pytest.mark.criterion("C2")
@given(digits=st.integers(min_value=1000, max_value=999_999_999_999),
       exp=st.integers(min_value=-14, max_value=-3), extra=st.integers(min_value=1, max_value=6))
def test_ratio_key_rounded_at_or_below_the_rule_passes(rules, digits, exp, extra):
    # oracle: requirement 2 — for T with up to 12 significant digits, T rounded to its
    # 3rd significant figure passes, and so does T rounded to any finer place, and the
    # same values written as a percentage; the 3-s.f. value plus one unit in the 3rd
    # figure fails
    _, named = rules
    ratio = named("ratio")
    t = Decimal(digits).scaleb(exp)
    key = float(t)
    lead = t.adjusted()  # exponent of the leading digit, exact in decimal
    q3 = Decimal(1).scaleb(lead - 2)
    k3 = _round(t, q3)
    assert matches_at_precision(float(k3), key, ratio) is True
    finer = _round(t, Decimal(1).scaleb(lead - 2 - extra))
    assert matches_at_precision(float(finer), key, ratio) is True
    assert matches_at_precision(float(k3 * 100), key, ratio) is True
    assert matches_at_precision(float(k3 + q3), key, ratio) is False


@pytest.mark.criterion("C2")
@given(tenths=st.integers(min_value=-2000, max_value=2000).filter(lambda v: v != 0),
       rest=st.integers(min_value=-49, max_value=49))
def test_percent_key_at_a_tenth_of_a_point(rules, tenths, rest):
    # oracle: requirement 2 — a growth key of (tenths/10 + rest/1000) percent, |rest| < 50,
    # rounds to tenths/10 at 0.1 point: stated as a percentage or as a fraction it passes;
    # a tenth of a point more fails
    _, named = rules
    percent = named("percent")
    pct = Decimal(tenths) / 10 + Decimal(rest) / 1000
    key = float(pct / 100)
    stated = Decimal(tenths) / 10
    assert matches_at_precision(float(stated), key, percent) is True
    assert matches_at_precision(float(stated / 100), key, percent) is True
    assert matches_at_precision(float(stated + Decimal("0.1")), key, percent) is False


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(m=st.integers(min_value=1, max_value=999_999), k=st.integers(min_value=0, max_value=8),
       off=st.integers(min_value=-3, max_value=3), up=st.booleans())
def test_binary_noise_does_not_change_the_verdict(rules, m, k, off, up):
    # oracle: requirement 2 — both sides are normalised to 15 significant figures, so
    # moving either one ulp never changes the verdict of a value off by 0..3 quanta
    amount, _ = rules
    rule = amount(k)
    q = 10**k
    key = float(m * q)
    value = float(m * q + off * q)
    direction = math.inf if up else -math.inf
    plain = matches_at_precision(value, key, rule)
    assert plain is (off == 0)
    assert matches_at_precision(math.nextafter(value, direction), key, rule) is plain
    assert matches_at_precision(value, math.nextafter(key, direction), rule) is plain
