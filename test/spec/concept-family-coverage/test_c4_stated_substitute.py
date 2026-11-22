"""C4: a stated substitute answers, and the answer carries what it substitutes.

Oracle: the decision table's `stated substitute` rows (maintainer, 2026-11-07; code and
note as fixed in docs/specs/concept-family-coverage.md R1) and the filers' statements, where
the substitute is the filer's nearest presented line and no line for the family's own
measure exists.
"""

from __future__ import annotations

import pytest
from cfc_support import (
    CASES,
    CIK,
    G24,
    G25,
    IFRS25,
    NO_ENTRIES_CIK,
    lookup,
    row_of,
    store_rows,
    substitution_of,
)
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import candidate_order, family_member

DEP = ("depreciation_only", "Depreciation only; amortization is not included.")
DEP_IMP = ("depreciation_and_impairment_only",
           "Depreciation and impairment of property and equipment only; "
           "amortization is not included.")
IMP = ("includes_impairment", "Depreciation and amortization including impairment charges.")
ACC = ("includes_accretion", "Depreciation and amortization including accretion.")
COR = ("includes_cost_of_revenue", "Total costs and expenses, including cost of revenue.")
LTD = ("excludes_current_portion", "Long-term debt excluding the current portion.")
LTDL = ("excludes_current_portion",
        "Long-term debt and lease obligations excluding the current portion.")
COVER = ("cover_page_count",
         "Shares outstanding on the cover-page date of the annual report, "
         "not at the balance-sheet date.")
LINES = ("one_of_several_lines", "One of several gross debt lines; the filer reports no total.")
INV = ("includes_investment_gains",
       "Net investment income, which can include gains and losses besides interest.")
RES = ("includes_impairment_or_other",
       "Restructuring combined with impairment or other charges on one line.")
CONT = ("continuing_operations_only",
        "Continuing operations only; cash flows of discontinued operations are reported "
        "separately.")
APX = ("includes_accrued_liabilities",
       "Accounts payable together with accrued and other liabilities.")
GW = ("includes_goodwill", "Intangible assets including goodwill.")

# (decision, family, filer, element as tagged, (code, note))
SUBS = [
    ("D2", "DepreciationAndAmortization", "AVGO", G24 + "Depreciation", DEP),
    ("D2", "DepreciationAndAmortization", "TSM",
     "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full:DepreciationExpense", DEP),
    ("D2", "DepreciationAndAmortization", "GOOGL",
     "http://www.google.com/20221231:DepreciationAndImpairmentOnDispositionOfPropertyAndEquipment",
     DEP_IMP),
    ("D3", "DepreciationAndAmortization", "GOOGL", G25 + "Depreciation", DEP),
    ("D5", "DepreciationAndAmortization", "TSLA",
     "http://www.tesla.com/20251231:DepreciationAmortizationAndImpairment", IMP),
    ("D6", "DepreciationAndAmortization", "CEG", G25 + "DepreciationAmortizationAndAccretionNet",
     ACC),
    ("D6", "DepreciationAndAmortization", "WMT", G25 + "DepreciationAmortizationAndAccretionNet",
     ACC),
    ("D9", "OperatingExpenses", "GOOGL", G25 + "CostsAndExpenses", COR),
    ("D11", "Debt", "AAPL", G24 + "LongTermDebtNoncurrent", LTD),
    ("D11", "Debt", "KO", G25 + "LongTermDebtAndCapitalLeaseObligations", LTDL),
    ("D16", "SharesOutstanding", "KO",
     "http://xbrl.sec.gov/dei/2025:EntityCommonStockSharesOutstanding",
     COVER),
    ("D21", "DebtRepaid", "TSM", IFRS25 + "RepaymentsOfBondsNotesAndDebentures", LINES),
    ("D21", "DebtRepaid", "TSM", IFRS25 + "RepaymentsOfNoncurrentBorrowings", LINES),
    ("D21", "DebtRepaid", "VST", G24 + "RepaymentsOfLinesOfCredit", LINES),
    ("D21", "DebtRepaid", "VST",
     "http://www.vistraenergy.com/20221231:RepaymentsOfDebtShortTermInNature",
     LINES),
    ("D21", "DebtIssued", "TSM", IFRS25 + "ProceedsFromIssueOfBondsNotesAndDebentures", LINES),
    ("D25", "InterestIncome", "MU", G25 + "InvestmentIncomeNet", INV),
    ("D26", "RestructuringCharges", "LLY", G24 + "RestructuringSettlementAndImpairmentProvisions",
     RES),
    ("D26", "RestructuringCharges", "MU", G24 + "RestructuringCostsAndAssetImpairmentCharges", RES),
    ("D26", "RestructuringCharges", "TSLA",
     "http://www.tesla.com/20241231:RestructuringAndOtherExpenses",
     RES),
    ("D26", "RestructuringCharges", "TXN",
     "http://www.ti.com/20241231:RestructuringChargesAndOtherNet", RES),
    ("D26", "RestructuringCharges", "LMT", "ext:SeveranceAndRestructuringCosts", RES),
    ("D31", "CashFromOperations", "MRK",
     G24 + "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
     CONT),
    ("D31", "CashFromInvesting", "MRK",
     G24 + "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
     CONT),
    ("D31", "CashFromFinancing", "MRK",
     G24 + "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
     CONT),
    ("D33", "AccountsPayable", "CRM", G25 + "AccountsPayableAndOtherAccruedLiabilitiesCurrent",
     APX),
    ("D35", "IntangibleAssets", "TSM",
     "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full:IntangibleAssetsAndGoodwill",
     GW),
]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("decision,family,filer,element,sub", SUBS,
                         ids=[f"{d}-{f}-{t}-{e.rsplit(':', 1)[1][:30]}"
                              for d, f, t, e, _ in SUBS])
def test_substitutes_carry_their_code_and_note(decision, family, filer, element, sub):
    # oracle: R1's stated-substitute table, written from the decision table's reasons
    m = family_member(family, CIK[filer], element)
    assert m is not None
    assert m["role"] == "substitute"
    assert m["rank"] >= 50
    assert m["substitution"] == {"code": sub[0], "note": sub[1]}


@pytest.mark.criterion("C4")
def test_component_band_ranks_are_kept_for_the_new_substitutes():
    # oracle: R1 — only the role changes for these four (concept-rank-integrity and
    # family-measure-equivalence pin the ranks)
    want = [("DepreciationAndAmortization", "Depreciation", 50),
            ("OperatingExpenses", "CostsAndExpenses", 51),
            ("Debt", "LongTermDebtNoncurrent", 53),
            ("Debt", "LongTermDebtAndCapitalLeaseObligations", 54)]
    for family, local, rank in want:
        m = family_member(family, NO_ENTRIES_CIK, "us-gaap:" + local)
        assert (m["rank"], m["role"]) == (rank, "substitute")


# (case, family, expected fact id, value, (code, note), the filer's line)
SUB_CASES = [
    ("avgo_fy2025_da", "DepreciationAndAmortization", 27161, 574_000_000, DEP, "Depreciation"),
    ("googl_fy2025_da", "DepreciationAndAmortization", 34530, 21_136_000_000, DEP,
     "Depreciation of property and equipment"),
    ("intc_fy2025_da", "DepreciationAndAmortization", 157040, 10_757_000_000, DEP, "Depreciation"),
    ("abt_fy2025_da", "DepreciationAndAmortization", 240860, 1_434_000_000, DEP, "Depreciation"),
    ("tsm_fy2024_da", "DepreciationAndAmortization", 90124, 653_610_500_000, DEP,
     "Depreciation expense (TWD)"),
    ("mrk_fy2025_da", "DepreciationAndAmortization", 232104, 3_045_000_000, DEP, "Depreciation"),
    ("googl_fy2022_da", "DepreciationAndAmortization", 29566, 15_287_000_000, DEP_IMP,
     "Depreciation and impairment of property and equipment"),
    ("tsla_fy2025_da", "DepreciationAndAmortization", 113156, 6_148_000_000, IMP,
     "Depreciation, amortization and impairment"),
    ("ceg_fy2025_da", "DepreciationAndAmortization", 128772, 2_601_000_000, ACC,
     "Depreciation, amortization, and accretion, including nuclear fuel ..."),
    ("wmt_fy2026_da", "DepreciationAndAmortization", 261492, 14_203_000_000, ACC,
     "Depreciation and amortization"),
    ("googl_fy2025_opex", "OperatingExpenses", 34390, 273_797_000_000, COR,
     "Total costs and expenses"),
    ("amzn_fy2025_opex", "OperatingExpenses", 9621, 636_949_000_000, COR,
     "Total operating expenses"),
    ("ups_fy2025_opex", "OperatingExpenses", 257475, 80_794_000_000, COR,
     "Total Operating Expenses"),
    ("pg_fy2026_debt", "Debt", 264978, 22_842_000_000, LTD, "Long-term debt"),
    ("nflx_fy2025_debt", "Debt", 188127, 13_463_971_000, LTD, "Long-term debt"),
    ("aapl_fy2022_debt", "Debt", 11829, 98_959_000_000, LTD, "Term debt (non-current)"),
    ("cost_fy2026_debt", "Debt", 267902, 3_914_000_000, LTD,
     "Long-term debt, excluding current portion"),
    ("ko_fy2025_debt", "Debt", 196440, 42_119_000_000, LTDL, "Long-term debt"),
    ("ko_fy2025_shares", "SharesOutstanding", 195335, 4_300_723_069, COVER, "10-K cover page"),
    ("mcd_fy2025_shares", "SharesOutstanding", 217548, 710_398_642, COVER, "10-K cover page"),
    ("txn_fy2025_shares", "SharesOutstanding", 183616, 907_550_774, COVER, "10-K cover page"),
    ("tsm_fy2025_debtflow", "DebtRepaid", 91666, 54_310_000_000, LINES, "Repayment of bonds (TWD)"),
    ("tsm_fy2025_debtflow", "DebtIssued", 91662, 86_900_000_000, LINES,
     "Proceeds from issuance of bonds (TWD)"),
    ("mu_fy2026_intinc", "InterestIncome", 268437, 1_084_000_000, INV, "Interest income"),
    ("ups_fy2023_intinc", "InterestIncome", 251111, 217_000_000, INV,
     "Investment income and other"),
    ("lly_fy2024_restr", "RestructuringCharges", 221861, 860_600_000, RES,
     "Asset impairment, restructuring, and other special charges"),
    ("tsla_fy2024_restr", "RestructuringCharges", 111286, 684_000_000, RES,
     "Restructuring and other"),
    ("txn_fy2024_restr", "RestructuringCharges", 183473, -124_000_000, RES,
     "Restructuring charges/other"),
    ("mrk_fy2023_cashflow", "CashFromOperations", 225343, 13_006_000_000, CONT,
     "Net Cash Provided by Operating Activities of Continuing Operations"),
    ("mrk_fy2023_cashflow", "CashFromInvesting", 225376, -14_083_000_000, CONT,
     "Net Cash Used in Investing Activities of Continuing Operations"),
    ("mrk_fy2023_cashflow", "CashFromFinancing", 225403, -4_810_000_000, CONT,
     "Net Cash Used in Financing Activities of Continuing Operations"),
    ("crm_fy2026_ap", "AccountsPayable", 261689, 8_253_000_000, APX,
     "Accounts payable, accrued expenses and other liabilities"),
    ("tsm_fy2024_intang", "IntangibleAssets", 88688, 26_282_500_000, GW, "Intangible assets (TWD)"),
]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("case_id,family,fact_id,value,sub,label", SUB_CASES,
                         ids=[f"{c}-{f}" for c, f, *_ in SUB_CASES])
def test_substitute_answers_and_states_it(case_id, family, fact_id, value, sub, label):
    # oracle: the filer's statement line `label` and its filed value; the substitution is
    # R1's code and note for the element on that line
    r = lookup(case_id, family)
    assert r.status.value == "OK", r.resolution_notes
    assert r.citation.fact_id == fact_id
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded
    want = {"code": sub[0], "note": sub[1], "concept": row_of(case_id, fact_id)["concept"]}
    assert substitution_of(r) == want
    assert r.to_dict().get("substitution") == want


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("case_id,family,fact_id,value", [
    ("unp_fy2025_shares", "SharesOutstanding", 243930, 593_245_884),
    ("msft_fy2025_opex", "OperatingExpenses", 50498, 65_365_000_000),
])
def test_a_primary_line_answers_without_substitution(case_id, family, fact_id, value):
    # oracle: UNP's balance sheet "shares outstanding" 593,245,884 at 2025-12-31 (the cover
    # count 593,391,460 is dated 2026-01-30; shares-outstanding-reading); MSFT FY2025 total
    # operating expenses 65,365 ($M), tagged OperatingExpenses
    assert family_member(family, CASES[case_id]["cik"],
                         row_of(case_id, fact_id)["concept"])["role"] == "primary"
    r = lookup(case_id, family)
    assert r.status.value == "OK", r.resolution_notes
    assert r.citation.fact_id == fact_id
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded
    assert substitution_of(r) is None


PRIMARY_OF = {
    "DepreciationAndAmortization": G25 + "DepreciationDepletionAndAmortization",
    "OperatingExpenses": G25 + "OperatingExpenses",
    "Debt": G25 + "LongTermDebt",
    "SharesOutstanding": G25 + "CommonStockSharesOutstanding",
    "InterestIncome": G25 + "InvestmentIncomeInterest",
    "RestructuringCharges": G25 + "RestructuringCharges",
    "CashFromOperations": G25 + "NetCashProvidedByUsedInOperatingActivities",
    "AccountsPayable": G25 + "AccountsPayableCurrent",
}
WITH_PRIMARY = [c for c in SUB_CASES if c[1] in PRIMARY_OF and c[0].split("_")[0] != "tsm"]


@pytest.mark.criterion("C4")
@given(st.sampled_from(WITH_PRIMARY), st.data())
def test_any_primary_row_displaces_the_substitute(case, data):
    # oracle: R2 rule 1 — a primary of the family beats a substitute at any period, and
    # then there is nothing to state
    case_id, family, fact_id, _, _, _ = case
    rows = store_rows(case_id, family)
    periods = sorted({r["period_end"] for r in rows})
    m = family_member(family, CASES[case_id]["cik"], PRIMARY_OF[family])
    assert m["role"] == "primary"
    added = {**row_of_store(rows, fact_id), "fact_id": 990_001, "concept": PRIMARY_OF[family],
             "concept_pref_rank": m["rank"], "concept_role": "primary",
             "substitution_code": None, "substitution_note": None,
             "value": data.draw(st.integers(min_value=1, max_value=10**12)),
             "period_end": data.draw(st.sampled_from(periods)),
             "presented": data.draw(st.booleans())}
    both = [*rows, added]
    assert both[candidate_order(both)[0]]["fact_id"] == 990_001
    r = lookup(case_id, family, both)
    assert r.citation.fact_id == 990_001
    assert substitution_of(r) is None


def row_of_store(rows, fact_id):
    """The store row with this fact id."""
    return next(r for r in rows if r["fact_id"] == fact_id)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.sampled_from(SUB_CASES), st.randoms(use_true_random=False))
def test_substitution_follows_the_answer_role(case, rnd):
    # oracle: R3 — the substitution is set exactly when the answer's role is `substitute`,
    # and comes from the answer row, whatever the row order
    case_id, family, fact_id, _, sub, _ = case
    rows = store_rows(case_id, family)
    rnd.shuffle(rows)
    r = lookup(case_id, family, rows)
    winner = row_of_store(rows, r.citation.fact_id)
    assert winner["concept_role"] == "substitute"
    assert substitution_of(r) == {"code": winner["substitution_code"],
                                  "note": winner["substitution_note"],
                                  "concept": winner["concept"]}
    assert substitution_of(r)["code"] == sub[0]
