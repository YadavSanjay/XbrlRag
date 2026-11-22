"""q4-lookup C5: a Q4 is in one unit, the one the caller resolved.

docs/specs/q4-lookup.md R1, R2. MSFT's FY2026 revenue (with a synthetic EUR convenience
translation added to the 10-K's own column) and dividends per share (stored `shares*USD`).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.q4_lookup import select_q4


def _with_translation(fx) -> list[dict]:
    rows = fx.store(fx.REVENUE)
    year = next(r for r in rows if r["fact_id"] == 105742)
    rows.append(dict(year, fact_id=999_001, unit="EUR", value=year["value"] * 9 / 10))
    return rows


@pytest.mark.criterion("C5")
def test_two_units_without_a_unit_are_refused(fx):
    # oracle: R2 by hand: the year's own column carries USD and EUR, no unit given
    out = select_q4(_with_translation(fx), 2026)
    fx.assert_refused(out)
    assert "unit" in out["reason"].lower()


@pytest.mark.criterion("C5")
def test_the_resolved_unit_answers(fx):
    # oracle: C1's hand value (FSDS 331,839M - 241,832M) once the EUR row is set aside (R1)
    out = select_q4(_with_translation(fx), 2026, unit="USD")
    assert (out["status"], out["value"], out["unit"]) == ("derived", 90_007_000_000, "USD")


@pytest.mark.criterion("C5")
def test_per_share_unit_matches_its_stored_form(fx):
    # oracle: display_unit (ifrs-family-meaning R5): shares*USD is shown USD/shares; FSDS
    # 10-K qtrs 1 at 20260630 = 0.91
    assert {r["unit"] for r in fx.store(fx.DPS)} == {"shares*USD"}
    out = select_q4(fx.store(fx.DPS), 2026, unit="USD/shares")
    assert out["status"] == "filed"
    assert out["unit"] == "USD/shares"
    # tolerance: a two-decimal per-share value; abs 1e-12 accepts Decimal or float
    assert float(out["value"]) == pytest.approx(0.91, abs=1e-12)


@pytest.mark.criterion("C5")
@given(code=st.sampled_from(["EUR", "TWD", "JPY", "GBP", "CHF"]),
       other=st.sampled_from(["CAD", "AUD", "SEK"]))
def test_renaming_the_unit_changes_nothing_but_the_unit(fx, code, other):
    # oracle: R1 by hand: the unit selects rows, it never changes a value
    rows = [dict(r, unit=code) for r in fx.store(fx.REVENUE)]
    out = select_q4(rows, 2026, unit=code)
    assert (out["status"], out["value"], out["unit"]) == ("derived", 90_007_000_000, code)
    fx.assert_refused(select_q4(rows, 2026, unit=other))
