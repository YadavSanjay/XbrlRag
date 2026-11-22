"""Spec tests for units-currency-integrity, screen side: C4 (`derived_metric_unit`) and C5
(a derived screen ranks in one currency and names who it left out).

docs/specs/units-currency-integrity.md. The database is faked at `psycopg2.connect`
(conftest.py, "Fake database contract"); the screen runs for real over 33 / 32 filers'
stored facts.
"""

from __future__ import annotations

import random
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_screen import DERIVED_METRICS, derived_metric_unit

MONETARY = {"EBITDA", "FreeCashFlow", "NetDebt"}
PER_SHARE = {"BookValuePerShare", "DilutedEpsDerived", "FreeCashFlowPerShare"}
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _rule(metric, units):
    """Requirement 1.1 written out."""
    if not units or any(u is None for u in units) or metric not in DERIVED_METRICS:
        return None
    cur = {u for u in units if _CURRENCY.match(u)}
    if len(cur) > 1:
        return None
    if metric in MONETARY:
        return units[0] if len(cur) == 1 and all(u in cur for u in units) else None
    if metric in PER_SHARE:
        if len(cur) == 1 and "shares" in units and all(u in cur or u == "shares"
                                                       for u in units):
            return f"{next(iter(cur))}/shares"
        return None
    return "pure"


# =========================================================================== C4
@pytest.mark.criterion("C4")
def test_derived_metric_unit_point_cases():
    # oracle: Requirement 1.1 by hand on the stored component units (Notes)
    assert derived_metric_unit("FreeCashFlow", ["TWD", "TWD"]) == "TWD"  # TSM FY2025
    assert derived_metric_unit("FreeCashFlow", ["USD", "USD"]) == "USD"  # AAPL FY2025
    assert derived_metric_unit("FreeCashFlowPerShare", ["USD", "shares", "USD"]) == "USD/shares"
    assert derived_metric_unit("FreeCashFlowMarginPct", ["TWD", "TWD", "TWD"]) == "pure"
    assert derived_metric_unit("EffectiveTaxRate", ["Rate"]) == "pure"  # AVGO, AMD
    assert derived_metric_unit("GrossMarginPct", ["USD", "USD", "USD"]) == "pure"
    assert derived_metric_unit("FreeCashFlow", ["TWD", "USD"]) is None
    assert derived_metric_unit("FreeCashFlowMarginPct", ["TWD", "TWD", "USD"]) is None
    assert derived_metric_unit("DilutedEpsDerived", ["USD"]) is None  # no share count
    assert derived_metric_unit("FreeCashFlow", ["USD", "shares"]) is None
    assert derived_metric_unit("FreeCashFlow", []) is None
    assert derived_metric_unit("NetDebt", ["USD", None]) is None
    assert derived_metric_unit("ProfitabilityScore", ["USD"]) is None


METRICS = sorted(DERIVED_METRICS) + ["ProfitabilityScore"]
UNITS = ["USD", "TWD", "EUR", "shares", "pure", "Rate", None]


@pytest.mark.criterion("C4")
@given(st.sampled_from(METRICS), st.lists(st.sampled_from(UNITS), max_size=5))
def test_derived_metric_unit_matches_the_rule(metric, units):
    # oracle: Requirement 1.1 written out in _rule
    assert derived_metric_unit(metric, units) == _rule(metric, units)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.sampled_from(METRICS), st.lists(st.sampled_from(UNITS), max_size=5),
       st.randoms(use_true_random=False), st.sampled_from(["JPY", "CNY", "KRW"]))
def test_order_and_currency_label_do_not_matter(metric, units, rnd, code):
    # oracle: metamorphic — a set of facts has no order; a currency code is a label: a
    # monetary or per-share unit follows the relabelling, "pure" and None stay
    # anchor: a fixed case, so a constant result cannot satisfy the relation
    assert derived_metric_unit("FreeCashFlow", ["TWD", "TWD"]) == "TWD"
    shuffled = list(units)
    rnd.shuffle(shuffled)
    base = derived_metric_unit(metric, units)
    assert derived_metric_unit(metric, shuffled) == base
    relabelled = [code if u == "TWD" else u for u in units]
    got = derived_metric_unit(metric, relabelled)
    assert got == (base.replace("TWD", code) if isinstance(base, str) else base)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.sampled_from(METRICS), st.lists(st.sampled_from(UNITS), max_size=5),
       st.lists(st.booleans(), min_size=5, max_size=5))
def test_pure_and_rate_are_interchangeable(metric, units, flips):
    # oracle: metamorphic — Requirement 1 treats every dimensionless unit like "pure", so
    # swapping "pure" and "Rate" on any components never changes the result
    # anchor: a fixed case, so a constant result cannot satisfy the relation
    assert derived_metric_unit("EffectiveTaxRate", ["Rate"]) == "pure"
    swap = {"pure": "Rate", "Rate": "pure"}
    swapped = [swap.get(u, u) if f else u for u, f in zip(units, flips, strict=False)]
    assert derived_metric_unit(metric, swapped) == derived_metric_unit(metric, units)


# =========================================================================== C5
def _by_value(rows):
    """Tickers in descending value order, ties by ticker (ranking is a stable sort)."""
    return [t for _, t in sorted(((-v, t) for t, v in rows.items()))]


def _fcf(comps):
    return comps["operating_cash_flow"][2] - comps["capex"][2]


def _margin(comps):
    return (comps["operating_cash_flow"][2] - comps["capex"][2]) / comps["revenue"][2]


def _exclusions(result):
    return (result.to_dict().get("disclosure") or {}).get("exclusions") or []


@pytest.mark.criterion("C5")
def test_fy2025_free_cash_flow_screen_leaves_tsm_out(fx):
    # oracle: S4 (FCF = OCF - capex) on each filer's filed facts; S6 — TSM reports in TWD
    store = fx.build_store("FreeCashFlow", 2025, fx.FCF_FY2025)
    result = fx.run_derived_screen(store, 2025)
    usd = {t: _fcf(c) for t, _, _, _, c in fx.FCF_FY2025 if t != "TSM"}
    expected = _by_value(usd)
    assert [r["ticker"] for r in result.rows] == expected
    assert [r["rank"] for r in result.rows] == list(range(1, 33))
    assert all(r["unit"] == "USD" for r in result.rows)
    assert result.rows[0]["ticker"] == "AAPL"
    assert result.rows[0]["value"] == pytest.approx(98_767_000_000.0, rel=1e-12)  # exact int
    assert next(r["rank"] for r in result.rows if r["ticker"] == "MSFT") == 3
    assert result.rows[-1]["ticker"] == "INTC"
    assert result.rows[-1]["value"] == pytest.approx(-4_949_000_000.0, rel=1e-12)  # exact
    assert _exclusions(result) == [{"ticker": "TSM", "reason": "currency", "currency": "TWD"}]
    assert "TSM" not in result.to_dict()["disclosure"]["ranked"]


@pytest.mark.criterion("C5")
def test_fy2024_fcf_margin_ranks_tsm_as_a_ratio(fx):
    # oracle: S4 (FCF margin) on filed facts; a ratio of TWD over TWD has no currency;
    # R1.3's ratio policy (decided 2026-11-07): every filer is ranked whatever its currency
    store = fx.build_store("FreeCashFlowMarginPct", 2024, fx.FCF_MARGIN_FY2024)
    result = fx.run_derived_screen(store, 2024)
    usd = [t for t, _, _, _, c in fx.FCF_MARGIN_FY2024
           if all(u == "USD" for _, u, _ in c.values())]
    assert len(usd) == 31 and len(fx.FCF_MARGIN_FY2024) == 32  # 31 USD filers and TSM
    # One ranking holds TSM and every USD filer, ranks 1..32 without a gap.
    assert sorted(r["ticker"] for r in result.rows) == sorted(usd + ["TSM"])
    assert [r["rank"] for r in result.rows] == list(range(1, 33))
    margins = {t: _margin(c) for t, _, _, _, c in fx.FCF_MARGIN_FY2024}
    assert [r["ticker"] for r in result.rows] == _by_value(margins)
    tsm = next(r for r in result.rows if r["ticker"] == "TSM")
    assert tsm["rank"] == 6
    assert tsm["unit"] == "pure"
    # (1,826,177,100,000 - 956,006,500,000) / 2,894,307,700,000; float division only
    assert tsm["value"] == pytest.approx(870_170_600_000 / 2_894_307_700_000, rel=1e-12)
    assert [r["ticker"] for r in result.rows[4:7]] == ["MSFT", "TSM", "QCOM"]
    assert all(r["unit"] == "pure" for r in result.rows)
    assert _exclusions(result) == []


@pytest.mark.criterion("C5")
def test_tsm_row_mixing_twd_and_usd_is_not_ranked(fx):
    # oracle: Requirement 1–2 — OCF in TWD (fact 91562) less capex in USD (fact 91587) has
    # no unit; the exclusion names the units met
    rows = [(t, cik, end, acc, dict(c, capex=fx.TSM_FY2025_USD_CAPEX) if t == "TSM" else c)
            for t, cik, end, acc, c in fx.FCF_FY2025]
    result = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, rows), 2025)
    assert "TSM" not in [r["ticker"] for r in result.rows]
    assert len(result.rows) == 32
    assert _exclusions(result) == [
        {"ticker": "TSM", "reason": "unit_mismatch", "units": ["TWD", "USD"]}]


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(st.randoms(use_true_random=False))
def test_ticker_permutation_permutes_the_ranking(fx, rnd):
    # oracle: metamorphic — tickers are labels: relabelling filers relabels the ranking
    # and the excluded filer the same way
    tickers = [t for t, *_ in fx.FCF_FY2025]
    perm = list(tickers)
    rnd.shuffle(perm)
    to = dict(zip(tickers, perm, strict=True))
    base = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, fx.FCF_FY2025), 2025)
    rows = [(to[t], cik, end, acc, c) for t, cik, end, acc, c in fx.FCF_FY2025]
    moved = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, rows), 2025)
    assert [r["ticker"] for r in moved.rows] == [to[r["ticker"]] for r in base.rows]
    assert [r["value"] for r in moved.rows] == [r["value"] for r in base.rows]
    assert _exclusions(moved) == [{"ticker": to["TSM"], "reason": "currency",
                                   "currency": "TWD"}]


def _with_tsm_unit(rows, unit):
    return [(t, cik, end, acc,
             {k: (fid, unit, v) for k, (fid, _, v) in c.items()} if t == "TSM" else c)
            for t, cik, end, acc, c in rows]


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(st.sampled_from(["JPY", "EUR", "CNY", "KRW", "GBP"]))
def test_tsm_currency_label_only_changes_the_exclusion(fx, code):
    # oracle: metamorphic — another non-USD code is excluded the same way; in USD the same
    # facts are ranked first (1,002,565,100,000 > AAPL's 98,767,000,000)
    base = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, fx.FCF_FY2025), 2025)
    other = fx.run_derived_screen(
        fx.build_store("FreeCashFlow", 2025, _with_tsm_unit(fx.FCF_FY2025, code)), 2025)
    assert [r["ticker"] for r in other.rows] == [r["ticker"] for r in base.rows]
    assert _exclusions(other) == [{"ticker": "TSM", "reason": "currency", "currency": code}]
    usd = fx.run_derived_screen(
        fx.build_store("FreeCashFlow", 2025, _with_tsm_unit(fx.FCF_FY2025, "USD")), 2025)
    assert usd.rows[0]["ticker"] == "TSM" and usd.rows[0]["unit"] == "USD"
    assert usd.rows[0]["value"] == pytest.approx(1_002_565_100_000.0, rel=1e-12)  # exact
    assert [r["ticker"] for r in usd.rows[1:]] == [r["ticker"] for r in base.rows]
    assert _exclusions(usd) == []
    # A ratio does not care which single currency its inputs share.
    ratio = fx.run_derived_screen(fx.build_store(
        "FreeCashFlowMarginPct", 2024, _with_tsm_unit(fx.FCF_MARGIN_FY2024, code)), 2024)
    assert next(r["rank"] for r in ratio.rows if r["ticker"] == "TSM") == 6


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(st.floats(min_value=1e-3, max_value=1e3, allow_nan=False))
def test_scaling_usd_filers_keeps_the_order(fx, k):
    # oracle: metamorphic — FCF is linear in its inputs, so scaling every USD filer's facts
    # by k > 0 scales their FCF by k and keeps their order; TSM stays excluded
    scaled = [(t, cik, end, acc,
               c if t == "TSM" else {key: (fid, u, v * k) for key, (fid, u, v) in c.items()})
              for t, cik, end, acc, c in fx.FCF_FY2025]
    base = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, fx.FCF_FY2025), 2025)
    moved = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, scaled), 2025)
    assert [r["ticker"] for r in moved.rows] == [r["ticker"] for r in base.rows]
    for a, b in zip(moved.rows, base.rows, strict=True):
        # tolerance: two float products and a subtraction; rel=1e-9, abs for near-zero FCF
        assert a["value"] == pytest.approx(b["value"] * k, rel=1e-9, abs=1e-3)
    assert _exclusions(moved) == _exclusions(base)
    assert len(moved.rows) == 32


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_point_case_permutation_with_a_fixed_seed(fx):
    # oracle: metamorphic — a reversed roster ranks the same filers in the same order
    rows = list(reversed(fx.FCF_FY2025))
    a = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, fx.FCF_FY2025), 2025)
    b = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, rows), 2025)
    assert [r["ticker"] for r in b.rows] == [r["ticker"] for r in a.rows]
    assert _exclusions(b) == _exclusions(a) == [
        {"ticker": "TSM", "reason": "currency", "currency": "TWD"}]
    random.Random(7).shuffle(rows)  # any order of the store gives the same screen
    c = fx.run_derived_screen(fx.build_store("FreeCashFlow", 2025, rows), 2025)
    assert [r["ticker"] for r in c.rows] == [r["ticker"] for r in a.rows]
