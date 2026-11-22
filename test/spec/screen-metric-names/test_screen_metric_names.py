"""Spec tests for screen-metric-names: a planned derived-metric name resolves to a
metric that exists, or to nothing (docs/specs/screen-metric-names.md). Pure: no DB.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_screen import (
    normalize_metric_name,
    resolve_derived_metric,
)

# mv_derived_metrics.metric_name, 2026-10-15.
KNOWN = [
    "AssetTurnover", "BookValuePerShare", "CashRatio", "CurrentRatio",
    "DaysInventoryOutstanding", "DaysSalesOutstanding", "DebtToEquity", "DilutedEpsDerived",
    "EBITDA", "EBITDAMarginPct", "EffectiveTaxRate", "FreeCashFlow", "FreeCashFlowMarginPct",
    "FreeCashFlowPerShare", "GrossMarginPct", "InterestCoverage", "InventoryTurnover",
    "NetDebt", "NetIncomeGrowthPct", "NetMarginPct", "OperatingMarginPct", "QuickRatio",
    "ReturnOnAssetsPct", "ReturnOnEquityPct", "ReturnOnInvestedCapitalPct",
    "Revenue3YrCagrPct", "RevenueGrowthPct", "RnDIntensityPct", "SbcAdjOperatingMarginPct",
]
ALIASES = {"netprofitmargin": "NetMarginPct"}


def _norm_oracle(name):
    """The normalisation written out."""
    s = re.sub(r"[^a-z0-9]", "", name.lower())
    s = s.replace("yearoveryear", "").replace("yoy", "")
    for suffix in ("percentage", "percent", "pct"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def _resolve_oracle(name, known, aliases):
    """C1 written out."""
    if name in known:
        return name
    hits = [k for k in known if _norm_oracle(k) == _norm_oracle(name)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return None
    target = aliases.get(_norm_oracle(name))
    return target if target in known else None


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_resolve_point_cases():
    # oracle: the definition, by hand on the 2026-10-15 metric names
    cases = {
        "NetMarginPct": "NetMarginPct",
        "NetMargin": "NetMarginPct",
        "net margin %": "NetMarginPct",
        "NetProfitMargin": "NetMarginPct",
        "RevenueYoYGrowth": "RevenueGrowthPct",
        "Revenue Year-over-Year Growth Percent": "RevenueGrowthPct",
        "GrossMargin": "GrossMarginPct",
        "ReturnOnEquity": "ReturnOnEquityPct",
        "EBITDA": "EBITDA",
        "ProfitabilityScore": None,
    }
    for name, expected in cases.items():
        assert resolve_derived_metric(name, KNOWN, ALIASES) == expected, name
    assert resolve_derived_metric("NetProfitMargin", KNOWN, {"netprofitmargin": "Nope"}) is None


names = st.one_of(st.sampled_from(KNOWN),
                  st.text(st.characters(codec="ascii"), max_size=24),
                  st.sampled_from(["net margin", "Gross-Margin %", "revenue yoy growth",
                                   "NetProfitMargin", "ebitda margin pct", "x"]))


@pytest.mark.criterion("C1")
@given(names)
def test_resolve_matches_the_rule(name):
    # oracle: C1 written out (`_resolve_oracle`); a fixed alias case anchors it
    assert resolve_derived_metric(name, KNOWN, ALIASES) == _resolve_oracle(name, KNOWN, ALIASES)
    assert resolve_derived_metric("NetProfitMargin", KNOWN, ALIASES) == "NetMarginPct"


@pytest.mark.criterion("C1")
def test_ambiguous_normalised_form_is_unresolved():
    # oracle: the definition — two known metrics with one normalised form
    assert resolve_derived_metric("Margin", ["MarginPct", "MarginPercent"], {}) is None
    assert resolve_derived_metric("MarginPct", ["MarginPct", "MarginPercent"], {}) == "MarginPct"


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_normalize_point_cases():
    # oracle: the definition, by hand
    assert normalize_metric_name("Revenue Year-over-Year Growth Percent") == "revenuegrowth"
    assert normalize_metric_name("NetMarginPct") == "netmargin"
    assert normalize_metric_name("ROE pct pct") == "roepct"
    assert normalize_metric_name("YoY") == ""


@pytest.mark.criterion("C2")
@given(st.text(max_size=30))
def test_normalize_matches_the_rule(name):
    # oracle: the normalisation written out (`_norm_oracle`); a fixed case anchors it
    assert normalize_metric_name(name) == _norm_oracle(name)
    assert normalize_metric_name("Gross Margin %") == "grossmargin"


# --------------------------------------------------------------------------- C3
def _restyle(name, rnd):
    """Same name, different case, spacing and punctuation."""
    out = []
    for ch in name:
        out.append(ch.upper() if rnd.random() < 0.5 else ch.lower())
        if rnd.random() < 0.2:
            out.append(rnd.choice([" ", "-", "_", "."]))
    return "".join(out)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(names, st.randoms(use_true_random=False),
       st.dictionaries(st.from_regex(r"[a-z]{3,10}", fullmatch=True), st.sampled_from(KNOWN),
                       max_size=3))
def test_properties(name, rnd, extra_aliases):
    # oracle: the C3 properties, checked directly
    assert all(resolve_derived_metric(k, KNOWN, ALIASES) == k for k in KNOWN)
    out = resolve_derived_metric(name, KNOWN, ALIASES)
    assert out is None or out in KNOWN
    if name not in KNOWN:
        assert resolve_derived_metric(_restyle(name, rnd), KNOWN, ALIASES) == out
    if out is not None:
        assert resolve_derived_metric(name, KNOWN, {**extra_aliases, **ALIASES}) == out


@pytest.mark.criterion("C3")
def test_properties_point_case():
    # oracle: the C3 properties by hand — restyled names resolve alike; known names stay
    assert resolve_derived_metric("net-MARGIN pct", KNOWN, ALIASES) == "NetMarginPct"
    assert resolve_derived_metric("EBITDAMarginPct", KNOWN, ALIASES) == "EBITDAMarginPct"
    assert resolve_derived_metric("GrossMargin", KNOWN, {"grossmargin": "NetMarginPct"}) == (
        "GrossMarginPct")
