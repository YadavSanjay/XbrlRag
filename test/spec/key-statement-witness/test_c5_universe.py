"""C5: screen membership is decided by the filer's statement, not by our mapping.

Oracle: the fixtures' statements, checked by hand: a filer that presents the family's line is
ranked with that line's value; only a filer that presents none is ``metric_not_tagged``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import select_lines, universe_status

# tolerance: filed integer amounts; rel 1e-12 absorbs binary floating point only.
REL = 1e-12
Y25 = ("2025-01-01", "2025-12-31")
ROU = ("us-gaap:PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAsset"
       "AfterAccumulatedDepreciationAndAmortization")
PPE_NET = "us-gaap:PropertyPlantAndEquipmentNet"


def _u(kit, family, adsh, start, end, unit="USD", key=None, annual_value=None):
    e = kit.env()
    return universe_status(e.tables, e.rules, e.notes, e.resolutions, family, adsh, start, end,
                           unit, key, annual_value)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    ("family", "ticker", "start", "end", "concept", "value"),
    [
        # oracle: AMZN FY2025 10-K balance sheet "Property and equipment, net" 357,025
        ("PropertyPlantAndEquipment", "AMZN", None, "2025-12-31", ROU, 357_025e6),
        # oracle: AMZN FY2025 income statement "Interest expense" (2,274), tagged
        # InterestExpenseNonoperating (excluded as metric_not_tagged before the witness)
        ("InterestExpense", "AMZN", *Y25, "us-gaap:InterestExpenseNonoperating", 2_274e6),
        # oracle: MCD FY2025 balance sheet "Long-term debt" 39,973
        ("Debt", "MCD", None, "2025-12-31", "us-gaap:LongTermDebtNoncurrent", 39_973e6),
        # oracle: AMZN FY2025 income statement "Sales and marketing" 47,129 (MarketingExpense)
        ("SellingAndMarketing", "AMZN", *Y25, "us-gaap:MarketingExpense", 47_129e6),
        # oracle: AAPL FY2025 balance sheet "Term debt" 12,350 + 78,328 (two lines, no single
        # concept)
        ("Debt", "AAPL", None, "2025-09-27", None, 90_678e6),
    ],
)
def test_a_filer_that_presents_the_line_is_ranked(kit, family, ticker, start, end, concept,
                                                  value):
    # oracle: the rendered line named in the parametrize comment, checked by hand
    got = _u(kit, family, getattr(kit, ticker), start, end)
    assert (got["status"], got["reason"], got["concept"], got["witness"]) == (
        "ranked", None, concept, None)
    assert got["value"] == pytest.approx(value, rel=REL)


@pytest.mark.criterion("C5")
def test_a_filer_that_presents_no_line_is_metric_not_tagged(kit):
    # oracle: KO FY2025 10-K income statement shows "Selling, general and administrative
    # expenses" 14,521 and no selling-and-marketing line
    got = _u(kit, "SellingAndMarketing", kit.KO, *Y25)
    assert (got["status"], got["reason"], got["concept"], got["value"]) == (
        "metric_not_tagged", None, None, None)


@pytest.mark.criterion("C5")
def test_with_a_key_the_witness_decides(kit):
    # oracle: AMZN FY2025 PP&E key PropertyPlantAndEquipmentNet 357,025 equals the presented
    # line (agree_other_element, ranked); LMT 2025-12-31 PP&E key 11,292 from a 10-Q against
    # 8,875 on the 10-K balance sheet (held, value_recast); no FSDS data (held, no_fsds)
    k = kit.key("AMZN", PPE_NET, kit.AMZN, "2025-12-31", None, 2025)
    got = _u(kit, "PropertyPlantAndEquipment", kit.AMZN, None, "2025-12-31", key=k)
    assert (got["status"], got["concept"]) == ("ranked", PPE_NET)
    assert got["value"] == pytest.approx(357_025e6, rel=REL)
    assert got["witness"]["status"] == "agree_other_element"
    lmt = kit.key("LMT", PPE_NET, "0001628280-26-066928", "2025-12-31", None, 2025)
    held = _u(kit, "PropertyPlantAndEquipment", kit.LMT, None, "2025-12-31", key=lmt,
              annual_value=8_875e6)
    assert (held["status"], held["reason"], held["value"]) == ("held", "value_recast", None)
    gone = _u(kit, "SellingAndMarketing", kit.ABSENT, *Y25)
    assert (gone["status"], gone["reason"]) == ("held", "no_fsds")


FILINGS = ["AAPL", "KO", "META", "ORCL", "MCD", "VST", "PG", "AMZN", "AMD", "ABT", "LMT", "TSLA",
           "INTC"]
FAMILIES = ["Debt", "PropertyPlantAndEquipment", "InterestExpense", "SellingAndMarketing",
            "Inventory", "AccountsPayable", "TotalAssets", "Goodwill", "ResearchAndDevelopment",
            "OperatingIncome"]


@pytest.mark.criterion("C5")
@given(ticker=st.sampled_from(FILINGS), family=st.sampled_from(FAMILIES))
def test_metric_not_tagged_exactly_when_no_line_is_presented(kit, ticker, family):
    # oracle: requirement 5: without a key, metric_not_tagged iff the rule selects no line of
    # the filing (each sub row's own period end; durations are the year before it)
    e = kit.env()
    adsh = getattr(kit, ticker)
    sub = next(r for r in kit.raw_rows("sub") if r["adsh"] == adsh)
    p = sub["period"]
    end = f"{p[:4]}-{p[4:6]}-{p[6:]}"
    instant = e.rules[family]["statements"][0] == "BS"
    start = None if instant else (dt.date.fromisoformat(end) - dt.timedelta(days=363)).isoformat()
    lines = select_lines(e.tables, e.rules[family], adsh, start, end, "USD")
    got = _u(kit, family, adsh, start, end)
    assert (got["status"] == "metric_not_tagged") == (lines == [])
    assert got["status"] in {"ranked", "definition_excluded", "metric_not_tagged"}
