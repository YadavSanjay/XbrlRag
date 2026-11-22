"""C3: ``recompute_metric`` recomputes a derived value from its role values.

Oracle: requirement 6 of docs/specs/sec-companyfacts-check.md (its formula table, which is
the formula scripts/ddl/mv/34_mv_derived_metrics.sql uses; yoy follows mv_yoy_comparison's
``|base|`` convention). Every expected value is computed in the test from hand-chosen inputs.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_check.core import recompute_metric

# tolerance used throughout: the expected value is the same closed form evaluated in the
# test; rel=1e-12 only absorbs a different but equivalent operation order


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("metric", "values", "expected"),
    [
        ("GrossMarginPct", {"revenue": 400.0, "cost_of_revenue": 300.0}, (400.0 - 300.0) / 400.0),
        ("OperatingMarginPct", {"revenue": 400.0, "operating_income": 50.0}, 50.0 / 400.0),
        ("NetMarginPct", {"revenue": 400.0, "net_income": 40.0}, 40.0 / 400.0),
        ("RnDIntensityPct", {"revenue": 400.0, "rnd": 30.0}, 30.0 / 400.0),
        ("EffectiveTaxRate", {"tax": 21.0, "pretax_income": 100.0}, 21.0 / 100.0),
        ("CurrentRatio", {"current_assets": 150.0, "current_liabilities": 100.0}, 150.0 / 100.0),
        ("FreeCashFlow", {"operating_cash_flow": 120.0, "capex": 45.0}, 120.0 - 45.0),
        ("EBITDA", {"operating_income": 50.0, "dep_amort": 20.0}, 50.0 + 20.0),
        ("yoy", {"current": 110.0, "prior": 100.0}, (110.0 - 100.0) / abs(100.0)),
    ],
)
def test_every_row_of_the_table(metric, values, expected):
    # oracle: requirement 6 table — each row's formula applied by hand to the inputs:
    # 0.25, 0.125, 0.1, 0.075, 0.21, 1.5, 75, 70, 0.1
    assert recompute_metric(metric, values) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C3")
def test_effective_tax_rate_prefers_the_reported_rate():
    # oracle: requirement 6 — `effective_tax_rate` when present, else tax / pretax_income;
    # with both present the reported 0.19 wins over 21 / 100 = 0.21
    both = {"effective_tax_rate": 0.19, "tax": 21.0, "pretax_income": 100.0}
    assert recompute_metric("EffectiveTaxRate", both) == pytest.approx(0.19, rel=1e-12)
    only_rate = {"effective_tax_rate": 0.19}
    assert recompute_metric("EffectiveTaxRate", only_rate) == pytest.approx(0.19, rel=1e-12)


@pytest.mark.criterion("C3")
def test_yoy_with_negative_prior_uses_abs():
    # oracle: requirement 6 — yoy = (current - prior) / abs(prior): a loss narrowing from
    # -50 to -40 is (-40 - -50) / 50 = +0.2 (dividing by the signed prior would give -0.2)
    got = recompute_metric("yoy", {"current": -40.0, "prior": -50.0})
    assert got == pytest.approx((-40.0 - -50.0) / abs(-50.0), rel=1e-12)
    assert got == pytest.approx(0.2, rel=1e-12)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("metric", "zero_den", "good", "expected"),
    [
        ("GrossMarginPct", {"revenue": 0.0, "cost_of_revenue": 300.0},
         {"revenue": 400.0, "cost_of_revenue": 300.0}, 0.25),
        ("OperatingMarginPct", {"revenue": 0.0, "operating_income": 50.0},
         {"revenue": 400.0, "operating_income": 50.0}, 0.125),
        ("EffectiveTaxRate", {"tax": 21.0, "pretax_income": 0.0},
         {"tax": 21.0, "pretax_income": 100.0}, 0.21),
        ("CurrentRatio", {"current_assets": 150.0, "current_liabilities": 0.0},
         {"current_assets": 150.0, "current_liabilities": 100.0}, 1.5),
        ("yoy", {"current": 110.0, "prior": 0.0}, {"current": 110.0, "prior": 100.0}, 0.1),
    ],
)
def test_zero_denominator_gives_none(metric, zero_den, good, expected):
    # oracle: requirement 6 — None when a denominator is 0; the same metric with a non-zero
    # denominator gives the hand-computed value (so a constant None cannot pass)
    assert recompute_metric(metric, zero_den) is None
    assert recompute_metric(metric, good) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("metric", "missing", "full", "expected"),
    [
        ("GrossMarginPct", {"revenue": 400.0}, {"revenue": 400.0, "cost_of_revenue": 300.0},
         0.25),
        ("FreeCashFlow", {"operating_cash_flow": 120.0},
         {"operating_cash_flow": 120.0, "capex": 45.0}, 75.0),
        ("EBITDA", {"dep_amort": 20.0}, {"operating_income": 50.0, "dep_amort": 20.0}, 70.0),
        ("EffectiveTaxRate", {"tax": 21.0}, {"tax": 21.0, "pretax_income": 100.0}, 0.21),
        ("yoy", {"current": 110.0}, {"current": 110.0, "prior": 100.0}, 0.1),
    ],
)
def test_missing_role_gives_none(metric, missing, full, expected):
    # oracle: requirement 6 — None when a role is missing; with every role present the
    # hand-computed value
    assert recompute_metric(metric, missing) is None
    assert recompute_metric(metric, full) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C3")
def test_unknown_metric_gives_none():
    # oracle: requirement 6 — None when the metric is not in the table; the known metric on
    # the same roles gives 50 / 400
    roles = {"revenue": 400.0, "operating_income": 50.0}
    assert recompute_metric("ReturnOnEquity", roles) is None
    assert recompute_metric("OperatingMarginpct", roles) is None
    assert recompute_metric("OperatingMarginPct", roles) == pytest.approx(0.125, rel=1e-12)


_POS = st.floats(min_value=1.0, max_value=1e12, allow_nan=False, allow_infinity=False)
_ANY = st.floats(min_value=-1e12, max_value=1e12, allow_nan=False, allow_infinity=False)
_K = st.floats(min_value=1e-3, max_value=1e3, allow_nan=False, allow_infinity=False)

# metric -> (roles, closed form); denominators are drawn from _POS so never 0
_RATIOS = {
    "GrossMarginPct": (("revenue", "cost_of_revenue"), lambda v: (v[0] - v[1]) / v[0]),
    "OperatingMarginPct": (("revenue", "operating_income"), lambda v: v[1] / v[0]),
    "NetMarginPct": (("revenue", "net_income"), lambda v: v[1] / v[0]),
    "RnDIntensityPct": (("revenue", "rnd"), lambda v: v[1] / v[0]),
    "EffectiveTaxRate": (("pretax_income", "tax"), lambda v: v[1] / v[0]),
    "CurrentRatio": (("current_liabilities", "current_assets"), lambda v: v[1] / v[0]),
    "yoy": (("prior", "current"), lambda v: (v[1] - v[0]) / abs(v[0])),
}
_SUMS = {
    "FreeCashFlow": (("operating_cash_flow", "capex"), lambda v: v[0] - v[1]),
    "EBITDA": (("operating_income", "dep_amort"), lambda v: v[0] + v[1]),
}


@pytest.mark.criterion("C3")
@given(metric=st.sampled_from(sorted(_RATIOS)), den=_POS, num=_ANY, k=_K,
       negative_prior=st.booleans())
def test_ratio_metrics_are_scale_invariant(metric, den, num, k, negative_prior):
    # oracle: requirement 6 — each ratio is homogeneous of degree 0, so scaling every input
    # by k > 0 leaves it unchanged; the base value is the table's closed form
    roles, form = _RATIOS[metric]
    if metric == "yoy" and negative_prior:
        den = -den
    base = {roles[0]: den, roles[1]: num}
    scaled = {roles[0]: den * k, roles[1]: num * k}
    got = recompute_metric(metric, base)
    # tolerance: closed form vs implementation order, plus k-scaling round-off; abs floor
    # covers results near 0 where (a - b) cancels
    assert got == pytest.approx(form((den, num)), rel=1e-9, abs=1e-9)
    assert recompute_metric(metric, scaled) == pytest.approx(got, rel=1e-9, abs=1e-9)


@pytest.mark.criterion("C3")
@given(metric=st.sampled_from(sorted(_SUMS)), a=_ANY, b=_ANY, k=_K)
def test_cash_metrics_scale_with_k(metric, a, b, k):
    # oracle: requirement 6 — FreeCashFlow and EBITDA are linear, so scaling every input by
    # k > 0 scales the result by k; the base value is the table's closed form
    roles, form = _SUMS[metric]
    got = recompute_metric(metric, {roles[0]: a, roles[1]: b})
    # tolerance: a +/- b with inputs up to 1e12 rounds by ~2e-4 (abs=1e-3); after scaling,
    # inputs reach 1e15 and a*k +/- b*k vs k*(a +/- b) differs by ~0.2 at worst (abs=1.0)
    assert got == pytest.approx(form((a, b)), rel=1e-9, abs=1e-3)
    scaled = recompute_metric(metric, {roles[0]: a * k, roles[1]: b * k})
    assert scaled == pytest.approx(k * form((a, b)), rel=1e-9, abs=1.0)
