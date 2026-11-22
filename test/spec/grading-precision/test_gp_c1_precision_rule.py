"""C1: the precision rule of each real dev case (docs/specs/grading-precision.md, req. 1)."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.precision import precision_rule

REL = 1e-12  # quanta are powers of ten; the tolerance only absorbs float formatting


def _check(rule, kind, compare, quantum=None, sig_figs=None, rel_tol=None):
    assert rule.kind == kind
    assert rule.compare == compare
    if quantum is None:
        assert rule.quantum is None
    else:
        assert rule.quantum == pytest.approx(quantum, rel=REL)
    assert rule.sig_figs == sig_figs
    if rel_tol is None:
        assert rule.rel_tol is None
    else:
        assert rule.rel_tol == pytest.approx(rel_tol, rel=REL)


@pytest.mark.criterion("C1")
def test_reported_amounts_and_shares_use_the_key_facts_decimals(dev):
    # oracle: requirement 1's table read against each fixture's key.inputs[].decimals:
    # INTC OperatingIncomeLoss -6 -> 1e6; TSM CashAndCashEquivalents -5 -> 1e5;
    # CRM WeightedAverageNumberOfSharesOutstandingBasic -6 -> 1e6; AAPL shares -3 -> 1e3;
    # NFLX G&A as presented (point in time) -3 -> 1e3
    _check(precision_rule(dev("intc_opinc")), "amount", "quantum", quantum=1e6)
    _check(precision_rule(dev("tsm_cash")), "amount", "quantum", quantum=1e5)
    _check(precision_rule(dev("crm_shares")), "shares", "quantum", quantum=1e6)
    _check(precision_rule(dev("aapl_shares")), "shares", "quantum", quantum=1e3)
    _check(precision_rule(dev("nflx_ga_pit")), "amount", "quantum", quantum=1e3)


@pytest.mark.criterion("C1")
def test_per_share_ratio_and_percent_kinds(dev):
    # oracle: requirement 1 — unit USD/shares is cents whatever the decimals (NVDA EPS
    # carries 2, MSFT DPS carries none); compute.ratio is 3 significant figures;
    # compute.yoy_growth and compute.cagr are 0.1 percentage point = 0.001 on a fraction
    _check(precision_rule(dev("nvda_eps")), "per_share", "quantum", quantum=0.01)
    _check(precision_rule(dev("msft_dps")), "per_share", "quantum", quantum=0.01)
    _check(precision_rule(dev("mu_eps_pit")), "per_share", "quantum", quantum=0.01)
    _check(precision_rule(dev("msft_dps_diff")), "per_share", "quantum", quantum=0.01)
    _check(precision_rule(dev("nflx_rd_ratio")), "ratio", "sig_figs", sig_figs=3)
    _check(precision_rule(dev("tsm_rd_ratio")), "ratio", "sig_figs", sig_figs=3)
    _check(precision_rule(dev("msft_tax_yoy")), "percent", "quantum", quantum=0.001)
    _check(precision_rule(dev("qcom_pretax_cagr")), "percent", "quantum", quantum=0.001)
    _check(precision_rule(dev("meta_intinc_yoy")), "percent", "quantum", quantum=0.001)


@pytest.mark.criterion("C1")
def test_aggregates_use_the_coarsest_integer_decimals(dev):
    # oracle: requirement 1 — dividends sum inputs (-6, -6, -6) -> 1e6; equity median
    # inputs (None, -6, -6, None, -6, -6, -6) -> coarsest integer -6 -> 1e6; GOOGL
    # difference inputs (-8, None) -> 1e8; P&G difference (-6, -6) -> 1e6
    _check(precision_rule(dev("bigtech_div_sum")), "aggregate", "quantum", quantum=1e6)
    _check(precision_rule(dev("bigtech_equity_median")), "aggregate", "quantum", quantum=1e6)
    _check(precision_rule(dev("googl_ccl_diff")), "aggregate", "quantum", quantum=1e8)
    _check(precision_rule(dev("pg_nonop_diff")), "aggregate", "quantum", quantum=1e6)


@pytest.mark.criterion("C1")
def test_rows_use_their_own_input(dev):
    # oracle: requirement 1 — a row's decimals are those of the input with its value and
    # period_end. Goodwill top 3: MSFT 119,651,000,000 @ 2026-06-30 has decimals None
    # (rel_tol 1e-4 fallback), NVDA 20,832,000,000 @ 2026-01-25 has -6. TSM gross profit
    # rows are all -5. UNP share trend rows have None. META equity: FY2023 -6, FY2024 None.
    goodwill = dev("bigtech_goodwill_top3")
    msft, nvda = goodwill.expected_rows
    _check(precision_rule(goodwill, msft), "amount", "rel_tol", rel_tol=1e-4)
    _check(precision_rule(goodwill, nvda), "amount", "quantum", quantum=1e6)
    tsm = dev("tsm_gp_trend")
    for row in tsm.expected_rows:
        _check(precision_rule(tsm, row), "amount", "quantum", quantum=1e5)
    unp = dev("unp_shares_trend")
    for row in unp.expected_rows:
        _check(precision_rule(unp, row), "shares", "rel_tol", rel_tol=1e-4)
    meta = dev("meta_equity_trend")
    fy23, fy24, _ = meta.expected_rows
    _check(precision_rule(meta, fy23), "amount", "quantum", quantum=1e6)
    _check(precision_rule(meta, fy24), "amount", "rel_tol", rel_tol=1e-4)
    # A row carrying its own decimals uses them.
    _check(precision_rule(goodwill, {**msft, "decimals": -9}), "amount", "quantum",
           quantum=1e9)


@pytest.mark.criterion("C1")
def test_inf_is_exact_and_untagged_cases_are_legacy(dev_record):
    # oracle: requirement 1 — INF decimals compare exactly; a case without a shape: tag
    # keeps its own rel_tol (here 0.01, to show the case's value is the one used)
    record = dev_record("intc_opinc")
    record["key"]["inputs"][0]["decimals"] = "INF"
    _check(precision_rule(EvalCase.from_dict(record)), "amount", "exact")
    legacy = dev_record("intc_opinc")
    legacy["tags"] = [t for t in legacy["tags"] if not t.startswith("shape:")]
    legacy["rel_tol"] = 0.01
    _check(precision_rule(EvalCase.from_dict(legacy)), "legacy", "rel_tol", rel_tol=0.01)
    agg = dev_record("pg_nonop_diff")
    for item in agg["key"]["inputs"]:
        item["decimals"] = "INF"
    _check(precision_rule(EvalCase.from_dict(agg)), "aggregate", "exact")
    agg["key"]["inputs"][0]["decimals"] = None
    _check(precision_rule(EvalCase.from_dict(agg)), "aggregate", "rel_tol", rel_tol=1e-4)


decimals_lists = st.lists(st.one_of(st.integers(min_value=-9, max_value=2), st.none()),
                          min_size=1, max_size=6).filter(
    lambda ds: any(d is not None for d in ds))


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(decs=decimals_lists, seed=st.randoms(use_true_random=False))
def test_aggregate_rule_is_order_free_and_follows_the_coarsest(dev_record, decs, seed):
    # oracle: requirement 1 — "the coarsest integer decimals among the inputs" is the
    # minimum of the integers, so order and missing (None) inputs do not change it, and a
    # coarser integer input moves it to that input's quantum (10**-d)
    base = dev_record("bigtech_div_sum")
    template = base["key"]["inputs"][0]
    inputs = []
    for i, d in enumerate(decs):
        item = copy.deepcopy(template)
        item["decimals"] = d
        item["value"] = 1_000_000_000 * (i + 1)
        inputs.append(item)
    want = 10.0 ** (-min(d for d in decs if d is not None))

    def rule_for(items):
        record = copy.deepcopy(base)
        record["key"]["inputs"] = items
        return precision_rule(EvalCase.from_dict(record))

    got = rule_for(inputs)
    _check(got, "aggregate", "quantum", quantum=want)
    shuffled = list(inputs)
    seed.shuffle(shuffled)
    assert rule_for(shuffled) == got
    missing = copy.deepcopy(template)
    missing["decimals"] = None
    assert rule_for(inputs + [missing]) == got
    coarser = copy.deepcopy(template)
    coarser["decimals"] = min(d for d in decs if d is not None) - 1
    _check(rule_for(inputs + [coarser]), "aggregate", "quantum", quantum=want * 10)
