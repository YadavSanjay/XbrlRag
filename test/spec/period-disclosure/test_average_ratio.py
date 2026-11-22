"""Spec tests for period-disclosure C4: average-based ratios use (opening + closing) / 2.

docs/specs/period-disclosure.md, requirement R4. AAPL's FY2022 10-K (0000320193-22-000108)
files both balance-sheet dates; the expected ratios are computed by hand in each test from
SEC companyfacts and FSDS `num`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.period_disclosure import average_based_ratio, opening_balance

ACC = "0000320193-22-000108"
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
KEYS = {"value", "basis", "denominator", "note"}


@pytest.mark.criterion("C4")
def test_aapl_fy2022_opening_balance_is_the_comparative_column(fx):
    # oracle: companyfacts: Assets at 2021-09-25 = 351,002M and 2022-09-24 = 352,755M; the
    # FY2022 revenue period starts 2021-09-26, the day after the opening balance-sheet date
    closing, opening = fx.fact(11022), fx.fact(11023)
    revenue = fx.fact(10919)
    assert fx.cf_entry(ACC, "us-gaap:Assets", None, "2021-09-25")["val"] == opening["value"]
    assert fx.cf_entry(ACC, "us-gaap:Assets", None, "2022-09-24")["val"] == closing["value"]
    picked = opening_balance([closing, opening], revenue["period_start"])
    assert picked is not None and picked["fact_id"] == 11023
    assert opening_balance([closing], revenue["period_start"]) is None


@pytest.mark.criterion("C4")
def test_aapl_fy2022_asset_turnover_on_the_average(fx):
    # oracle: by hand from FSDS num (0000320193-22-000108): revenue qtrs 4 at 2022-09-30 =
    # 394,328M; Assets 352,755M (2022) and 351,002M (2021):
    # 394,328 / ((352,755 + 351,002) / 2) = 394,328 / 351,878.5 = 1.1206368...
    revenue = fx.fsds_value(ACC, "RevenueFromContractWithCustomerExcludingAssessedTax",
                            "20220930", 4)
    closing = fx.fsds_value(ACC, "Assets", "20220930", 0)
    opening = fx.fsds_value(ACC, "Assets", "20210930", 0)
    assert (revenue, closing, opening) == (394328000000, 352755000000, 351002000000)
    out = average_based_ratio(revenue, closing, opening)
    assert set(out) == KEYS
    # tolerance: exact integer inputs; rel 1e-9 separates 1.12064 from the stored 1.11785
    assert out["value"] == pytest.approx(394328 / ((352755 + 351002) / 2), rel=1e-9)
    assert out["value"] == pytest.approx(1.1206368, rel=1e-6)  # the hand value, 8 s.f.
    assert out["denominator"] == pytest.approx((352755000000 + 351002000000) / 2, rel=1e-12)
    assert out["basis"] == "average"
    assert out["note"] is None


@pytest.mark.criterion("C4")
def test_no_opening_balance_falls_back_and_says_so(fx):
    # oracle: R4 by hand: 394,328 / 352,755 = 1.117853... on the closing balance, stated
    out = average_based_ratio(394328000000, 352755000000, None)
    assert out["value"] == pytest.approx(394328 / 352755, rel=1e-9)  # exact inputs
    assert out["basis"] == "closing"
    assert out["denominator"] == pytest.approx(352755000000, rel=1e-12)
    assert "opening" in out["note"].lower() and "closing" in out["note"].lower()


@pytest.mark.criterion("C4")
def test_zero_denominator_has_no_value():
    # oracle: R4: value is None when the denominator is 0
    assert average_based_ratio(100.0, 0.0, None)["value"] is None
    assert average_based_ratio(100.0, 5.0, -5.0)["value"] is None


_amounts = st.integers(min_value=1, max_value=10**12)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(num=_amounts, closing=_amounts, opening=_amounts, k=st.integers(min_value=2, max_value=1000))
def test_average_ratio_properties(num, closing, opening, k):
    # oracle: arithmetic of (opening + closing) / 2
    out = average_based_ratio(float(num), float(closing), float(opening))
    # tolerance: float division of integers up to 1e15
    assert out["value"] == pytest.approx(num / ((opening + closing) / 2), rel=1e-9)
    lo, hi = sorted((num / max(opening, closing), num / min(opening, closing)))
    assert lo * (1 - 1e-12) <= out["value"] <= hi * (1 + 1e-12)
    scaled = average_based_ratio(float(num * k), float(closing * k), float(opening * k))
    assert scaled["value"] == pytest.approx(out["value"], rel=1e-9)
    same = average_based_ratio(float(num), float(closing), float(closing))
    closing_only = average_based_ratio(float(num), float(closing), None)
    assert same["value"] == pytest.approx(closing_only["value"], rel=1e-12)  # same division


@pytest.mark.criterion("C4")
@given(start=st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31)),
       offsets=st.lists(st.integers(min_value=-800, max_value=800).filter(lambda x: x != 0),
                        max_size=6),
       with_match=st.booleans())
def test_only_the_instant_at_the_period_start_opens(start, offsets, with_match):
    # oracle: R4: the opening balance is stored with period_end == the period's start;
    # rows at other dates, and nil rows, never are
    rows = [{"fact_id": 100 + i, "value": 1000 + i,
             "period_end": (start + dt.timedelta(days=o)).isoformat()}
            for i, o in enumerate(offsets)]
    rows.append({"fact_id": 50, "value": None, "period_end": start.isoformat()})  # a nil
    if with_match:
        rows += [{"fact_id": 90, "value": 7, "period_end": start.isoformat()},
                 {"fact_id": 80, "value": 7, "period_end": start.isoformat()}]
    picked = opening_balance(rows, start.isoformat())
    if with_match:
        assert picked is not None and picked["fact_id"] == 80
    else:
        assert picked is None
