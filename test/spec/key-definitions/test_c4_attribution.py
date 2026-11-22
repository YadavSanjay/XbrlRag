"""C4: net and comprehensive income are the parent's figures.

Oracle: requirement 4 of docs/specs/key-definitions.md; the us-gaap 2025 definitions of
NetIncomeLoss and ProfitLoss (committed taxonomy excerpt); the printed income-statement and
comprehensive-income lines of the fixture 10-Ks, hand-checked (spec Notes).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

NI = "us-gaap:NetIncomeLoss"
PL = "us-gaap:ProfitLoss"
CI = "us-gaap:ComprehensiveIncomeNetOfTax"
CI_NCI = "us-gaap:ComprehensiveIncomeNetOfTaxIncludingPortionAttributableToNoncontrollingInterest"
VST_NI = "us-gaap:NetIncomeLossIncludingPortionAttributableToNonredeemableNoncontrollingInterest"
INTC23 = ("2023-01-01", "2023-12-30")
INTC25 = ("2024-12-29", "2025-12-27")
AVGO24 = ("2023-10-30", "2024-11-03")
AVGO25 = ("2024-11-04", "2025-11-02")
VST25 = ("2025-01-01", "2025-12-31")
TSLA25 = ("2025-01-01", "2025-12-31")

CASES = [
    # oracle: INTC FY2025 10-K, 2023: "Net income (loss) attributable to Intel" 1,689 (parent)
    ("INTC", "NetIncome", INTC23, NI, "parent"),
    # oracle: INTC 2023 "Net income (loss)" 1,675 includes non-controlling interests, and the
    # filing reports a different parent figure (1,689)
    ("INTC", "NetIncome", INTC23, PL, "includes_noncontrolling_interests"),
    # oracle: AVGO FY2025 10-K, FY2024: one "Net income" line 5,895 tagged ProfitLoss; the
    # filing tags no NetIncomeLoss, so the consolidated figure is the parent's
    ("AVGO", "NetIncome", AVGO24, PL, "consolidated_is_parent"),
    # oracle: VST FY2025 "Net income" 944 and "Net income attributable to Vistra" 944: equal
    ("VST", "NetIncome", VST25, PL, "consolidated_is_parent"),
    ("VST", "NetIncome", VST25, VST_NI, "consolidated_is_parent"),
    # oracle: TSLA FY2025 "Comprehensive income attributable to common stockholders" 4,825
    # (parent) against "Comprehensive income" 4,886
    ("TSLA", "ComprehensiveIncome", TSLA25, CI, "parent"),
    ("TSLA", "ComprehensiveIncome", TSLA25, CI_NCI, "includes_noncontrolling_interests"),
    # oracle: INTC FY2025 "Total comprehensive income (loss)" 850 against "... attributable
    # to Intel" 557
    ("INTC", "ComprehensiveIncome", INTC25, CI_NCI, "includes_noncontrolling_interests"),
    # oracle: AVGO FY2025 "Comprehensive income" 23,137, the only comprehensive income line
    ("AVGO", "ComprehensiveIncome", AVGO25, CI_NCI, "consolidated_is_parent"),
]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(("ticker", "family", "period", "concept", "expected"), CASES)
def test_attribution_of_the_fixture_lines(kit, ticker, family, period, concept, expected):
    # oracle: the printed lines in the CASES comments and requirement 4
    from src.eval.keys.key_definitions import attribution_status

    got = attribution_status(kit.env().tables, family, kit.FILINGS[ticker][1], period[0],
                             period[1], "USD", concept)
    assert got == expected


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(("family", "concept"), [
    ("Revenue", NI), ("NetIncome", CI), ("ComprehensiveIncome", NI),
    ("NetIncome", "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic"),
])
def test_other_family_or_concept_is_refused(kit, family, concept):
    # oracle: requirement 4 — only the two families and their parent / consolidated elements;
    # income available to common stockholders is another family
    from src.eval.keys.key_definitions import attribution_status

    with pytest.raises(ValueError):
        attribution_status(kit.env().tables, family, kit.FILINGS["VST"][1], *VST25, "USD",
                           concept)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_removing_the_parent_figure_makes_the_consolidated_one_the_parents(kit):
    # oracle: requirement 4 — INTC 2023 ProfitLoss is includes_noncontrolling_interests while
    # NetIncomeLoss (1,689) is reported; with every NetIncomeLoss row removed it becomes
    # consolidated_is_parent, and never the reverse
    from src.eval.keys.key_definitions import attribution_status

    adsh = kit.FILINGS["INTC"][1]
    before = attribution_status(kit.env().tables, "NetIncome", adsh, *INTC23, "USD", PL)
    t = kit.tables_copy()
    t["num"] = [r for r in t["num"] if not (r["adsh"] == adsh and r["tag"] == "NetIncomeLoss")]
    after = attribution_status(t, "NetIncome", adsh, *INTC23, "USD", PL)
    assert (before, after) == ("includes_noncontrolling_interests", "consolidated_is_parent")


@pytest.mark.criterion("C4")
@settings(max_examples=20, deadline=None)
@given(delta=st.integers(min_value=-500, max_value=500))
def test_consolidated_is_parent_exactly_when_values_agree(kit, delta):
    # oracle: requirement 4 — set INTC's 2023 NetIncomeLoss to ProfitLoss (1,675) + delta
    # million: ProfitLoss is consolidated_is_parent iff delta == 0
    from src.eval.keys.key_definitions import attribution_status

    adsh = kit.FILINGS["INTC"][1]
    t = kit.tables_copy()
    for r in t["num"]:
        if (r["adsh"], r["tag"], r["ddate"], r["qtrs"]) == (adsh, "NetIncomeLoss", "20231231", "4"):
            r["value"] = repr(1_675e6 + delta * 1e6)
    got = attribution_status(t, "NetIncome", adsh, *INTC23, "USD", PL)
    expected = "consolidated_is_parent" if delta == 0 else "includes_noncontrolling_interests"
    assert got == expected
    assert attribution_status(t, "NetIncome", adsh, *INTC23, "USD", NI) == "parent"
