"""C6: a cross-filer screen never ranks a substitute beside primaries as one measure.

Oracle: the us-gaap 2025 documentation of ``CostsAndExpenses`` ("Total costs of sales and
operating expenses for the period") against ``OperatingExpenses`` ("... except for the portion
of these expenses which can be clearly related to production and included in cost of sales"),
and the rendered income statements of AMZN and AAPL, checked by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import statement_witness, universe_status

# tolerance: filed integer amounts; rel 1e-12 absorbs binary floating point only.
REL = 1e-12
SUB = "substitute:includes_cost_of_revenue"
NOTE = "Total costs and expenses, including cost of revenue."
CAE = "us-gaap:CostsAndExpenses"
AMZN_Y = {2025: ("2025-01-01", "2025-12-31", 636_949e6),
          2024: ("2024-01-01", "2024-12-31", 569_366e6)}
AAPL_Y = {2025: ("2024-09-29", "2025-09-27", 62_151e6),
          2024: ("2023-10-01", "2024-09-28", 57_467e6)}


def _u(kit, adsh, start, end, key=None):
    e = kit.env()
    return universe_status(e.tables, e.rules, e.notes, e.resolutions, "OperatingExpenses", adsh,
                           start, end, "USD", key)


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("year", [2024, 2025])
def test_amzn_costs_and_expenses_is_excluded_and_aapl_is_ranked(kit, year):
    # oracle: AMZN FY2025 10-K income statements "Total operating expenses" 569,366 (2024) /
    # 636,949 (2025), which include "Cost of sales"; AAPL FY2025 10-K "Total operating
    # expenses" 57,467 (2024) / 62,151 (2025), below gross margin
    start, end, amzn_value = AMZN_Y[year]
    got = _u(kit, kit.AMZN, start, end)
    assert (got["status"], got["reason"]) == ("definition_excluded", SUB)
    k = kit.key("AMZN", CAE, kit.AMZN, end, start, year)
    with_key = _u(kit, kit.AMZN, start, end, k)
    assert (with_key["status"], with_key["reason"], with_key["concept"]) == (
        "definition_excluded", SUB, CAE)
    assert with_key["value"] == pytest.approx(amzn_value, rel=REL)
    a_start, a_end, aapl_value = AAPL_Y[year]
    aapl = _u(kit, kit.AAPL, a_start, a_end)
    assert (aapl["status"], aapl["concept"]) == ("ranked", "us-gaap:OperatingExpenses")
    assert aapl["value"] == pytest.approx(aapl_value, rel=REL)


@pytest.mark.criterion("C6")
def test_the_witness_states_the_substitution(kit):
    # oracle: requirement 1 step 7 and the concept-family-coverage stated-substitute code/note
    e = kit.env()
    k = kit.key("AMZN", CAE, kit.AMZN, "2025-12-31", "2025-01-01", 2025)
    w = statement_witness(e.tables, e.rules, e.notes, "OperatingExpenses", k, kit.AMZN)
    assert w["status"] == "agree"
    assert w["substitution"] == {"code": "includes_cost_of_revenue", "note": NOTE,
                                 "concept": CAE}
    doc = kit.taxonomy["elements"][CAE]["documentation"]
    assert doc == "Total costs of sales and operating expenses for the period."


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_the_exclusion_follows_the_element_not_the_label(kit):
    # oracle: requirement 6: the substitute is the element's definition; relabelling AMZN's
    # line "Total costs and expenses" or "Operating expenses" changes nothing
    e = kit.env()
    for label in ("Total costs and expenses", "Operating expenses", "TOTAL OPERATING EXPENSES"):
        tables = {k: [dict(r) for r in v] for k, v in e.tables.items()}
        for r in tables["pre"]:
            if r["adsh"] == kit.AMZN and r["tag"] == "CostsAndExpenses":
                r["plabel"] = label
        got = universe_status(tables, e.rules, e.notes, e.resolutions, "OperatingExpenses",
                              kit.AMZN, "2025-01-01", "2025-12-31", "USD")
        assert (got["status"], got["reason"]) == ("definition_excluded", SUB), label


SCREENS = [
    ("OperatingExpenses", "AMZN", "2025-01-01", "2025-12-31"),
    ("OperatingExpenses", "AAPL", "2024-09-29", "2025-09-27"),
    ("DepreciationAndAmortization", "MCD", "2024-01-01", "2024-12-31"),
    ("InterestIncome", "AMZN", "2025-01-01", "2025-12-31"),
    ("RestructuringCharges", "INTC", "2023-01-01", "2023-12-30"),
    ("CashFromOperations", "TSLA", "2025-01-01", "2025-12-31"),
    ("PropertyPlantAndEquipment", "AMZN", None, "2025-12-31"),
]
INTC_RESTRUCTURING = ("definition_excluded", "substitute:includes_impairment_or_other")


@pytest.mark.criterion("C6")
@given(case=st.sampled_from(SCREENS))
def test_a_ranked_member_never_carries_a_substitute(kit, case):
    # oracle: requirement 6: ranked implies the member's concept is no substitute of the
    # family (INTC FY2025 10-K "Restructuring and other charges" 2023 (62) is tagged
    # RestructuringSettlementAndImpairmentProvisions)
    family, ticker, start, end = case
    e = kit.env()
    got = universe_status(e.tables, e.rules, e.notes, e.resolutions, family, getattr(kit, ticker),
                          start, end, "USD")
    subs = e.rules[family]["substitutes"]
    if got["status"] == "ranked":
        assert got["concept"] not in subs
    if got["status"] == "definition_excluded" and got["reason"].startswith("substitute:"):
        assert got["reason"].removeprefix("substitute:") in {s["code"] for s in subs.values()}
    assert got["status"] in {"ranked", "definition_excluded", "metric_not_tagged"}
    if (family, ticker) == ("RestructuringCharges", "INTC"):
        assert (got["status"], got["reason"]) == INTC_RESTRUCTURING
