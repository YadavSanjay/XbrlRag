"""C5: ``period_value`` returns a filing's as-filed consolidated value for one period.

Oracle: the rendered statements, checked by hand in our local EDGAR copies
(data/storage/sec_edgar/...), plus SEC readme (num key, coreg, value). Comparisons are
exact: FSDS values are the XBRL amounts with at most four decimals, and every expected
value below is an integer number of dollars (or a two-decimal EPS) printed in the filing.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_reference.fsds import fsds_uom, load_tables, period_value

# tolerance: FSDS values are the filed amounts (at most 4 decimals); rel 1e-12 absorbs only
# binary floating-point representation, so a wrong figure can never pass.
REL = 1e-12

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    ("acc", "tag", "start", "end", "uom", "expected"),
    [
        # AAPL FY2024 10-K, income statement "Total net sales", 2024: 391,035 (millions)
        ("AAPL_24", REV, "2023-10-01", "2024-09-28", None, 391_035_000_000.0),
        # same line, 2022 column (52 weeks ended 2022-09-24): 394,328
        ("AAPL_24", REV, "2021-09-26", "2022-09-24", "USD", 394_328_000_000.0),
        # MSFT FY2024 10-K, income statements "Total revenue", year ended June 30, 2024: 245,122
        ("MSFT_24", REV, "2023-07-01", "2024-06-30", "USD", 245_122_000_000.0),
        # ORCL FY2025 10-K, income statements "Total revenues", year ended May 31, 2025: 57,399
        ("ORCL_25", REV, "2024-06-01", "2025-05-31", None, 57_399_000_000.0),
        # ORCL same filing, "Cloud services and license support" (orcl: extension): 44,029
        ("ORCL_25", "CloudServicesAndLicenseSupportRevenue", "2024-06-01", "2025-05-31", None,
         44_029_000_000.0),
        # COST FY2023 10-K, income statements "Total revenue", 53 weeks ended 2023-09-03: 242,290
        ("COST_23", REV, "2022-08-29", "2023-09-03", None, 242_290_000_000.0),
        # AVGO FY2024 10-K, "Cost of products sold" (avgo: extension), fiscal 2024: 9,797
        ("AVGO_24", "CostofProductsSold", "2023-10-30", "2024-11-03", None, 9_797_000_000.0),
        # TSM 2024 20-F, consolidated statements of comprehensive income, "NET REVENUE" 2024:
        # NT$2,894,307.7 million and the US$88,268.0 million convenience translation
        ("TSM_24", "Revenue", "2024-01-01", "2024-12-31", "TWD", 2_894_307_700_000.0),
        ("TSM_24", "Revenue", "2024-01-01", "2024-12-31", "USD", 88_268_000_000.0),
        # CEG FY2023 10-K, balance sheet "Total assets (a)", Constellation Energy Corp,
        # 2023-12-31: 50,758
        ("CEG_23", "Assets", None, "2023-12-31", "USD", 50_758_000_000.0),
        # AAPL FY2024, "Earnings per share: Basic" 2024: $6.11 (companyfacts unit USD/shares)
        ("AAPL_24", "EarningsPerShareBasic", "2023-10-01", "2024-09-28", "USD/shares", 6.11),
    ],
)
def test_hand_checked_values(kit, acc, tag, start, end, uom, expected):
    # oracle: the rendered statement line named in the parametrize comment, checked by hand
    tables = load_tables(kit.dirs)
    assert period_value(tables, getattr(kit, acc), tag, start, end, uom) == expected


@pytest.mark.criterion("C5")
def test_absent_ambiguous_and_version(kit):
    # oracle: requirement 8 — no candidate gives None; TSM's 2024 revenue exists in TWD and
    # USD, so without a uom it is ambiguous; MSFT's "Depreciation, amortization, and other"
    # (22,287 for FY2024, hand-checked in the cash flows statement) exists only as msft:.
    tables = load_tables(kit.dirs)
    assert period_value(tables, kit.AAPL_24, REV, "2024-09-29", "2025-09-27") is None
    assert period_value(tables, kit.AAPL_24, "NoSuchTag", "2023-10-01", "2024-09-28") is None
    with pytest.raises(ValueError):
        period_value(tables, kit.TSM_24, "Revenue", "2024-01-01", "2024-12-31")
    dep = "DepreciationAmortizationAndOther"
    fy24 = ("2023-07-01", "2024-06-30")
    got = period_value(tables, kit.MSFT_24, dep, *fy24, "USD", kit.MSFT_24)
    assert got == pytest.approx(22_287_000_000.0, rel=REL)
    assert period_value(tables, kit.MSFT_24, dep, *fy24, "USD", "us-gaap/2023") is None
    # the AAPL value belongs to AAPL's accession only (requirement 11)
    assert period_value(tables, kit.MSFT_24, REV, "2023-10-01", "2024-09-28") is None


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("USD/shares", "USD"), ("TWD/shares", "TWD"), ("USD", "USD"),
        ("shares", "shares"), ("pure", "pure"), ("TWD", "TWD"),
    ],
)
def test_fsds_uom_points(unit, expected):
    # oracle: requirement 7 — no '/shares' uom among SEC's 301,943 num rows for our filers; AAPL
    # EPS 6.11 is a USD row in SEC's num and a USD/shares entry in companyfacts
    assert fsds_uom(unit) == expected


@pytest.mark.criterion("C5")
@given(unit=st.from_regex(r"[A-Za-z]{1,6}", fullmatch=True))
def test_fsds_uom_strips_only_per_share(unit):
    # oracle: requirement 7
    assert fsds_uom(f"{unit}/shares") == unit
    assert fsds_uom(unit) == unit


def _period_for(ddate_iso: str, qtrs: int) -> tuple[str | None, str]:
    """A period whose FSDS encoding is (ddate, qtrs): end on the month end, qtrs*91.3125 days."""
    if qtrs == 0:
        return None, ddate_iso
    end = dt.date.fromisoformat(ddate_iso)
    days = round(qtrs * 365.25 / 4)
    return (end - dt.timedelta(days=days - 1)).isoformat(), ddate_iso


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_every_consolidated_row_is_found_by_its_period(kit):
    # oracle: SEC's num key (adsh, tag, version, ddate, qtrs, uom, segments, coreg) is unique,
    # so each consolidated, non-nil raw row is the one value for its own period and unit
    tables = load_tables(kit.dirs)
    raw = [r for r in kit.raw_rows("num") if not r["segments"] and not r["coreg"] and r["value"]]
    assert len(raw) == 473  # consolidated, non-nil rows in the fixture excerpts
    for r in raw:
        d = f"{r['ddate'][:4]}-{r['ddate'][4:6]}-{r['ddate'][6:]}"
        start, end = _period_for(d, int(r["qtrs"]))
        got = period_value(tables, r["adsh"], r["tag"], start, end, r["uom"], r["version"])
        assert got == float(r["value"]), r


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(seed=st.integers(0, 100_000), delta=st.floats(1, 1e9, allow_nan=False))
def test_segment_and_coregistrant_rows_do_not_change_the_value(kit, seed, delta):
    # oracle: readme — segments are axis/member values and a non-NULL coreg is not the
    # consolidated entity; adding such rows with other values leaves the consolidated value
    tables = load_tables(kit.dirs)
    rng = random.Random(seed)
    extra = []
    for _ in range(3):
        pool = [r for r in tables["num"] if r["adsh"] == kit.AAPL_24 and r["tag"] == REV]
        base = rng.choice(pool)
        seg = "ProductOrService=Synthetic;"
        extra.append({**base, "segments": seg, "coreg": "", "value": f"{delta:.4f}"})
        co = {"segments": "LegalEntity=Sub;", "coreg": "Sub"}
        extra.append({**base, **co, "value": f"{-delta:.4f}"})
    noisy = dict(tables)
    noisy["num"] = tables["num"] + extra
    got = period_value(noisy, kit.AAPL_24, REV, "2023-10-01", "2024-09-28")
    assert got == pytest.approx(391_035_000_000.0, rel=REL)
    got = period_value(noisy, kit.CEG_23, "Assets", None, "2023-12-31", "USD")
    assert got == pytest.approx(50_758_000_000.0, rel=REL)
