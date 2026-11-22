"""C2: unchanged without a later report, no answer without both years
(docs/specs/yoy-growth-s1.md, requirement 1).

Values are FSDS ``num`` of the named accession (checked when the conftest loads); which
filings exist is ``dei.json``.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.sql_derived import s1_yoy_pick

TOL = 5e4  # half of $0.1M: amounts tagged in millions or hundred-thousands
GROWTH_TOL = 1e-6  # FSDS values are exact integers in millions; the hand ratio is exact

# ticker, family, fy, unit, current, prior, the one filing both come from, a value never used
OWN = [
    ("INTC", "Revenue", 2025, "USD", 52_853e6, 53_101e6, "0000050863-26-000011", None),
    ("TSM", "NetIncome", 2025, "TWD", 1_695_124.9e6, 1_157_523.9e6, "0001628280-26-025362",
     54_036.5e6),
]
# ticker, family, fy without a result: no corpus filing labelled fy - 1, or none labelled fy
NO_RESULT = [("INTC", "Revenue", 2022), ("TSM", "NetIncome", 2022), ("INTC", "Revenue", 2026)]
GRID = [("INTC", f, fy) for f in ("Revenue", "CashFromFinancing", "CashFromInvesting")
        for fy in (2023, 2024, 2025)] + [("META", "Capex", fy) for fy in (2023, 2024, 2025)] \
    + [("TSM", "NetIncome", fy) for fy in (2023, 2024, 2025)]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("case", OWN, ids=lambda c: f"{c[0]}-{c[1]}-FY{c[2]}")
def test_without_a_later_report_the_filing_s_own_comparison_answers(kit, case):
    # oracle: FSDS num of the latest filing (conftest ORACLE_POINTS); dei.json has no later
    # annual report of either filer; growth by hand
    ticker, family, fy, unit, cur_v, pri_v, accession, never = case
    got = s1_yoy_pick(kit.store_rows(ticker, [family]), fy)
    assert isinstance(got, dict)
    assert got["current"]["accession_no"] == accession
    assert got["prior"]["accession_no"] == accession
    assert got["current"]["value"] == pytest.approx(cur_v, abs=TOL)  # tagged in 0.1M
    assert got["prior"]["value"] == pytest.approx(pri_v, abs=TOL)  # tagged in 0.1M
    if never is not None:
        assert got["current"]["value"] != pytest.approx(never, abs=TOL)
    assert got["unit"] == unit
    assert got["value"] == pytest.approx(cur_v / pri_v - 1, abs=GROWTH_TOL)  # exact ratio
    assert got["not_meaningful"] is False
    assert got["fy_prior"] == fy - 1


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("case", NO_RESULT, ids=lambda c: f"{c[0]}-{c[1]}-FY{c[2]}")
def test_a_year_without_its_own_filing_has_no_growth(kit, case):
    # oracle: dei.json — no corpus annual report is labelled 2021 (INTC, TSM) or 2026 (INTC);
    # the year after it does have a growth (INTC/TSM FY2023 C1, INTC FY2025 above)
    ticker, family, fy = case
    rows = kit.store_rows(ticker, [family])
    assert s1_yoy_pick(rows, fy) is None
    neighbour = fy + 1 if fy == 2022 else fy - 1
    assert isinstance(s1_yoy_pick(rows, neighbour), dict)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=40)
@given(st.sampled_from(GRID))
def test_removing_the_prior_years_own_column_leaves_no_growth(kit, item):
    # oracle: requirement 1 — the prior leg is the fy - 1 filing's own column (as restated);
    # without that column the product's fy - 1 lookup has no answer, whatever later filings say
    ticker, family, fy = item
    rows = kit.store_rows(ticker, [family])
    assert isinstance(s1_yoy_pick(rows, fy), dict)
    own = kit.own_filing(ticker, fy - 1)
    end = kit.own_end(ticker, fy - 1)
    kept = [r for r in rows if not (r["accession_no"] == own and kit.iso(r["period_end"]) == end)]
    assert s1_yoy_pick(kept, fy) is None


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=40)
@given(st.sampled_from(GRID))
def test_a_prior_leg_in_another_unit_leaves_no_growth(kit, item):
    # oracle: units-currency-integrity — the product's compute refuses two currencies, so a
    # prior answered only in another unit has no growth to key
    ticker, family, fy = item
    rows = kit.store_rows(ticker, [family])
    base = s1_yoy_pick(rows, fy)
    assert isinstance(base, dict)
    end = kit.own_end(ticker, fy - 1)
    moved = [dict(r, unit="EUR") if kit.iso(r["period_end"]) == end else r for r in rows]
    assert s1_yoy_pick(moved, fy) is None


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(st.sampled_from(GRID), st.integers(min_value=1, max_value=50_000))
def test_a_non_positive_prior_is_not_meaningful(kit, item, millions):
    # oracle: pct-change-nm — a change from a base <= 0 is "n/m", keyed with the |base| ratio
    # (computed here by hand); a base of exactly 0 has no ratio, so no growth
    ticker, family, fy = item
    rows = kit.store_rows(ticker, [family])
    base = s1_yoy_pick(rows, fy)
    assert isinstance(base, dict)
    end = kit.own_end(ticker, fy - 1)
    negative = -1e6 * millions
    flipped = [dict(r, value=negative) if kit.iso(r["period_end"]) == end else r for r in rows]
    got = s1_yoy_pick(flipped, fy)
    assert isinstance(got, dict)
    assert got["not_meaningful"] is True
    c = got["current"]["value"]
    assert got["value"] == pytest.approx((c - negative) / abs(negative), rel=1e-12)  # same floats
    zeroed = [dict(r, value=0.0) if kit.iso(r["period_end"]) == end else r for r in rows]
    assert s1_yoy_pick(zeroed, fy) is None
