"""C8: the two same-measure additions decided on 2026-11-07.

1. TSMC's interest-income extension `InvestmentIncomeInterest` (FY2022-23 20-Fs) is a
   cik-scoped member of InterestIncome, ranked after `ifrs-full:RevenueFromInterest`, which
   TSMC tags with the same value in every year.
2. `us-gaap:DebtLongtermAndShorttermCombinedAmount` (total debt, short- and long-term) is a
   Debt primary ranked right after `us-gaap:LongTermDebt`.

Oracle: the maintainer's decision (2026-11-07); the filers' own facts in the cited 10-K /
20-F (fixture rows, accession per case) and SEC companyfacts, which matches every us-gaap
and ifrs fact used here where the filer is cached (fixture `companyfacts`; QCOM is not
cached).
"""

from __future__ import annotations

import pytest
from cfc_support import CASES, CIK, G24, G25, NO_ENTRIES_CIK, lookup, store_rows, substitution_of
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.family_resolution import candidate_order, family_member

TSMC_INTEREST = "http://www.tsmc.com/20231231:InvestmentIncomeInterest"
IFRS_INTEREST = "https://xbrl.ifrs.org/taxonomy/2023-03-23/ifrs-full:RevenueFromInterest"
COMBINED = G25 + "DebtLongtermAndShorttermCombinedAmount"


@pytest.mark.criterion("C8")
def test_tsmc_interest_extension_is_tsmc_only_and_ranks_after_the_ifrs_element():
    # oracle: decision 2026-11-07 — same measure, cik-scoped, never displacing
    # `ifrs-full:RevenueFromInterest` (D34) where TSMC tags both
    ext = family_member("InterestIncome", CIK["TSM"], TSMC_INTEREST)
    ifrs = family_member("InterestIncome", CIK["TSM"], IFRS_INTEREST)
    assert ext is not None and ext["role"] == "primary" and ext["substitution"] is None
    assert ifrs is not None and ifrs["role"] == "primary"
    assert ext["rank"] > ifrs["rank"]
    assert ext["rank"] < 50 or ext["rank"] >= 100
    for filer in ("NVDA", "INTC", "META"):
        assert family_member("InterestIncome", CIK[filer],
                             f"http://www.example{filer.lower()}.com/20231231:"
                             "InvestmentIncomeInterest") is None


@pytest.mark.criterion("C8")
def test_combined_debt_is_a_primary_right_after_long_term_debt():
    # oracle: decision 2026-11-07 — same measure as total debt; below LongTermDebt so a
    # filer's long-term-debt total keeps answering where it exists
    generic = family_member("Debt", NO_ENTRIES_CIK,
                            "us-gaap:DebtLongtermAndShorttermCombinedAmount")
    assert (generic["rank"], generic["role"], generic["substitution"]) == (2, "primary", None)
    for filer in ("LLY", "AMD", "AVGO", "MRK", "ORCL", "QCOM"):
        ltd = family_member("Debt", CIK[filer], G24 + "LongTermDebt")
        comb = family_member("Debt", CIK[filer], COMBINED)
        assert comb["role"] == "primary"
        assert ltd["rank"] < comb["rank"] < 50


# (case, family, expected fact id, value, what the filer tags)
CASES_C8 = [
    ("tsm_fy2022_intinc", "InterestIncome", 82026, 22_422_200_000,
     "RevenueFromInterest and the extension, equal (TWD); the IFRS element answers"),
    ("tsm_fy2023_intinc", "InterestIncome", 85795, 60_293_900_000,
     "RevenueFromInterest and the extension, equal (TWD)"),
    ("tsm_fy2024_intinc", "InterestIncome", 89673, 87_213_400_000, "RevenueFromInterest only"),
    ("lly_fy2025_totaldebt", "Debt", 224243, 42_503_000_000,
     "total debt 42,503 in a note; balance sheet: current 1,635 + long-term 40,868"),
    ("lly_fy2024_totaldebt", "Debt", 222477, 29_474_000_000,
     "LongTermDebt 29,474.0 (rank 1) beats total debt 33,644.2"),
    ("lly_fy2023_totaldebt", "Debt", 220237, 19_104_600_000,
     "LongTermDebt 19,104.6 (rank 1) beats total debt 25,225.3"),
    ("amd_fy2022_totaldebt", "Debt", 17029, 2_467_000_000, "total debt; was noncurrent 2,467"),
    ("amd_fy2023_totaldebt", "Debt", 18426, 2_468_000_000,
     "total debt = noncurrent 1,717 + short-term 751"),
    ("amd_fy2024_totaldebt", "Debt", 19816, 1_721_000_000, "total debt"),
    ("amd_fy2025_totaldebt", "Debt", 115444, 3_222_000_000,
     "total debt = noncurrent 2,348 + current 874"),
    ("avgo_fy2025_totaldebt", "Debt", 27848, 67_120_000_000,
     "total debt; was noncurrent debt and leases 61,984"),
    ("mrk_fy2023_totaldebt", "Debt", 226224, 35_100_000_000, "total debt, tagged at -8"),
    ("mrk_fy2024_totaldebt", "Debt", 229665, 37_100_000_000, "total debt, tagged at -8"),
    ("mrk_fy2025_totaldebt", "Debt", 233065, 49_300_000_000, "total debt, tagged at -8"),
    ("orcl_fy2023_totaldebt", "Debt", 68774, 90_481_000_000,
     "total borrowings; before, only the current part existed"),
    ("orcl_fy2024_totaldebt", "Debt", 70792, 86_869_000_000, "total borrowings"),
    ("orcl_fy2025_totaldebt", "Debt", 72881, 92_568_000_000, "total borrowings"),
    ("orcl_fy2026_totaldebt", "Debt", 108243, 129_541_000_000, "total borrowings"),
    ("qcom_fy2023_totaldebt", "Debt", 176697, 14_484_000_000,
     "LongTermDebt presented on the balance sheet beats total debt 15,398 (note)"),
    ("qcom_fy2024_totaldebt", "Debt", 177904, 13_270_000_000,
     "LongTermDebt presented beats total debt 14,634"),
    ("qcom_fy2025_totaldebt", "Debt", 179118, 14_811_000_000,
     "LongTermDebt presented; total debt equal"),
]


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("case_id,family,fact_id,value,what", CASES_C8,
                         ids=[c[0] for c in CASES_C8])
def test_decided_additions_answer(case_id, family, fact_id, value, what):
    # oracle: the filer's fact (`what`; value as filed, accession in the fixture,
    # companyfacts `match` where cached); R2 by hand for the pick
    r = lookup(case_id, family)
    assert r.status.value == "OK", r.resolution_notes
    assert r.citation.fact_id == fact_id
    assert r.value == pytest.approx(value, rel=1e-12)  # stored value, unrounded
    assert substitution_of(r) is None


TOTAL_DEBT = [c for c in CASES_C8 if c[1] == "Debt"]
WITH_SUBSTITUTE = [c for c in TOTAL_DEBT if c[0].split("_")[0] in {"lly", "amd", "avgo", "mrk"}
                   and c[0] not in {"lly_fy2024_totaldebt", "lly_fy2023_totaldebt"}]


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(st.sampled_from(["tsm_fy2022_intinc", "tsm_fy2023_intinc"]),
       st.randoms(use_true_random=False))
def test_tsmc_extension_answers_the_same_value_alone(case_id, rnd):
    # oracle: metamorphic — the two TSMC elements carry the same filed value, so dropping
    # the IFRS rows leaves the extension answering that value, in any row order
    rows = store_rows(case_id, "InterestIncome")
    full = lookup(case_id, "InterestIncome", list(rows))
    alone = [r for r in rows if not r["concept"].endswith(":RevenueFromInterest")]
    assert alone and len(alone) < len(rows)
    rnd.shuffle(alone)
    r = lookup(case_id, "InterestIncome", alone)
    assert r.citation.fact_id != full.citation.fact_id
    assert r.value == pytest.approx(full.value, rel=1e-12)  # same filed value
    assert r.unit == full.unit


@pytest.mark.criterion("C8")
@given(st.sampled_from(TOTAL_DEBT), st.integers(min_value=1, max_value=10**12),
       st.booleans())
def test_long_term_debt_always_outranks_combined_debt(case, value, presented):
    # oracle: decision 2026-11-07 — LongTermDebt first; R2 rules 1-4 (same role and
    # period, the presented row first, then rank)
    case_id, _, fact_id, _, _ = case
    rows = store_rows(case_id, "Debt")
    win = next(r for r in rows if r["fact_id"] == fact_id)
    comb = [r for r in rows if r["concept"].endswith(":DebtLongtermAndShorttermCombinedAmount")]
    assert comb
    m = family_member("Debt", CASES[case_id]["cik"], G24 + "LongTermDebt")
    latest = max(r["period_end"] for r in comb)
    added = {**win, "fact_id": 990_002, "concept": G24 + "LongTermDebt",
             "concept_pref_rank": m["rank"], "concept_role": "primary",
             "substitution_code": None, "substitution_note": None, "value": value,
             "period_end": latest, "presented": presented}
    both = [*rows, added]
    first = both[candidate_order(both)[0]]
    assert first["concept"].endswith(":LongTermDebt")
    assert first["period_end"] == latest


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(st.sampled_from(WITH_SUBSTITUTE))
def test_without_combined_debt_the_substitute_answers_again(case):
    # oracle: metamorphic — removing the total-debt fact returns to the C4 answer: the
    # noncurrent line, stated as excluding the current portion
    case_id, _, fact_id, _, _ = case
    rows = [r for r in store_rows(case_id, "Debt")
            if not r["concept"].endswith(":DebtLongtermAndShorttermCombinedAmount")]
    r = lookup(case_id, "Debt", rows)
    assert r.citation.fact_id != fact_id
    assert substitution_of(r)["code"] == "excludes_current_portion"
