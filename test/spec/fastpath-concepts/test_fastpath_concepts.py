"""Spec tests for fastpath-concepts: the router's fast path must not map a metric to a
near-miss concept family (docs/specs/fastpath-concepts.md).

`IntentRouter._match_concept_family` is a staticmethod: no API key, no LLM, no DB.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.intent_router import IntentRouter

match = IntentRouter._match_concept_family

# Expected values, with their US-GAAP oracle:
#   AssetsCurrent / LiabilitiesCurrent are distinct elements from Assets / Liabilities.
CURRENT = {
    "total current assets": "CurrentAssets",
    "current assets": "CurrentAssets",
    "total current liabilities": "CurrentLiabilities",
    "current liabilities": "CurrentLiabilities",
}

# Ratios, margins and non-GAAP measures: computed from several facts, never one tagged fact.
DERIVED = [
    "gross margin",
    "operating margin",
    "net margin",
    "net profit margin",
    "EBITDA",
    "EBITDA margin",
    "free cash flow",
    "return on equity",
    "debt to equity",
    "current ratio",
    "revenue growth",
]

# (direct name, its family) paired with (sibling, what the sibling must map to).
CONTRAST = [
    ("gross profit", "GrossProfit", "gross margin", None),
    ("operating income", "OperatingIncome", "operating margin", None),
    ("operating income", "OperatingIncome", "EBITDA", None),
    ("net income", "NetIncome", "net margin", None),
    ("revenue", "Revenue", "revenue growth", None),
    ("net sales", "Revenue", "revenue growth", None),
    ("total assets", "TotalAssets", "total current assets", "CurrentAssets"),
    ("total liabilities", "TotalLiabilities", "current liabilities", "CurrentLiabilities"),
    ("capital expenditures", "Capex", "free cash flow", None),
    ("effective tax rate", "EffectiveTaxRate", "return on equity", None),
]

RATIO_BASES = ["revenue", "net income", "operating income", "gross profit"]
RATIO_QUALIFIERS = ["margin", "growth", "per share"]

# Every (phrase, expected) the spec fixes, for the formatting-invariance property.
EXPECTED = {
    **CURRENT,
    **{p: None for p in DERIVED},
    **{direct: fam for direct, fam, _, _ in CONTRAST},
    **{sib: fam for _, _, sib, fam in CONTRAST},
}


@st.composite
def reformatted(draw, phrase: str) -> str:
    """The same phrase with different case, padding, inner whitespace and a trailing
    period — formatting that does not change what it names."""
    case = draw(st.sampled_from([str.lower, str.upper, str.title, lambda s: s]))
    words = case(phrase).split(" ")
    gaps = [" " * draw(st.integers(min_value=1, max_value=3)) for _ in words[1:]]
    body = words[0] + "".join(g + w for g, w in zip(gaps, words[1:], strict=True))
    pad_left = " " * draw(st.integers(min_value=0, max_value=3))
    pad_right = " " * draw(st.integers(min_value=0, max_value=3))
    period = draw(st.sampled_from(["", "."]))
    return f"{pad_left}{body}{period}{pad_right}"


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_current_assets_and_liabilities_have_their_own_families():
    # oracle: US-GAAP — us-gaap:AssetsCurrent and us-gaap:LiabilitiesCurrent are distinct
    # elements from us-gaap:Assets and us-gaap:Liabilities
    for phrase, family in CURRENT.items():
        assert match(phrase) == family, phrase


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_total_prefix_does_not_change_the_current_family():
    # oracle: metamorphic — "total current assets" and "current assets" name the same
    # US-GAAP element (AssetsCurrent); likewise for liabilities
    for base in ("current assets", "current liabilities"):
        assert match(base) is not None, base
        assert match(f"total {base}") == match(base), base


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_ratio_margin_and_derived_phrases_do_not_fast_path():
    # oracle: none of these is a single tagged US-GAAP fact — each is a ratio, margin or
    # non-GAAP measure computed from several facts (mv_derived_metrics derives them)
    for phrase in DERIVED:
        assert match(phrase) is None, phrase


@pytest.mark.criterion("C2")
@given(st.sampled_from(DERIVED).flatmap(reformatted))
def test_derived_phrases_do_not_fast_path_in_any_formatting(phrase):
    # oracle: property — formatting does not turn a ratio into a tagged fact
    assert match(phrase) is None


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_direct_names_keep_their_family_while_siblings_do_not():
    # oracle: US-GAAP element names for the direct names; the sibling is a ratio,
    # derived measure or more specific element (see C1/C2)
    for direct, family, sibling, sibling_family in CONTRAST:
        assert match(direct) == family, direct
        assert match(sibling) == sibling_family, sibling


@pytest.mark.criterion("C3")
@given(st.sampled_from(CONTRAST), st.data())
def test_contrast_pairs_hold_under_reformatting(pair, data):
    # oracle: property — each side of a contrast pair keeps its expected family whatever
    # the formatting
    direct, family, sibling, sibling_family = pair
    assert match(data.draw(reformatted(direct))) == family
    assert match(data.draw(reformatted(sibling))) == sibling_family


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_ratio_forming_qualifiers_point_cases():
    # oracle: a margin, growth rate or per-share figure is not the underlying value —
    # today "revenue per share" maps to DividendsPerShare and "operating income growth"
    # to OperatingIncome, both wrong
    assert match("revenue per share") is None
    assert match("operating income growth") is None
    assert match("gross profit margin") is None
    assert match("net income growth") is None


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.sampled_from(RATIO_BASES), st.sampled_from(RATIO_QUALIFIERS))
def test_any_ratio_qualifier_on_a_base_metric_yields_none(base, qualifier):
    # oracle: metamorphic — appending margin/growth/per share turns the base's tagged
    # value into a ratio, rate or per-share figure
    assert match(base) is not None  # the base itself is a direct name
    assert match(f"{base} {qualifier}") is None


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_formatting_point_cases():
    # oracle: formatting does not change meaning; expected families from C1-C3
    assert match("  Total   Current Assets. ") == "CurrentAssets"
    assert match("Total Assets.") == "TotalAssets"
    assert match(" GROSS  MARGIN ") is None


@pytest.mark.criterion("C5")
@given(st.data())
def test_every_specified_phrase_is_formatting_invariant(data):
    # oracle: property — every phrase the spec fixes maps to the same expected family
    # however it is cased, padded, spaced or terminated
    for phrase, expected in EXPECTED.items():
        assert match(data.draw(reformatted(phrase))) == expected, phrase
