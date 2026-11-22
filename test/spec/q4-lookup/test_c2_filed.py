"""q4-lookup C2: a filed fourth quarter answers before a derivation; one equal to its year does not.

docs/specs/q4-lookup.md R3. MSFT's FY2026 10-K tags the fourth quarter of its cash dividends
(6,759M) and dividends per share (0.91); FSDS `num` states both, the year and the nine months.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.q4_lookup import select_q4


@pytest.mark.criterion("C2")
def test_msft_fy2026_filed_q4_dividends_answer(fx):
    # oracle: FSDS num: 10-K qtrs 1 at 20260630 = 6,759,000,000 (the filed Q4), qtrs 4 =
    # 27,035,000,000; 10-Q qtrs 3 at 20260331 = 20,278,000,000; by hand FY - 9M =
    # 6,757,000,000, which differs (the 10-K restates Q3 from 6,760M to 6,758M)
    filed = fx.fsds_value(fx.MSFT_10K, fx.DIVIDENDS, "20260630", 1)
    derived = (fx.fsds_value(fx.MSFT_10K, fx.DIVIDENDS, "20260630", 4)
               - fx.fsds_value(fx.MSFT_10Q, fx.DIVIDENDS, "20260331", 3))
    assert derived == 6_757_000_000 != filed
    rows = fx.store(fx.DIVIDENDS)
    assert [r["value"] for r in rows if r["fact_id"] == 105540] == [filed]

    out = select_q4(rows, 2026)
    assert set(out) == fx.KEYS
    assert out["status"] == "filed"
    assert out["value"] == filed == 6_759_000_000
    assert out["unit"] == "USD"
    assert tuple(out["fact_ids"]) == (105540,)
    assert tuple(out["accessions"]) == (fx.MSFT_10K,)
    assert (out["period_start"], out["period_end"], out["days"]) == ("2026-04-01", "2026-06-30", 91)
    assert out["decimals"] == -6
    assert out["basis"] == "latest"
    assert "filed" in out["note"].lower()
    assert str(int(derived)) in out["note"].replace(",", "")
    assert out["reason"] is None


@pytest.mark.criterion("C2")
def test_msft_fy2026_filed_q4_dividends_per_share(fx):
    # oracle: FSDS num: 10-K qtrs 1 at 20260630 = 0.91, qtrs 4 = 3.64; 10-Q qtrs 3 = 2.73;
    # by hand 3.64 - 2.73 = 0.91 agrees; stored unit shares*USD is shown USD/shares
    filed = fx.fsds_value(fx.MSFT_10K, fx.DPS, "20260630", 1)
    assert (fx.fsds_value(fx.MSFT_10K, fx.DPS, "20260630", 4)
            - fx.fsds_value(fx.MSFT_10Q, fx.DPS, "20260331", 3)) == filed
    out = select_q4(fx.store(fx.DPS), 2026)
    assert out["status"] == "filed"
    # tolerance: a per-share value of two decimals; abs 1e-12 accepts Decimal or float
    assert float(out["value"]) == pytest.approx(float(filed), abs=1e-12)
    assert out["unit"] == "USD/shares"
    assert tuple(out["fact_ids"]) == (105539,)
    assert (out["period_start"], out["period_end"]) == ("2026-04-01", "2026-06-30")


def _orcl_shape(fx, with_nine: bool) -> list[dict]:
    """ORCL FY2022-shaped: the year's revenue also tagged with a three-month context."""
    start = dt.date(2021, 6, 1)
    filed = dt.date(2022, 6, 21)
    rows = [
        fx.synth(1, 2022, "FY", start, 365, 42_440_000_000, "0000000002-22-000010", filed),
        fx.synth(2, 2022, "FY", dt.date(2022, 3, 1), 92, 42_440_000_000,
                 "0000000002-22-000010", filed),
    ]
    if with_nine:
        rows.append(fx.synth(3, 2022, "Q3", start, 273, 30_600_000_000,
                             "0000000002-22-000007", dt.date(2022, 3, 10)))
    return rows


@pytest.mark.criterion("C2")
def test_a_quarter_equal_to_its_year_is_set_aside(fx):
    # oracle: R3 by hand: ORCL's 10-K 0001564590-22-023675 context 2022-03-01..2022-05-31
    # carries 42,440M, its FY2022 revenue; with nine months 30,600M the Q4 is
    # 42,440M - 30,600M = 11,840M (derived); without nine months there is no answer
    out = select_q4(_orcl_shape(fx, with_nine=True), 2022)
    assert out["status"] == "derived"
    assert out["value"] == 11_840_000_000
    assert tuple(out["fact_ids"]) == (1, 3)
    fx.assert_refused(select_q4(_orcl_shape(fx, with_nine=False), 2022))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(fy_val=st.integers(min_value=1, max_value=10**12),
       nm_val=st.integers(min_value=0, max_value=10**12),
       q_val=st.integers(min_value=0, max_value=10**12))
def test_a_filed_quarter_wins_whatever_the_nine_months(fx, fy_val, nm_val, q_val):
    # oracle: R3/R5 by hand: a filed Q4 (not equal to the year) answers; without it, FY - 9M
    if q_val == fy_val:
        q_val += 1
    start = dt.date(2023, 7, 1)
    filed = dt.date(2024, 7, 30)
    year = fx.synth(20, 2024, "FY", start, 366, fy_val, "0000000003-24-000010", filed)
    q4 = fx.synth(21, 2024, "FY", dt.date(2024, 4, 1), 91, q_val, "0000000003-24-000010", filed)
    nine = fx.synth(10, 2024, "Q3", start, 275, nm_val, "0000000003-24-000007",
                    dt.date(2024, 4, 30))
    out = select_q4([year, q4, nine], 2024)
    assert out["status"] == "filed"
    assert out["value"] == Decimal(q_val)
    assert tuple(out["fact_ids"]) == (21,)
    without = select_q4([year, nine], 2024)
    assert without["status"] == "derived"
    assert without["value"] == Decimal(fy_val - nm_val)
