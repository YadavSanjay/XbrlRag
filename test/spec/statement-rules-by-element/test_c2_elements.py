"""C2: a rule selects a line by its standard element, whatever the line's label.

Oracle: requirement 2 of docs/specs/statement-rules-by-element.md, and TSM's FY2024 20-F
consolidated balance sheet (NT$ millions, read by hand from the primary document,
d896993d20f.htm): "Long-term liabilities - current portion" 9,293.3 (2023) / 59,857.9 (2024)
/ US$1,825.5 (2024 convenience translation); "Bonds payable" 913,899.8 / 926,604.5 /
US$28,258.7; "Long-term bank loans" 4,383.0 / 31,824.4 / US$970.5. FSDS rows: report 2,
lines 38, 42 and 43, tags ifrs-full CurrentPortionOfLongtermBorrowings,
NoncurrentPortionOfNoncurrentBondsIssued and LongtermBorrowings.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import line_is_candidate, select_lines

# tolerance: filed amounts with one decimal in millions; rel 1e-12 absorbs binary floating
# point of the sum only.
REL = 1e-12
CURRENT = "ifrs-full:CurrentPortionOfLongtermBorrowings"
BONDS = "ifrs-full:NoncurrentPortionOfNoncurrentBondsIssued"
LOANS = "ifrs-full:LongtermBorrowings"


def _debt_with_element(kit) -> dict:
    return {**kit.env().rules["Debt"], "elements": [CURRENT]}


@pytest.mark.criterion("C2")
def test_element_match_is_label_independent_point():
    # oracle: requirement 2 - a line on a listed element is a candidate whatever its label,
    # exclusions included; another concept falls back to the label test
    rule = {"statements": ["BS"], "parenthetical": False, "include": ["\\bdebt\\b"],
            "require": [], "exclude": ["\\binterest"], "section": None, "prefer": [],
            "combine": "sum", "substitutes": {}, "elements": [CURRENT]}
    assert line_is_candidate(rule, "Long-term liabilities - current portion", CURRENT) is True
    assert line_is_candidate(rule, "Accrued interest", CURRENT) is True
    assert line_is_candidate(rule, "Long-term liabilities - current portion", LOANS) is False
    assert line_is_candidate(rule, "Long-term debt", LOANS) is True
    assert line_is_candidate(rule, "Debt and accrued interest", LOANS) is False
    # an extension line never matches by element, even with the same local name
    assert line_is_candidate(rule, "Current portion", "ext:CurrentPortionOfLongtermBorrowings") \
        is False


@pytest.mark.criterion("C2")
@given(label=st.text(max_size=60), other=st.sampled_from([LOANS, BONDS, "us-gaap:DebtCurrent",
                                                          "ext:CurrentPortionOfLongtermBorrowings"]))
def test_element_match_is_invariant_to_the_label(label, other):
    # oracle: requirement 2 (property) - for any label, the listed element is a candidate; an
    # unlisted concept is a candidate exactly when the label passes the label test (here an
    # exclude of any character rejects every label, and the empty label fails the include)
    rule = {"statements": ["BS"], "parenthetical": False, "include": ["^"], "require": [],
            "exclude": ["(?s)."], "section": None, "prefer": [], "combine": "one",
            "substitutes": {}, "elements": [CURRENT, "us-gaap:LongTermDebtNoncurrent"]}
    assert line_is_candidate(rule, label, CURRENT) is True
    assert line_is_candidate(rule, label, "us-gaap:LongTermDebtNoncurrent") is True
    assert line_is_candidate(rule, label, other) is (label == "")


@pytest.mark.criterion("C2")
def test_tsm_debt_gains_the_current_portion_by_element(kit):
    # oracle: TSM FY2024 balance sheet, hand-checked (module docstring). The live Debt rule
    # selects lines 42 and 43 by label; with CurrentPortionOfLongtermBorrowings listed, line
    # 38 joins: 59,857.9 + 926,604.5 + 31,824.4 = 1,018,286.8 (NT$ millions), and for 2023
    # 9,293.3 + 913,899.8 + 4,383.0 = 927,576.1; US$ 1,825.5 + 28,258.7 + 970.5 = 31,054.7
    e = kit.env()
    rule = _debt_with_element(kit)
    for end, unit, values, total in (
            ("2024-12-31", "TWD", (59_857.9e6, 926_604.5e6, 31_824.4e6), 1_018_286.8e6),
            ("2023-12-31", "TWD", (9_293.3e6, 913_899.8e6, 4_383.0e6), 927_576.1e6),
            ("2024-12-31", "USD", (1_825.5e6, 28_258.7e6, 970.5e6), 31_054.7e6)):
        got = select_lines(e.tables, rule, kit.TSM, None, end, unit)
        assert [(ln["concept"], ln["report"], ln["line"]) for ln in got] == [
            (CURRENT, 2, 38), (BONDS, 2, 42), (LOANS, 2, 43)]
        assert [ln["value"] for ln in got] == [pytest.approx(v, rel=REL) for v in values]
        assert sum(ln["value"] for ln in got) == pytest.approx(total, rel=REL)
        assert got[0]["plabel"] == "Long-term liabilities - current portion"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@pytest.mark.parametrize("label", ["Accrued interest on guarantee deposits", "", "TOTAL",
                                   "Lease liabilities - operating"])
def test_relabelling_the_element_line_changes_nothing(kit, label):
    # oracle: requirement 2 (metamorphic) - TSM line 38 relabelled (even to a label the Debt
    # rule excludes) is still selected with the same value; given an extension version, it
    # is no longer selected (its label names no borrowing)
    rule = _debt_with_element(kit)
    t = kit.tables_copy()
    kit.set_pre(t, kit.TSM, 38, plabel=label)
    got = select_lines(t, rule, kit.TSM, None, "2024-12-31", "TWD")
    assert [(ln["line"], ln["plabel"]) for ln in got] == [
        (38, label), (42, "Bonds payable"), (43, "Long-term bank loans")]
    assert got[0]["value"] == pytest.approx(59_857.9e6, rel=REL)
    kit.set_pre(t, kit.TSM, 38, plabel="Long-term liabilities - current portion", version=kit.TSM)
    for r in t["num"]:
        if r["adsh"] == kit.TSM and r["tag"] == "CurrentPortionOfLongtermBorrowings":
            r["version"] = kit.TSM
    got = select_lines(t, rule, kit.TSM, None, "2024-12-31", "TWD")
    assert [ln["line"] for ln in got] == [42, 43]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_element_lines_still_obey_statement_and_section(kit):
    # oracle: requirement 2 - statements, parenthetical lines and sections still apply to an
    # element line: moved to the income statement, or marked parenthetical, line 38 drops out;
    # under section current_liabilities (between "^total assets" and "Total current
    # liabilities"; TSM labels its total assets "TOTAL", so the section is absent) nothing is
    # selected
    rule = _debt_with_element(kit)
    for edit in ({"stmt": "IS"}, {"inpth": "1"}):
        t = kit.tables_copy()
        kit.set_pre(t, kit.TSM, 38, **edit)
        got = select_lines(t, rule, kit.TSM, None, "2024-12-31", "TWD")
        assert [ln["line"] for ln in got] == [42, 43]
    t = kit.tables_copy()
    got = select_lines(t, {**rule, "section": "current_liabilities"}, kit.TSM, None,
                       "2024-12-31", "TWD")
    assert got == []
    kit.set_pre(t, kit.TSM, 27, plabel="Total assets")
    got = select_lines(t, {**rule, "section": "current_liabilities"}, kit.TSM, None,
                       "2024-12-31", "TWD")
    assert [ln["line"] for ln in got] == [38]
