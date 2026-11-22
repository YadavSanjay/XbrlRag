"""C3: a different metric never answers the family.

Oracle: the decision table's `different metric` rows (maintainer, 2026-11-07) and the
filers' statements, where each such element is a separate line (a part, a per-share amount,
a related-party line) beside the family's own.
"""

from __future__ import annotations

import pytest
from cfc_support import CASES, CIK, G24, G25, IFRS25, NO_ENTRIES_CIK, lookup, store_rows
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import candidate_order, family_member

TSMC24 = "http://www.tsmc.com/20241231:"

# (decision, family, filer, element, an element that IS the family's primary for the filer)
DIFFERENT = [
    ("D8", "DepreciationAndAmortization", "AMD", G24 + "AdjustmentForAmortization",
     G24 + "OtherDepreciationAndAmortization"),
    ("D8", "DepreciationAndAmortization", "MRK", G25 + "AdjustmentForAmortization",
     G25 + "DepreciationDepletionAndAmortization"),
    ("D10", "Debt", "LLY", G25 + "DebtCurrent", G25 + "LongTermDebt"),
    ("D10", "Debt", "NFLX", G25 + "ShortTermBorrowings", G25 + "LongTermDebt"),
    ("D12", "Debt", "AAPL", G24 + "LongTermDebtCurrent", G24 + "LongTermDebt"),
    ("D12", "Debt", "AAPL", G24 + "CommercialPaper", G24 + "LongTermDebt"),
    ("D15", "SharesOutstanding", "KO", G25 + "CommonStockSharesIssued",
     G25 + "CommonStockSharesOutstanding"),
    ("D20", "DebtIssued", "MSFT",
     G24 + "ProceedsFromRepaymentsOfShortTermDebtMaturingInThreeMonthsOrLess",
     G24 + "ProceedsFromIssuanceOfLongTermDebt"),
    ("D20", "DebtRepaid", "CSCO",
     G24 + "ProceedsFromRepaymentsOfShortTermDebtMaturingInThreeMonthsOrLess",
     G24 + "RepaymentsOfLongTermDebt"),
    ("D23", "DebtRepaid", "MSFT", G24 + "PaymentsOfDebtRestructuringCosts",
     G24 + "RepaymentsOfLongTermDebt"),
    ("D23", "DebtRepaid", "TSM", IFRS25 + "PaymentsForDebtIssueCosts",
     G24 + "RepaymentsOfLongTermDebt"),
    ("D28", "Revenue", "ORCL", "http://www.oracle.com/20250531:SalesRevenueServicesNet1",
     G24 + "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("D28", "ProductRevenue", "ORCL", "http://www.oracle.com/20250531:SalesRevenueServicesNet1",
     G24 + "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("D30", "IncomeFromDiscontinuedOperations", "QCOM",
     G24 + "DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTaxPerBasicShare",
     G24 + "IncomeLossFromDiscontinuedOperationsNetOfTax"),
    ("D30", "IncomeFromDiscontinuedOperations", "QCOM",
     G24 + "DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTaxPerDilutedShare",
     G24 + "IncomeLossFromDiscontinuedOperationsNetOfTax"),
    ("D30", "IncomeFromDiscontinuedOperations", "MRK",
     G24 + "IncomeLossFromDiscontinuedOperationsNetOfTaxPerBasicShare",
     G24 + "IncomeLossFromDiscontinuedOperationsNetOfTax"),
    ("D30", "IncomeFromDiscontinuedOperations", "MRK",
     G24 + "IncomeLossFromDiscontinuedOperationsNetOfTaxPerDilutedShare",
     G24 + "IncomeLossFromDiscontinuedOperationsNetOfTax"),
    ("D36", "AccountsReceivable", "TSM",
     TSMC24 + "CurrentReceivablesFromRelatedPartiesRelatedToCoreBusiness",
     G24 + "AccountsReceivableNetCurrent"),
    ("D36", "AccountsReceivable", "TSM", TSMC24 + "OtherCurrentReceivablesFromRelatedParties",
     G24 + "AccountsReceivableNetCurrent"),
    ("D36", "AccountsReceivable", "TSM",
     IFRS25 + "TradeAndOtherCurrentReceivablesDueFromRelatedParties",
     G24 + "AccountsReceivableNetCurrent"),
    ("D37", "OtherNonOperatingIncome", "TSM", IFRS25 + "OtherGainsLosses",
     G24 + "OtherNonoperatingIncomeExpense"),
    ("D37", "OtherNonOperatingIncome", "TSM", IFRS25 + "OtherIncome",
     G24 + "OtherNonoperatingIncomeExpense"),
    ("D38", "Inventory", "CEG", G24 + "EnergyRelatedInventoryNaturalGasInStorage",
     G24 + "InventoryNet"),
    ("D38", "Inventory", "CEG", G24 + "InventoryRawMaterialsAndSuppliesNetOfReserves",
     G24 + "InventoryNet"),
    ("D40", "ShortTermInvestments", "KO", G24 + "MarketableSecurities",
     G24 + "ShortTermInvestments"),
    ("D42", "CostOfRevenue", "LLY",
     "http://www.lilly.com/20241231:CostOfSalesOperatingExpensesAndOtherNet",
     G24 + "CostOfGoodsAndServicesSold"),
    ("D42", "InterestExpense", "CEG",
     "http://www.constellationenergy.com/20241231:RelatedPartyTransactionInterestExpense",
     G24 + "InterestExpense"),
    ("D42", "SellingGeneralAndAdministrative", "MCD",
     G24 + "OtherSellingGeneralAndAdministrativeExpense",
     G24 + "SellingGeneralAndAdministrativeExpense"),
]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("decision,family,filer,element,anchor", DIFFERENT,
                         ids=[f"{d}-{f}-{e.rsplit(':', 1)[1][:40]}" for d, f, _, e, _ in DIFFERENT])
def test_different_metric_never_answers(decision, family, filer, element, anchor):
    # oracle: the decision table's `different metric` rows; the anchor is the family's own
    # declared primary, which stays a member
    a = family_member(family, CIK[filer], anchor)
    assert a is not None and a["role"] == "primary"
    m = family_member(family, CIK[filer], element)
    assert m is None or m["role"] == "component"
    if m is not None:
        assert 50 <= m["rank"] < 100


# (case, family, fact ids that must never answer, expected answer fact id or None, value)
NEVER = [
    ("lly_fy2025_debt", "Debt", {223582, 224245}, 224243, 42_503_000_000),
    ("pg_fy2026_debt", "Debt", {263403, 264861, 264855, 264976, 264857}, 264978, 22_842_000_000),
    ("nflx_fy2025_debt", "Debt", {188121, 188511}, 188127, 13_463_971_000),
    ("aapl_fy2022_debt", "Debt", {11030, 11032, 11827}, 11829, 98_959_000_000),
    ("ko_fy2025_shares", "SharesOutstanding", {195509}, 195335, 4_300_723_069),
    ("mcd_fy2025_shares", "SharesOutstanding", {217742}, 217548, 710_398_642),
    ("txn_fy2025_shares", "SharesOutstanding", {183787}, 183616, 907_550_774),
    ("amd_fy2024_da", "DepreciationAndAmortization", {19328, 19738}, 19325, 671_000_000),
    ("mrk_fy2025_da", "DepreciationAndAmortization", {232101, 233168}, 232104, 3_045_000_000),
    ("lly_fy2024_cor", "CostOfRevenue", {221215}, 221197, 8_418_300_000),
    ("ceg_fy2024_interest", "InterestExpense", {122967}, 122964, 506_000_000),
    ("tsm_fy2025_nonop", "OtherNonOperatingIncome", {90991, 91003, 93356}, 91007, 105_563_000_000),
    ("tsm_fy2024_ar", "AccountsReceivable", {86955, 90636, 86958, 90642}, 88192, 270_683_200_000),
    ("ko_fy2024_sti", "ShortTermInvestments", {192648, 192646}, 192644, 2_020_000_000),
    ("qcom_fy2023_discops", "IncomeFromDiscontinuedOperations", {176782, 176791}, 176773,
     -107_000_000),
    ("msft_fy2023_debtflow", "DebtRepaid", {45309}, 45312, 2_750_000_000),
]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("case_id,family,never,fact_id,value", NEVER, ids=[n[0] for n in NEVER])
def test_real_filings_never_answer_with_a_different_metric(case_id, family, never, fact_id, value):
    # oracle: the filer's statement — the family's line (value in the table) sits beside
    # the different metric's line (the `never` facts: current part, issued shares, the
    # amortization line, the related-party and per-share lines)
    r = lookup(case_id, family)
    assert r.status.value == "OK", r.resolution_notes
    assert r.citation.fact_id not in never
    assert r.citation.fact_id == fact_id
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("case_id,family,anchor", [
    ("ceg_fy2024_inventory", "Inventory", G24 + "InventoryNet"),
    ("mcd_fy2023_sga", "SellingGeneralAndAdministrative",
     G24 + "SellingGeneralAndAdministrativeExpense"),
])
def test_only_parts_present_is_not_found(case_id, family, anchor):
    # oracle: D38 (CEG: two inventory lines, no total) and D42 (MCD: "Other" is part of
    # SG&A, no SG&A total tagged) — the family is correctly not tagged
    assert family_member(family, CASES[case_id]["cik"], anchor)["role"] == "primary"
    rows = store_rows(case_id, family)
    assert [r for r in rows if r["concept_role"] != "component"] == []
    assert lookup(case_id, family).status.value == "NOT_FOUND"


COMPONENTS = [("Debt", G25 + "DebtCurrent"), ("Debt", G25 + "ShortTermBorrowings"),
              ("Debt", G25 + "NotesPayable"), ("SharesOutstanding",
               G25 + "CommonStockSharesIssued"),
              ("DepreciationAndAmortization", G25 + "AmortizationOfIntangibleAssets"),
              ("AccountsPayable", G25 + "AccountsPayableAndAccruedLiabilitiesCurrent")]
WITH_COMPONENTS = [n for n in NEVER if n[1] in {f for f, _ in COMPONENTS}]


@pytest.mark.criterion("C3")
@given(st.sampled_from(WITH_COMPONENTS), st.data())
def test_adding_components_never_changes_the_answer(case, data):
    # oracle: metamorphic — a component (any value, period, presentation, fact id) is a
    # candidate for the checks but never the answer (R2, R3)
    case_id, family, _, fact_id, value = case
    rows = store_rows(case_id, family)
    concepts = [c for f, c in COMPONENTS if f == family]
    periods = sorted({r["period_end"] for r in rows})
    extra = []
    for i in range(data.draw(st.integers(min_value=1, max_value=4))):
        concept = data.draw(st.sampled_from(concepts))
        m = family_member(family, NO_ENTRIES_CIK, concept)
        extra.append({**rows[0], "fact_id": 900_000 + i, "concept": concept,
                      "concept_pref_rank": m["rank"], "concept_role": m["role"],
                      "substitution_code": None, "substitution_note": None,
                      "value": data.draw(st.integers(min_value=-10**12, max_value=10**12)),
                      "period_end": data.draw(st.sampled_from(periods)),
                      "presented": data.draw(st.booleans())})
    both = rows + extra
    assert both[candidate_order(both)[0]]["fact_id"] == fact_id
    r = lookup(case_id, family, both)
    assert r.citation.fact_id == fact_id
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.sampled_from(NEVER), st.randoms(use_true_random=False))
def test_component_rows_are_never_ordered(case, rnd):
    # oracle: R2 — `candidate_order` leaves every component row out, in any input order
    case_id, family, _, fact_id, _ = case
    rows = store_rows(case_id, family)
    unit = next(r["unit"] for r in rows if r["fact_id"] == fact_id)
    rows = [r for r in rows if r["unit"] == unit]  # the lookup's one-unit restriction comes first
    rnd.shuffle(rows)
    order = candidate_order(rows)
    assert rows[order[0]]["fact_id"] == fact_id
    assert all(rows[i]["concept_role"] != "component" for i in order)
    assert sorted(order) == [i for i, r in enumerate(rows) if r["concept_role"] != "component"]
