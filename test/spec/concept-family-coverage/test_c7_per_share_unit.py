"""C7: a per-share answer shows its unit as `<currency>/shares`.

Oracle: R4 of docs/specs/concept-family-coverage.md. The store renders an XBRL divide unit
(numerator USD, denominator shares) as the product `shares*USD`; SEC companyfacts reports
the same EPS facts under unit `USD/shares` (LLY FY2025 diluted EPS 22.95, fixture
`companyfacts: match` after that mapping), and units-currency-integrity's `expr_unit` reads
`USD/shares` as USD over shares.
"""

from __future__ import annotations

import pytest
from cfc_support import lookup, row_of
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import display_unit


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("stored,shown", [
    ("shares*USD", "USD/shares"),
    ("shares*TWD", "TWD/shares"),
    ("USD*shares", "USD/shares"),
    ("USD", "USD"),
    ("TWD", "TWD"),
    ("shares", "shares"),
    ("pure", "pure"),
    ("Rate", "Rate"),
    ("USD/shares", "USD/shares"),
    ("shares*shares", "shares*shares"),
    ("shares*usd", "shares*usd"),
    ("shares*USD*EUR", "shares*USD*EUR"),
    (None, None),
])
def test_display_unit_point_cases(stored, shown):
    # oracle: R4 by hand — exactly two factors, `shares` and an ISO-shaped code
    assert display_unit(stored) == shown


@pytest.mark.criterion("C7")
def test_lly_diluted_eps_is_usd_per_share():
    # oracle: LLY FY2025 income statement "Earnings per share - diluted" $22.95; stored
    # unit shares*USD (fact 223527), companyfacts unit USD/shares
    assert row_of("lly_fy2025_eps", 223527)["unit"] == "shares*USD"
    r = lookup("lly_fy2025_eps", "EarningsPerShareDiluted")
    assert r.status.value == "OK", r.resolution_notes
    assert r.citation.fact_id == 223527
    assert r.value == pytest.approx(22.95, rel=1e-12)  # stored value
    assert r.unit == "USD/shares"
    assert r.to_dict()["unit"] == "USD/shares"


codes = st.from_regex(r"[A-Z]{3}", fullmatch=True)


@pytest.mark.criterion("C7")
@given(codes, st.booleans())
def test_any_currency_per_share_maps(code, shares_first):
    # oracle: R4 written out
    stored = f"shares*{code}" if shares_first else f"{code}*shares"
    assert display_unit(stored) == f"{code}/shares"
    assert display_unit(display_unit(stored)) == f"{code}/shares"  # idempotent


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ*/_0123456789",
               min_size=1, max_size=20))
def test_other_units_are_unchanged(unit):
    # oracle: R4 — only `shares` times one ISO-shaped code changes
    parts = unit.split("*")
    per_share = (len(parts) == 2 and "shares" in parts
                 and all(p == "shares" or (len(p) == 3 and p.isascii() and p.isupper()
                                           and p.isalpha()) for p in parts)
                 and parts.count("shares") == 1)
    out = display_unit(unit)
    if per_share:
        code = parts[0] if parts[1] == "shares" else parts[1]
        assert out == f"{code}/shares"
    else:
        assert out == unit
    assert display_unit(out) == out
