"""C9: the manifest counts the build's witness records and refuses an unreviewed disagreement.

Oracle: the hand count of the fixture keys below; each key's status is assigned by hand from
its rendered statement line (the comment beside it) and requirement 1.
"""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import (
    WitnessBlocked,
    resolve,
    statement_witness,
    witness_manifest,
)

STATUSES = ("agree", "agree_other_element", "note_witnessed", "value_recast", "no_fsds",
            "disagree", "ambiguous", "rejected_line", "note_unlisted")
G = "us-gaap:"
AAPL25 = ("2025-09-27", "2024-09-29")
AAPL24 = ("2024-09-28", "2023-10-01")
CY23 = ("2023-12-31", "2023-01-01")
CY24 = ("2024-12-31", "2024-01-01")
CY25 = ("2025-12-31", "2025-01-01")


def _keys(kit):
    """(family, key, annual adsh, annual value, hand-assigned status)."""
    k = kit.key
    absent = k("AAPL", G + "OperatingExpenses", kit.AAPL, *AAPL25, 2025)
    absent["accession"] = kit.ABSENT  # constructed: a 10-K the FSDS data does not hold yet
    return [
        # "Total operating expenses" 62,151 / 57,467 (AAPL FY2025 10-K)
        ("OperatingExpenses", k("AAPL", G + "OperatingExpenses", kit.AAPL, *AAPL25, 2025),
         kit.AAPL, None, "agree"),
        ("OperatingExpenses", k("AAPL", G + "OperatingExpenses", kit.AAPL, *AAPL24, 2024),
         kit.AAPL, None, "agree"),
        # "Proceeds from issuance of term debt, net" — (AAPL, 2024)
        ("DebtIssued",
         k("AAPL", G + "ProceedsFromIssuanceOfLongTermDebt", kit.AAPL, *AAPL24, 2024),
         kit.AAPL, None, "agree"),
        # "Operating revenues" 14,779 (VST 2023)
        ("Revenue", k("VST", G + "Revenues", kit.VST, *CY23, 2023), kit.VST, None, "agree"),
        # cash flows "Depreciation and amortization" 2,097 (MCD 2024)
        ("DepreciationAndAmortization",
         k("MCD", G + "DepreciationAndAmortization", kit.MCD, *CY24, 2024),
         kit.MCD, None, "agree"),
        # "Property, plant and equipment, net" 8,726 (LMT 2024-12-31)
        ("PropertyPlantAndEquipment",
         k("LMT", G + "PropertyPlantAndEquipmentNet", kit.LMT, "2024-12-31", None, 2024),
         kit.LMT, None, "agree"),
        # "Net income attributable to common stockholders" 7,091 (TSLA 2024)
        ("NetIncome", k("TSLA", G + "NetIncomeLoss", kit.TSLA, *CY24, 2024),
         kit.TSLA, None, "agree"),
        # "Comprehensive income attributable to common stockholders" 4,825 (TSLA 2025)
        ("ComprehensiveIncome", k("TSLA", G + "ComprehensiveIncomeNetOfTax", kit.TSLA, *CY25, 2025),
         kit.TSLA, None, "agree"),
        # "Total revenues" 96,773 on RevenueFromContract... (TSLA 2023); key Revenues
        ("Revenue", k("TSLA", G + "Revenues", kit.TSLA, *CY23, 2023),
         kit.TSLA, None, "agree_other_element"),
        # "Property and equipment, net" 357,025 on the finance-lease element (AMZN 2025-12-31)
        ("PropertyPlantAndEquipment",
         k("AMZN", G + "PropertyPlantAndEquipmentNet", kit.AMZN, "2025-12-31", None, 2025),
         kit.AMZN, None, "agree_other_element"),
        # "Term debt" 12,350 + 78,328 = 90,678 (AAPL 2025-09-27), key LongTermDebt from the 10-K
        ("Debt", k("AAPL", G + "LongTermDebt", kit.AAPL, "2025-09-27", None, 2025),
         kit.AAPL, None, "agree_other_element"),
        # no rate line; note 14.1% (ABT 2023)
        ("EffectiveTaxRate",
         k("ABT", G + "EffectiveIncomeTaxRateContinuingOperations", kit.ABT, *CY23, 2023,
           unit="pure"),
         kit.ABT, None, "note_witnessed"),
        # "Interest and other income, net"; note "Interest income" 2,517 (META 2024)
        ("InterestIncome", k("META", G + "InvestmentIncomeInterest", kit.META, *CY24, 2024),
         kit.META, None, "note_witnessed"),
        # "Interest and other, net"; note "Interest income" 1,335 (INTC 2023)
        ("InterestIncome",
         k("INTC", G + "InvestmentIncomeInterest", kit.INTC, "2023-12-30", "2023-01-01", 2023),
         kit.INTC, None, "note_witnessed"),
        # "Accounts payable and accrued expenses"; note "Accounts payable" 5,649 (KO 2025-12-31)
        ("AccountsPayable",
         k("KO", G + "AccountsPayableTradeCurrent", kit.KO, "2025-12-31", None, 2025),
         kit.KO, None, "note_witnessed"),
        # 10-Q 11,292 against the 10-K's 8,875 (LMT 2025-12-31)
        ("PropertyPlantAndEquipment",
         k("LMT", G + "PropertyPlantAndEquipmentNet", "0001628280-26-066928", "2025-12-31",
           None, 2025),
         kit.LMT, 8_875e6, "value_recast"),
        # 10-Q 90,700 against the 10-K's 90,678 (AAPL 2025-09-27)
        ("Debt", k("AAPL", G + "LongTermDebt", "0000320193-26-000020", "2025-09-27", None, 2025),
         kit.AAPL, 90_678e6, "value_recast"),
        # filing not in the FSDS data
        ("OperatingExpenses", absent, kit.ABSENT, None, "no_fsds"),
        # "Deferred revenues" 9,313 against the total 10,546 (ORCL 2024-05-31), resolved
        ("DeferredRevenue",
         k("ORCL", G + "ContractWithCustomerLiability", kit.ORCL, "2024-05-31", None, 2024),
         kit.ORCL, None, "disagree"),
    ]


def _witnesses(kit, keys):
    e = kit.env()
    return [resolve(statement_witness(e.tables, e.rules, e.notes, f, k, a, v), e.resolutions)
            for f, k, a, v, _s in keys]


@pytest.mark.criterion("C9")
def test_manifest_matches_the_hand_count(kit):
    # oracle: hand count of _keys: agree 8, agree_other_element 3, note_witnessed 4,
    # value_recast 2, no_fsds 1, disagree 1 (ORCL, resolved), the other statuses 0
    keys = _keys(kit)
    ws = _witnesses(kit, keys)
    assert [w["status"] for w in ws] == [s for *_x, s in keys]
    m = witness_manifest(ws)
    assert m["n"] == 19
    assert m["by_status"] == {
        "agree": 8, "agree_other_element": 3, "note_witnessed": 4, "value_recast": 2,
        "no_fsds": 1, "disagree": 1, "ambiguous": 0, "rejected_line": 0, "note_unlisted": 0}
    assert m["resolved"] == 1
    assert m["held"] == [
        {"family": "Debt", "cik": "0000320193", "fiscal_year": 2025,
         "key_concept": G + "LongTermDebt", "status": "value_recast"},
        {"family": "OperatingExpenses", "cik": "0000320193", "fiscal_year": 2025,
         "key_concept": G + "OperatingExpenses", "status": "no_fsds"},
        {"family": "PropertyPlantAndEquipment", "cik": "0000936468", "fiscal_year": 2025,
         "key_concept": G + "PropertyPlantAndEquipmentNet", "status": "value_recast"},
    ]


@pytest.mark.criterion("C9")
def test_an_unresolved_disagreement_refuses_the_manifest(kit):
    # oracle: requirement 9; VST's rebuilt revenue component 13,804 against "Operating
    # revenues" 14,779 has no resolution
    e = kit.env()
    vst = kit.key("VST", G + "RevenueFromContractWithCustomerExcludingAssessedTax", kit.VST,
                  *CY23, 2023)
    w = statement_witness(e.tables, e.rules, e.notes, "Revenue", vst, kit.VST)
    blocked = resolve(w, e.resolutions)
    assert blocked["status"] == "disagree" and blocked["resolution"] is None
    ws = _witnesses(kit, _keys(kit))
    with pytest.raises(WitnessBlocked):
        witness_manifest([*ws, blocked])


_ALL: list = []  # the 19 witnesses, computed once per session


@pytest.mark.criterion("C9")
@given(picks=st.lists(st.integers(min_value=0, max_value=18), max_size=30))
def test_counts_of_any_selection_are_the_hand_labels(kit, picks):
    # oracle: the hand-assigned statuses of _keys, counted for any multiset of them
    keys = _keys(kit)
    if not _ALL:
        _ALL.extend(_witnesses(kit, keys))
    chosen = [keys[i] for i in picks]
    m = witness_manifest([_ALL[i] for i in picks])
    want = Counter(s for *_x, s in chosen)
    assert m["n"] == len(chosen)
    assert m["by_status"] == {s: want.get(s, 0) for s in STATUSES}
    assert m["resolved"] == sum(1 for *_x, s in chosen if s == "disagree")
    assert len(m["held"]) == sum(1 for *_x, s in chosen if s in ("value_recast", "no_fsds"))
