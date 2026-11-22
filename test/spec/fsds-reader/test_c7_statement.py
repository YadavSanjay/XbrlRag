"""C7: ``statement`` joins a filing's lines to their values, as filed per accession.

Oracle: the rendered statements of the AAPL FY2022 and FY2023 10-Ks and of AAPL FY2024,
COST FY2023 and TSM 2024, checked by hand in our local EDGAR copies, and requirements 10-11.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_reference.fsds import (
    load_tables,
    period_value,
    presented_value,
    statement,
    statement_lines,
)

# tolerance: FSDS values are the filed amounts (at most 4 decimals); rel 1e-12 absorbs only
# binary floating-point representation, so a wrong figure can never pass.
REL = 1e-12

FY22 = ("2021-09-26", "2022-09-24")  # AAPL fiscal 2022, 52 weeks ended September 24, 2022


@pytest.mark.criterion("C7")
def test_restated_prior_period_keeps_each_filings_value(kit):
    # oracle: hand check — cash flows line "Other" (adjustments to net income), 2022 column:
    # the FY2022 10-K (aapl-20220924.htm) prints 111 (fact -111, sign="-"); the FY2023 10-K
    # (aapl-20230930.htm) prints 1,006 for the same year (fact -1,006): restated.
    tables = load_tables(kit.dirs)
    first = statement(tables, kit.AAPL_22, "CF", *FY22)
    later = statement(tables, kit.AAPL_23, "CF", *FY22)
    assert [(r["line"]["tag"], r["value"], r["presented"]) for r in first] == [
        ("OtherNoncashIncomeExpense", -111_000_000.0, 111_000_000.0)]
    assert [(r["line"]["tag"], r["value"], r["presented"]) for r in later] == [
        ("OtherNoncashIncomeExpense", -1_006_000_000.0, 1_006_000_000.0)]
    # the FY2024 10-K repeats the restated figure for 2022 (hand check: 1,006 in its 2022 column)
    cf24 = statement(tables, kit.AAPL_24, "CF", *FY22)
    other = [r for r in cf24 if r["line"]["tag"] == "OtherNoncashIncomeExpense"]
    assert [(r["value"], r["presented"]) for r in other] == [(-1_006_000_000.0, 1_006_000_000.0)]


@pytest.mark.criterion("C7")
def test_aapl_fy2024_income_statement(kit):
    # oracle: hand check — AAPL FY2024 10-K consolidated statements of operations, 2024 column
    # (millions): Total net sales 391,035; Gross margin 180,683; Operating income 123,216;
    # Net income 93,736; basic EPS $6.11; 15 SEC lines from net sales to diluted shares.
    # SEC's plabel for the "Total net sales" row is the filer's "Net sales" label.
    tables = load_tables(kit.dirs)
    rows = statement(tables, kit.AAPL_24, "IS", "2023-10-01", "2024-09-28")
    assert len(rows) == 15
    by_tag = {r["line"]["tag"]: r for r in rows}
    assert rows[0]["line"]["plabel"] == "Net sales"
    assert rows[0]["value"] == pytest.approx(391_035_000_000.0, rel=REL)
    assert by_tag["GrossProfit"]["value"] == pytest.approx(180_683_000_000.0, rel=REL)
    assert by_tag["OperatingIncomeLoss"]["value"] == pytest.approx(123_216_000_000.0, rel=REL)
    assert by_tag["NetIncomeLoss"]["presented"] == pytest.approx(93_736_000_000.0, rel=REL)
    assert by_tag["EarningsPerShareBasic"]["value"] == pytest.approx(6.11, rel=REL)


@pytest.mark.criterion("C7")
def test_cost_53_week_year_and_tsm_currency(kit):
    # oracle: hand check — COST FY2023 10-K "Total revenue" 242,290 for the 53 weeks ended
    # 2023-09-03; TSM 2024 20-F "NET REVENUE" NT$2,894,307.7 million and "Finance costs"
    # NT$(10,495.4) million. TSM's lines need a uom (TWD and USD both present).
    tables = load_tables(kit.dirs)
    cost = statement(tables, kit.COST_23, "IS", "2022-08-29", "2023-09-03")
    assert cost[0]["line"]["plabel"] == "Total revenue"
    assert cost[0]["value"] == pytest.approx(242_290_000_000.0, rel=REL)
    with pytest.raises(ValueError):
        statement(tables, kit.TSM_24, "IS", "2024-01-01", "2024-12-31")
    tsm = statement(tables, kit.TSM_24, "IS", "2024-01-01", "2024-12-31", "TWD")
    by_tag = {r["line"]["tag"]: r for r in tsm}
    assert by_tag["Revenue"]["value"] == pytest.approx(2_894_307_700_000.0, rel=REL)
    assert by_tag["FinanceCosts"]["presented"] == pytest.approx(-10_495_400_000.0, rel=REL)


PERIODS = {
    "AAPL_24": ("2023-10-01", "2024-09-28", None),
    "MSFT_24": ("2023-07-01", "2024-06-30", None),
    "ORCL_25": ("2024-06-01", "2025-05-31", None),
    "AVGO_24": ("2023-10-30", "2024-11-03", None),
    "COST_23": ("2022-08-29", "2023-09-03", None),
    "TSM_24": ("2024-01-01", "2024-12-31", "USD"),
}


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(acc=st.sampled_from(sorted(PERIODS)), stmt=st.sampled_from(["IS", "CF"]))
def test_each_row_is_its_lines_period_value(kit, acc, stmt):
    # oracle: requirement 10 — the join is line by line, in statement_lines order
    tables = load_tables(kit.dirs)
    adsh = getattr(kit, acc)
    start, end, uom = PERIODS[acc]
    rows = statement(tables, adsh, stmt, start, end, uom)
    lines = statement_lines(tables, adsh, stmt)
    assert [r["line"] for r in rows] == lines
    for r in rows:
        ln = r["line"]
        v = period_value(tables, adsh, ln["tag"], start, end, uom, ln["version"])
        assert r["value"] == v
        assert r["presented"] == presented_value(ln, v)
    if stmt == "IS":
        assert rows and all(r["value"] is not None for r in rows[:3])
