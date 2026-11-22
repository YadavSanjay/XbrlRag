"""C1: same-measure elements are members of their family, at the decided ranks.

Oracle: the decision table of docs/design/XbrlRag_Concept_Decisions.md (maintainer,
2026-11-07), and the filers' presentation linkbases, where each element is the filer's own
line (concepts below are written in the fact store's namespace form, as tagged in the cited
filing).
"""

from __future__ import annotations

import pytest
from cfc_support import CIK, G24, G25, IFRS25, NO_ENTRIES_CIK
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import family_member

MSFT_EXT = "http://www.microsoft.com/20260630:DepreciationAmortizationAndOther"
ORCL_NONOP = ("http://www.oracle.com/20220531:NonoperatingIncomeExpenseIncludingElimination"
              "OfNetIncomeLossAttributableToNoncontrollingInterests")
ORCL_PRETAX = ("http://www.oracle.com/20250531:IncomeLossFromContinuingOperationsIncluding"
               "NoncontrollingInterestBeforeIncomeTaxesExtraordinaryItems")

# (decision, family, filer, concept) for every same-measure element; "*" = any filer.
SAME = [
    ("D1", "InterestExpense", "NVDA", G24 + "InterestExpenseNonoperating"),
    ("D4", "DepreciationAndAmortization", "MSFT", MSFT_EXT),
    ("D7", "DepreciationAndAmortization", "AMD", G25 + "OtherDepreciationAndAmortization"),
    ("D13", "OtherNonOperatingIncome", "GOOGL", G25 + "NonoperatingIncomeExpense"),
    ("D14", "OtherNonOperatingIncome", "ORCL", ORCL_NONOP),
    ("D14", "OtherNonOperatingIncome", "TSM",
     "http://www.tsmc.com/20251231:NonOperatingIncomeExpenseNet"),
    ("D14", "OtherNonOperatingIncome", "TSM",
     "http://www.tsmc.com/20231231:NonOperatingIncomeExpense1"),
    ("D14", "OtherNonOperatingIncome", "COST", G25 + "InterestAndOtherIncome"),
    ("D17", "DebtIssued", "MRK", G25 + "ProceedsFromIssuanceOfDebt"),
    ("D17", "DebtIssued", "HD", G24 + "ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet"),
    ("D18", "DebtRepaid", "HD", G24 + "RepaymentsOfLongTermDebtAndCapitalSecurities"),
    ("D18", "DebtRepaid", "CSCO", G24 + "RepaymentsOfOtherLongTermDebt"),
    ("D18", "DebtRepaid", "KO", G24 + "RepaymentsOfDebtAndCapitalLeaseObligations"),
    ("D19", "DebtRepaid", "TSLA", G25 + "RepaymentsOfConvertibleDebt"),
    ("D20", "DebtRepaid", "MSFT", G24 + "RepaymentsOfDebtMaturingInMoreThanThreeMonths"),
    ("D20", "DebtIssued", "MSFT", G24 + "ProceedsFromDebtMaturingInMoreThanThreeMonths"),
    ("D20", "DebtRepaid", "ABT",
     "http://www.abbott.com/20231231:RepaymentsOfLongTermDebtAndDebtMaturingInMoreThanThreeMonths"),
    ("D20", "DebtIssued", "ABT",
     "http://www.abbott.com/20231231:ProceedsFromLongTermDebtAndDebtMaturingInMoreThanThreeMonths"),
    ("D22", "DebtIssued", "CRM", G24 + "ProceedsFromIssuanceOfMediumTermNotes"),
    ("D22", "DebtRepaid", "QCOM", G24 + "RepaymentsOfAssumedDebt"),
    ("D22", "DebtRepaid", "TSLA",
     "http://www.tesla.com/20221231:RepaymentsOfConvertibleAndOtherDebt"),
    ("D24", "InterestIncome", "CSCO", G25 + "InvestmentIncomeInterestAndDividend"),
    ("D27", "PretaxIncome", "ORCL", ORCL_PRETAX),
    ("D27", "PretaxIncome", "MCD",
     "http://www.mcdonalds.com/20251231:IncomeLossFromContinuingOperationsBeforeIncomeTaxes"),
    ("D27", "PretaxIncome", "PG",
     "http://www.pg.com/20230630:IncomeLossFromContinuingOperationsBeforeIncomeTaxes"),
    ("D28", "ServiceRevenue", "ORCL", "http://www.oracle.com/20250531:SalesRevenueServicesNet1"),
    ("D29", "IncomeFromDiscontinuedOperations", "QCOM",
     G24 + "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"),
    ("D32", "SellingAndMarketing", "AMZN", G25 + "MarketingExpense"),
    ("D34", "ShareBasedCompensation", "TSM", IFRS25 + "AdjustmentsForSharebasedPayments"),
    ("D34", "AccountsReceivable", "TSM", IFRS25 + "CurrentTradeReceivables"),
    ("D34", "GeneralAndAdministrative", "TSM", IFRS25 + "GeneralAndAdministrativeExpense"),
    ("D34", "AccountsPayable", "TSM", IFRS25 + "TradeAndOtherCurrentPayablesToTradeSuppliers"),
    ("D34", "Revenue", "TSM", IFRS25 + "RevenueFromContractsWithCustomers"),
    ("D34", "InterestIncome", "TSM", IFRS25 + "RevenueFromInterest"),
    ("D39", "ShortTermInvestments", "KO", G24 + "OtherShortTermInvestments"),
    ("D39", "ShortTermInvestments", "UNP", G24 + "HeldToMaturitySecuritiesCurrent"),
    ("D39", "ShortTermInvestments", "NVDA",
     "http://www.nvidia.com/20260125:MarketableSecuritiesAndEquitySecuritiesFVNI"),
    ("D41", "Capex", "NVDA",
     "http://www.nvidia.com/20230129:PurchasesOfPropertyAndEquipmentAndIntangibleAssets"),
]

# Extension members are cik-scoped: (family, owner, local name).
EXTENSIONS = [
    ("DepreciationAndAmortization", "MSFT", "DepreciationAmortizationAndOther"),
    ("OtherNonOperatingIncome", "ORCL",
     "NonoperatingIncomeExpenseIncludingEliminationOfNetIncomeLossAttributableToNoncontrollingInterests"),
    ("OtherNonOperatingIncome", "TSM", "NonOperatingIncomeExpenseNet"),
    ("OtherNonOperatingIncome", "TSM", "NonOperatingIncomeExpense1"),
    ("DebtRepaid", "ABT", "RepaymentsOfLongTermDebtAndDebtMaturingInMoreThanThreeMonths"),
    ("DebtIssued", "ABT", "ProceedsFromLongTermDebtAndDebtMaturingInMoreThanThreeMonths"),
    ("DebtRepaid", "TSLA", "RepaymentsOfConvertibleAndOtherDebt"),
    ("PretaxIncome", "ORCL",
     "IncomeLossFromContinuingOperationsIncludingNoncontrollingInterestBeforeIncomeTaxesExtraordinaryItems"),
    ("PretaxIncome", "MCD", "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"),
    ("PretaxIncome", "PG", "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"),
    ("ServiceRevenue", "ORCL", "SalesRevenueServicesNet1"),
    ("ShortTermInvestments", "NVDA", "MarketableSecuritiesAndEquitySecuritiesFVNI"),
    ("Capex", "NVDA", "PurchasesOfPropertyAndEquipmentAndIntangibleAssets"),
]

# Standard same-measure elements, prefix form: (family, filer, prefix, local name).
STANDARD = [(f, t, None, c.rsplit(":", 1)[1]) for _, f, t, c in SAME if "fasb.org/us-gaap" in c]
# ... of which global (D7's element is AMD-only).
STANDARD_GLOBAL = [e for e in STANDARD if e[3] != "OtherDepreciationAndAmortization"]


def _eligible(rank):
    return rank < 50 or rank >= 100


def _rank(family, filer, concept):
    m = family_member(family, CIK[filer], concept)
    assert m is not None, (family, filer, concept)
    return m["rank"]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("decision,family,filer,concept", SAME,
                         ids=[f"{d}-{f}-{t}" for d, f, t, _ in SAME])
def test_same_measure_elements_are_primaries(decision, family, filer, concept):
    # oracle: the decision table's `same measure` rows; the element is the filer's
    # presented statement line in the cited filing (fixture `presented`)
    m = family_member(family, CIK[filer], concept)
    assert m is not None
    assert m["role"] == "primary"
    assert m["substitution"] is None
    assert _eligible(m["rank"])


@pytest.mark.criterion("C1")
def test_interest_expense_ranks_exactly():
    # oracle: D1 — "rank it just after InterestExpense so a filer tagging both keeps the
    # total"; the other two primaries keep their declared order behind it
    for filer in ("NVDA", "KO", "WMT"):
        assert _rank("InterestExpense", filer, G25 + "InterestExpense") == 1
        assert _rank("InterestExpense", filer, G25 + "InterestExpenseNonoperating") == 2
        assert _rank("InterestExpense", filer, G25 + "InterestAndDebtExpense") == 3
        assert _rank("InterestExpense", filer, G25 + "InterestExpenseDebt") == 4


@pytest.mark.criterion("C1")
def test_other_nonoperating_ranks_unchanged():
    # oracle: D13 — NonoperatingIncomeExpense stays in the family at rank 2; the presented-
    # line rule (C5), not a rank change, decides between the two
    for filer in ("GOOGL", "NVDA", NO_ENTRIES_CIK):
        cik = CIK.get(filer, filer)
        other = family_member("OtherNonOperatingIncome", cik,
            G25 + "OtherNonoperatingIncomeExpense")
        total = family_member("OtherNonOperatingIncome", cik, G25 + "NonoperatingIncomeExpense")
        assert (other["rank"], other["role"]) == (1, "primary")
        assert (total["rank"], total["role"]) == (2, "primary")


@pytest.mark.criterion("C1")
def test_debt_flow_orderings():
    # oracle: D18 (the lease-inclusive element "acceptable at a lower rank"), D19 ("rank
    # below D18"), D22 ("low rank") — every filer sees the generic order
    cik = NO_ENTRIES_CIK
    d18 = [family_member("DebtRepaid", cik, G24 + q)["rank"] for q in (
        "RepaymentsOfLongTermDebtAndCapitalSecurities", "RepaymentsOfOtherLongTermDebt")]
    lease = family_member("DebtRepaid", cik,
        G24 + "RepaymentsOfDebtAndCapitalLeaseObligations")["rank"]
    conv = family_member("DebtRepaid", cik, G24 + "RepaymentsOfConvertibleDebt")["rank"]
    assert max(d18) < lease < conv < 50
    repaid_globals = ["RepaymentsOfLongTermDebt", "RepaymentsOfDebt",
                      "RepaymentsOfLongTermLinesOfCredit", "RepaymentsOfNotesPayable",
                      "RepaymentsOfLongTermDebtAndCapitalSecurities",
                      "RepaymentsOfOtherLongTermDebt",
                      "RepaymentsOfDebtAndCapitalLeaseObligations",
                      "RepaymentsOfConvertibleDebt",
                      "RepaymentsOfDebtMaturingInMoreThanThreeMonths"]
    assumed = family_member("DebtRepaid", cik, G24 + "RepaymentsOfAssumedDebt")["rank"]
    assert all(family_member("DebtRepaid", cik, G24 + q)["rank"] < assumed for q in repaid_globals)
    tsla = family_member("DebtRepaid", CIK["TSLA"],
                         "http://www.tesla.com/20221231:RepaymentsOfConvertibleAndOtherDebt")["rank"]
    assert all(family_member("DebtRepaid", CIK["TSLA"], G24 + q)["rank"] < tsla < 50
               for q in repaid_globals)
    issued_globals = ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromLongTermLinesOfCredit",
                      "ProceedsFromDebtNetOfIssuanceCosts", "ProceedsFromIssuanceOfDebt",
                      "ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet",
                      "ProceedsFromDebtMaturingInMoreThanThreeMonths"]
    mtn = family_member("DebtIssued", cik, G24 + "ProceedsFromIssuanceOfMediumTermNotes")["rank"]
    assert all(family_member("DebtIssued", cik, G24 + q)["rank"] < mtn < 50 for q in issued_globals)


@pytest.mark.criterion("C1")
def test_bogus_us_gaap_element_is_gone_and_msft_extension_is_scoped():
    # oracle: D4 — `us-gaap:DepreciationAmortizationAndOther` does not exist in US-GAAP; the
    # MSFT line is its extension; CSCO's same-named extension is a soft gap, not decided
    for filer in ("MSFT", "MCD", "CSCO", "NVDA"):
        assert family_member("DepreciationAndAmortization", CIK[filer],
                             "us-gaap:DepreciationAmortizationAndOther") is None
        assert family_member("DepreciationAndAmortization", CIK[filer],
                             G25 + "DepreciationAmortizationAndOther") is None
    assert family_member("DepreciationAndAmortization", CIK["MSFT"], MSFT_EXT)["role"] == "primary"
    assert family_member("DepreciationAndAmortization", CIK["CSCO"],
                         "http://www.cisco.com/20250726:DepreciationAmortizationAndOther") is None
    anchor = family_member("DepreciationAndAmortization", CIK["MSFT"],
                           G25 + "DepreciationDepletionAndAmortization")
    assert (anchor["rank"], anchor["role"]) == (1, "primary")


@pytest.mark.criterion("C1")
def test_oracle_services_line_ranks_first_for_oracle():
    # oracle: D28 — ORCL's "Services" line; the ServiceRevenue family's revenue-total
    # primaries must not answer an undimensioned services lookup for ORCL
    ext = family_member("ServiceRevenue", CIK["ORCL"],
                        "http://www.oracle.com/20250531:SalesRevenueServicesNet1")
    assert (ext["rank"], ext["role"]) == (1, "primary")
    total = family_member("ServiceRevenue", CIK["ORCL"],
                          G24 + "RevenueFromContractWithCustomerExcludingAssessedTax")
    assert total["rank"] > 1


@pytest.mark.criterion("C1")
def test_amd_other_d_and_a_is_amd_only():
    # oracle: D7 — `same measure (cik-scoped)`: "globally the element means other D&A"
    q = G25 + "OtherDepreciationAndAmortization"
    assert family_member("DepreciationAndAmortization", CIK["AMD"], q)["role"] == "primary"
    for filer in ("MSFT", "NVDA", "INTC", "AVGO"):
        assert family_member("DepreciationAndAmortization", CIK[filer], q) is None


namespace_years = st.sampled_from(["2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"])


@pytest.mark.criterion("C1")
@given(st.sampled_from(STANDARD), namespace_years)
def test_any_dated_us_gaap_namespace_gives_the_prefix_answer(entry, year):
    # oracle: R1 — the namespace version never matters for a standard taxonomy
    family, filer, _, local = entry
    prefix = family_member(family, CIK[filer], "us-gaap:" + local)
    assert prefix is not None and prefix["role"] == "primary"
    assert family_member(family, CIK[filer], f"http://fasb.org/us-gaap/{year}:{local}") == prefix


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.sampled_from([
    ("ShareBasedCompensation", "AdjustmentsForSharebasedPayments"),
    ("AccountsReceivable", "CurrentTradeReceivables"),
    ("Revenue", "RevenueFromContractsWithCustomers"),
    ("InterestIncome", "RevenueFromInterest")]),
    st.sampled_from(["http", "https"]),
    st.sampled_from(["2022-03-24", "2023-03-23", "2024-03-27", "2025-03-27"]))
def test_any_ifrs_namespace_gives_the_prefix_answer(entry, scheme, date):
    # oracle: R1 — http and https, and every dated IFRS taxonomy, are the same `ifrs-full`
    family, local = entry
    prefix = family_member(family, CIK["TSM"], "ifrs-full:" + local)
    assert prefix is not None and prefix["role"] == "primary"
    uri = f"{scheme}://xbrl.ifrs.org/taxonomy/{date}/ifrs-full:{local}"
    assert family_member(family, CIK["TSM"], uri) == prefix


ciks = st.from_regex(r"[0-9]{10}", fullmatch=True)
ext_namespaces = st.from_regex(r"http://www\.[a-z]{3,12}\.com/20[0-9]{6}", fullmatch=True)


@pytest.mark.criterion("C1")
@given(st.sampled_from(EXTENSIONS), ext_namespaces, ciks)
def test_extension_members_are_scoped_to_their_filer(entry, namespace, other):
    # oracle: R1 — a cik-scoped extension matches its filer under any extension namespace
    # (filers re-date it every year) and never another filer
    family, owner, local = entry
    mine = family_member(family, CIK[owner], f"{namespace}:{local}")
    assert mine is not None and mine["role"] == "primary"
    assert family_member(family, CIK[owner], f"ext:{local}") == mine
    if other != CIK[owner]:
        assert family_member(family, other, f"{namespace}:{local}") is None


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.sampled_from(STANDARD_GLOBAL + [
    ("InterestIncome", "TSM", None, "InvestmentIncomeInterest"),
    ("InterestExpense", "NVDA", None, "InterestExpense")]),
       ext_namespaces)
def test_standard_local_name_in_an_extension_namespace_is_not_the_element(entry, namespace):
    # oracle: R1 — an extension named like a us-gaap element (TSMC's
    # `InvestmentIncomeInterest`, FY2022-23 20-Fs) is not that element; only a cik-scoped
    # entry makes an extension a member, and only for its filer (TSMC's is, since
    # 2026-11-07: C8)
    family, filer, _, local = entry
    assert family_member(family, NO_ENTRIES_CIK, "us-gaap:" + local)["role"] == "primary"
    assert family_member(family, NO_ENTRIES_CIK, f"{namespace}:{local}") is None
    assert family_member(family, CIK["AMD"], f"http://www.amd.com/20251227:{local}") is None
    assert family_member(family, CIK["NVDA"], f"{namespace}:{local}") is None
