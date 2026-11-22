"""C2: each concept-mapping addition answers its filer's presented line.

docs/specs/screen-candidate-order.md, requirement 2. The additions are primaries of their
family for every filer, ranked after the family's existing primaries and credited as the
family's measure; with requirement 1 (``candidate_order``) a lookup and a screen answer the
filer's presented line. Pure: the committed mapping (``family_member``) and SEC fixtures.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sco_support import NO_ENTRIES_CIK, G, store_rows

from src.retriever.xbrl.family_resolution import candidate_order, family_member, same_measure
from src.retriever.xbrl.xbrl_factstore_screen import select_preferred_concept

PPE = ("PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAsset"
       "AfterAccumulatedDepreciationAndAmortization")
AFS = "AvailableForSaleSecuritiesDebtSecuritiesCurrent"
SENIOR = "ProceedsFromIssuanceOfSeniorLongTermDebt"
SHORT = "ProceedsFromShortTermDebt"
CASH = "CashAndCashEquivalentsAtCarryingValue"
CASH_R = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"

# Each family's primaries before the change, in rank order (the declared lists of
# src/ingest/util/generate_concept_mapping.py and configs/concept_mapping/
# concept_family_overrides.yaml at d43bbfe, read by hand), and the additions in their order.
BEFORE = {
    "PropertyPlantAndEquipment": ["PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipmentGross"],
    "ShortTermInvestments": ["ShortTermInvestments", "AvailableForSaleSecuritiesCurrent",
                             "MarketableSecuritiesCurrent", "OtherShortTermInvestments",
                             "HeldToMaturitySecuritiesCurrent"],
    "DebtIssued": ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromLongTermLinesOfCredit",
                   "ProceedsFromDebtNetOfIssuanceCosts", "ProceedsFromIssuanceOfDebt",
                   "ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet",
                   "ProceedsFromDebtMaturingInMoreThanThreeMonths",
                   "ProceedsFromIssuanceOfMediumTermNotes"],
    "CashAndEquivalents": [CASH, "CashCashEquivalentsAndShortTermInvestments",
                           "CashAndCashEquivalentsAndShortTermInvestments"],
}
ADDED = {
    "PropertyPlantAndEquipment": [PPE],
    "ShortTermInvestments": [AFS],
    "DebtIssued": [SENIOR, SHORT],
    "CashAndEquivalents": [CASH_R],
}


def _answer(ticker: str, fy: int, family: str) -> dict:
    """The row a lookup answers with (candidate_order's first), checked against the screen."""
    rows = store_rows(ticker, fy, family)
    order = candidate_order(rows)
    assert order, f"{ticker} FY{fy}: no candidate row of {family}"
    first = rows[order[0]]
    kept, apart = select_preferred_concept(rows)
    assert apart == [] and kept == [first]
    return first


# --------------------------------------------------------------------------- points
@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("ticker", "fy", "value"), [
    # oracle: FSDS num/pre of each filer's 10-K: the balance-sheet PP&E line tagged
    # PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAsset... ($M); the family had no
    # member for these filings, so today no PP&E is found.
    ("INTC", 2025, 105_414), ("HD", 2025, 28_021), ("MU", 2025, 46_590),
    ("UPS", 2025, 37_731), ("CRM", 2025, 3_236), ("TSLA", 2025, 40_643),
    ("META", 2025, 176_400), ("GOOGL", 2025, 246_597),
])
def test_ppe_including_finance_lease_assets_answers(ticker, fy, value):
    row = _answer(ticker, fy, "PropertyPlantAndEquipment")
    assert row["concept"] == G + PPE and row["presented"]
    assert row["value"] == pytest.approx(value * 1e6, abs=0.5)  # exact integers from SEC


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("ticker", "fy", "value"), [
    # oracle: FSDS num/pre: the balance-sheet "Short-term investments" (INTC, MU) and
    # "Marketable securities" (ORCL) lines tagged AvailableForSaleSecuritiesDebtSecuritiesCurrent
    # ($M); the dev/validation key of int-072122c32d0e (FY2022 sum) holds the same three.
    ("INTC", 2022, 17_194), ("MU", 2022, 1_069), ("ORCL", 2022, 519),
])
def test_current_available_for_sale_debt_securities_answer(ticker, fy, value):
    row = _answer(ticker, fy, "ShortTermInvestments")
    assert row["concept"] == G + AFS and row["presented"]
    assert row["value"] == pytest.approx(value * 1e6, abs=0.5)  # exact integers from SEC


@pytest.mark.criterion("C2")
def test_short_term_and_senior_note_proceeds_answer_and_rank_after_long_term_lines():
    # oracle: FSDS cash-flow lines ($M). AMD FY2025 "Proceeds from debt and commercial paper"
    # (ProceedsFromShortTermDebt 2,441) is its only issuance line; ORCL FY2025 "Proceeds from
    # issuances of senior notes" (ProceedsFromIssuanceOfSeniorLongTermDebt 19,548). Control:
    # COST FY2024 presents both "Proceeds from issuance of long-term debt" 498 and "Proceeds
    # from short-term borrowings" 928 and no total: the long-term line keeps answering, as
    # the v2 statement-line rule (short-term lines excluded) keys it.
    amd = _answer("AMD", 2025, "DebtIssued")
    assert amd["concept"] == G + SHORT
    assert amd["value"] == pytest.approx(2_441e6, abs=0.5)  # exact integers from SEC
    orcl = _answer("ORCL", 2025, "DebtIssued")
    assert orcl["concept"] == G + SENIOR
    assert orcl["value"] == pytest.approx(19_548e6, abs=0.5)  # exact integers from SEC
    cost = _answer("COST", 2024, "DebtIssued")
    assert cost["concept"] == G + "ProceedsFromIssuanceOfLongTermDebt"
    assert cost["value"] == pytest.approx(498e6, abs=0.5)  # exact integers from SEC
    assert any(r["concept"] == G + SHORT for r in store_rows("COST", 2024, "DebtIssued"))


@pytest.mark.criterion("C2")
def test_cash_including_restricted_answers_only_without_a_plain_cash_line():
    # oracle: FSDS num/pre ($M). PG FY2025 presents "Cash and cash equivalents" 9,556 tagged
    # CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents and no plain cash fact.
    # INTC FY2025 presents plain CashAndCashEquivalentsAtCarryingValue 14,265 on the balance
    # sheet and the inclusive 14,712 on the cash-flow statement: the plain line answers.
    pg = _answer("PG", 2025, "CashAndEquivalents")
    assert pg["concept"] == G + CASH_R
    assert pg["value"] == pytest.approx(9_556e6, abs=0.5)  # exact integers from SEC
    intc_rows = store_rows("INTC", 2025, "CashAndEquivalents")
    assert {r["concept"] for r in intc_rows} == {G + CASH, G + CASH_R}
    assert all(r["presented"] for r in intc_rows)
    intc = _answer("INTC", 2025, "CashAndEquivalents")
    assert intc["concept"] == G + CASH
    assert intc["value"] == pytest.approx(14_265e6, abs=0.5)  # exact integers from SEC


# --------------------------------------------------------------------------- properties
@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_additions_are_measure_primaries_ranked_after_every_existing_primary():
    # oracle: requirement 2 — appended after the family's primaries, in the listed order;
    # no existing primary changes rank; each addition is credited as the family's measure.
    for family, before in BEFORE.items():
        ranks = [family_member(family, NO_ENTRIES_CIK, G + q)["rank"] for q in before]
        assert ranks == list(range(1, len(before) + 1)), family
        for n, q in enumerate(ADDED[family], start=len(before) + 1):
            m = family_member(family, NO_ENTRIES_CIK, G + q)
            assert m == {"rank": n, "role": "primary", "substitution": None}, (family, q)
            assert same_measure(family, NO_ENTRIES_CIK, G + q), (family, q)


@pytest.mark.criterion("C2")
@given(st.booleans(), st.booleans(), st.sampled_from([None, -6, -8]),
       st.sampled_from([None, -6, -8]), st.integers(1, 10_000), st.integers(1, 10_000))
def test_plain_cash_wins_unless_only_the_inclusive_line_is_presented(
        p_plain, p_incl, d_plain, d_incl, id_plain, id_incl):
    # oracle: requirement 2 with candidate_order (R2): both are primaries at one period, so
    # the presented one wins; at equal presentation the lower rank (plain cash) wins,
    # whatever the precision or fact id.
    rows = []
    for concept, p, d, i in ((CASH, p_plain, d_plain, id_plain),
                             (CASH_R, p_incl, d_incl, id_incl + 20_000)):
        m = family_member("CashAndEquivalents", NO_ENTRIES_CIK, G + concept)
        assert m is not None
        rows.append({"concept": G + concept, "concept_pref_rank": m["rank"],
                     "concept_role": m["role"], "presented": p, "decimals": d,
                     "fact_id": i, "value": 1.0, "period_end": "2025-06-30"})
    first = rows[candidate_order(rows)[0]]["concept"]
    assert first == (G + CASH_R if (p_incl and not p_plain) else G + CASH)
