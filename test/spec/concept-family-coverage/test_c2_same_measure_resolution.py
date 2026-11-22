"""C2: on real filings, a same-measure line answers, OK, with no substitution.

Oracle for every expected value: the filer's own statement line in the cited filing (label
in the comment, value as filed; the fixture's `companyfacts` field records SEC agreement
where companyfacts has the fact). Expected fact ids follow from R2 by hand: the filer's
line at the current period, presented first, then rank, finer `decimals`, larger fact id.
"""

from __future__ import annotations

import pytest
from cfc_support import CASES, lookup, row_of, store_rows, substitution_of
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import candidate_order

# (case, family, expected fact id, expected value, the filer's line)
SAME_CASES = [
    ("nvda_fy2025_interest", "InterestExpense", 64369, 247_000_000, "Interest expense"),
    ("nvda_fy2024_interest", "InterestExpense", 62956, 257_000_000, "Interest expense (control)"),
    ("cost_fy2025_interest", "InterestExpense", 209798, 154_000_000, "Interest expense"),
    ("wmt_fy2025_interest", "InterestExpense", 206434, 2_249_000_000, "Interest: Debt"),
    ("amzn_fy2025_interest", "InterestExpense", 8392, 2_274_000_000, "Interest expense"),
    ("hd_fy2025_interest", "InterestExpense", 213856, 2_412_000_000, "Interest expense"),
    ("ceg_fy2024_interest", "InterestExpense", 122964, 506_000_000, "Interest expense, net"),
    ("msft_fy2026_da", "DepreciationAndAmortization", 104199, 38_534_000_000,
     "Depreciation, amortization, and other"),
    ("amd_fy2022_da", "DepreciationAndAmortization", 16534, 4_174_000_000,
     "Depreciation and amortization"),
    ("amd_fy2024_da", "DepreciationAndAmortization", 19325, 671_000_000,
     "Depreciation and amortization (acquisition amortization on its own line)"),
    ("amd_fy2025_da", "DepreciationAndAmortization", 114785, 750_000_000,
     "Depreciation and amortization"),
    ("mcd_fy2025_da", "DepreciationAndAmortization", 217763, 2_199_000_000,
     "Depreciation and amortization (reviewed ranking)"),
    ("tsm_fy2023_nonop", "OtherNonOperatingIncome", 83329, 57_850_900_000,
     "Total non-operating income and expenses (TWD)"),
    ("tsm_fy2025_nonop", "OtherNonOperatingIncome", 91007, 105_563_000_000,
     "Total non-operating income and expenses (TWD)"),
    ("orcl_fy2022_nonop", "OtherNonOperatingIncome", 67468, -522_000_000,
     "Non-operating (expenses) income, net"),
    ("orcl_fy2025_nonop", "OtherNonOperatingIncome", 73611, 60_000_000,
     "Non-operating income (expenses), net"),
    ("cost_fy2026_nonop", "OtherNonOperatingIncome", 267476, 711_000_000,
     "Interest income and other, net"),
    ("msft_fy2024_debtflow", "DebtIssued", 47150, 24_395_000_000,
     "Proceeds from issuance of debt (maturities longer than 90 days)"),
    ("msft_fy2024_debtflow", "DebtRepaid", 47153, 29_070_000_000, "Repayments of debt"),
    ("msft_fy2023_debtflow", "DebtRepaid", 45312, 2_750_000_000, "Repayments of debt"),
    ("csco_fy2024_debtflow", "DebtRepaid", 163621, 9_826_000_000, "Repayments of debt"),
    ("csco_fy2024_debtflow", "DebtIssued", 163618, 31_818_000_000,
     "Issuances of senior fixed-rate notes"),
    ("tsla_fy2025_debtflow", "DebtRepaid", 113219, 5_546_000_000, "Repayments of debt"),
    ("tsla_fy2025_debtflow", "DebtIssued", 113216, 5_586_000_000,
     "Proceeds from issuances of debt"),
    ("tsla_fy2022_debtflow", "DebtRepaid", 75274, 3_364_000_000,
     "Repayments of convertible and other debt"),
    ("mrk_fy2025_debtflow", "DebtIssued", 232191, 13_880_000_000, "Proceeds from issuance of debt"),
    ("ko_fy2024_debtflow", "DebtIssued", 192776, 12_061_000_000,
     "Issuances of loans, notes payable and long-term debt"),
    ("ko_fy2024_debtflow", "DebtRepaid", 192779, 9_533_000_000,
     "Payments of loans, notes payable and long-term debt"),
    ("abt_fy2023_debtflow", "DebtRepaid", 235475, 2_498_000_000,
     "Repayments of long-term debt and debt with maturities over 3 months"),
    ("googl_fy2022_debtflow", "DebtRepaid", 29641, 54_068_000_000, "Repayments of debt"),
    ("hd_fy2024_debtflow", "DebtIssued", 212235, 10_010_000_000,
     "Proceeds from long-term debt, net of discounts"),
    ("hd_fy2024_debtflow", "DebtRepaid", 212238, 1_536_000_000, "Repayments of long-term debt"),
    ("csco_fy2025_intinc", "InterestIncome", 165814, 1_001_000_000, "Interest income"),
    ("hd_fy2024_intinc", "InterestIncome", 212382, 201_000_000, "Interest income and other, net"),
    ("orcl_fy2025_pretax", "PretaxIncome", 73614, 14_160_000_000, "Income before income taxes"),
    ("mcd_fy2025_pretax", "PretaxIncome", 218499, 10_897_000_000,
     "Income before provision for income taxes"),
    ("pg_fy2023_pretax", "PretaxIncome", 198685, 18_353_000_000,
     "Earnings from continuing operations before income taxes"),
    ("orcl_fy2025_services", "ServiceRevenue", 71905, 5_233_000_000, "Services"),
    ("orcl_fy2025_services", "Revenue", 73627, 57_399_000_000, "Total revenues (control)"),
    ("qcom_fy2023_discops", "IncomeFromDiscontinuedOperations", 176773, -107_000_000,
     "Discontinued operations, net of income taxes"),
    ("amzn_fy2025_sm", "SellingAndMarketing", 8374, 47_129_000_000, "Sales and marketing"),
    ("tsm_fy2024_ar", "AccountsReceivable", 88192, 270_683_200_000,
     "Notes and accounts receivable, net (TWD)"),
    ("tsm_fy2025_rev", "Revenue", 93234, 3_809_054_300_000, "Net revenue (TWD)"),
    ("tsm_fy2025_rev", "InterestIncome", 93306, 105_739_100_000, "Interest income (TWD)"),
    ("ko_fy2024_sti", "ShortTermInvestments", 192644, 2_020_000_000, "Short-term investments"),
    ("nvda_fy2026_sti", "ShortTermInvestments", 148, 51_951_000_000, "Marketable securities"),
    ("nvda_fy2025_sti", "ShortTermInvestments", 64428, 34_621_000_000,
     "Marketable securities (control)"),
    ("nvda_fy2023_capex", "Capex", 61816, 1_833_000_000,
     "Purchases related to property and equipment and intangible assets"),
    ("nvda_fy2024_capex", "Capex", 63228, 1_069_000_000,
     "Purchases related to property and equipment and intangible assets (control)"),
]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("case_id,family,fact_id,value,label", SAME_CASES,
                         ids=[f"{c}-{f}" for c, f, *_ in SAME_CASES])
def test_same_measure_line_answers(case_id, family, fact_id, value, label):
    # oracle: the filer's statement line `label` and its filed value (comment table above);
    # fact ids by R2 applied by hand to the fixture rows
    r = lookup(case_id, family)
    assert r.status.value == "OK", r.resolution_notes
    assert r.citation.fact_id == fact_id
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded
    assert substitution_of(r) is None


@pytest.mark.criterion("C2")
def test_retailer_interest_expense_fy2025_ranks_all_four():
    # oracle: the four FY2025 income statements — HD 2,412, AMZN 2,274, WMT (debt) 2,249,
    # COST 154 ($M); before D1 only COST and WMT resolved
    picks = {}
    for case_id in ("cost_fy2025_interest", "wmt_fy2025_interest", "amzn_fy2025_interest",
                    "hd_fy2025_interest"):
        rows = store_rows(case_id, "InterestExpense")
        order = candidate_order(rows)
        picks[CASES[case_id]["ticker"]] = rows[order[0]]["value"]
    ranked = sorted(picks, key=lambda t: -picks[t])
    assert ranked == ["HD", "AMZN", "WMT", "COST"]
    assert picks["AMZN"] == 2_274_000_000
    assert picks["HD"] == 2_412_000_000


@pytest.mark.criterion("C2")
def test_amd_year_switch_keeps_the_line_not_the_amortization():
    # oracle: AMD cash-flow statements — "Depreciation and amortization" 4,174 (FY2022), then
    # 671 and 750 once acquisition amortization (2,393; 2,254) got its own line ($M)
    values = [lookup(c, "DepreciationAndAmortization").value
              for c in ("amd_fy2022_da", "amd_fy2024_da", "amd_fy2025_da")]
    assert values == [4_174_000_000, 671_000_000, 750_000_000]
    assert row_of("amd_fy2024_da", 19328)["value"] == 2_393_000_000  # the D8 line, not taken


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.sampled_from(SAME_CASES), st.randoms(use_true_random=False))
def test_row_order_never_changes_the_answer(case, rnd):
    # oracle: metamorphic — R3 decides on the rows, not on the order the SQL returned them
    case_id, family, fact_id, value, _ = case
    rows = store_rows(case_id, family)
    rnd.shuffle(rows)
    r = lookup(case_id, family, rows)
    assert r.status.value == "OK"
    assert r.citation.fact_id == fact_id
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded
