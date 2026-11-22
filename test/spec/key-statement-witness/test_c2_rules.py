"""C2: the committed statement-line rules select the filer's own, hand-checked line.

Oracle: each fixture filing's rendered statement on EDGAR (our local copy of the primary
document), checked by hand; the line positions are the FSDS ``pre`` rows of those lines. The
live rules file must be the locked copy, byte for byte (SHA-256 pinned in the spec).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_statement_rules, select_lines

# tolerance: filed integer amounts; rel 1e-12 absorbs binary floating point only.
REL = 1e-12
RULES_SHA256 = "661bed5dc7d7d458e0402cb4d8e043f0979f474bfe4d4a113a625b0aa2eff295"

ROU = ("us-gaap:PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAsset"
       "AfterAccumulatedDepreciationAndAmortization")
PRETAX_BEFORE_EQUITY = ("us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
                        "MinorityInterestAndIncomeLossFromEquityMethodInvestments")
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
Y23 = ("2023-01-01", "2023-12-31")
Y24 = ("2024-01-01", "2024-12-31")
Y25 = ("2025-01-01", "2025-12-31")

# family, filing, period_start, period_end, unit -> [(concept, stmt, report, line, value)]
HAND_CHECKED = [
    # oracle: MCD FY2025 10-K cash flows "Depreciation and amortization" 2024: 2,097 (the
    # income statement's "Depreciation and amortization" sub-line 447 is not the family's line)
    ("DepreciationAndAmortization", "MCD", *Y24, "USD",
     [("us-gaap:DepreciationAndAmortization", "CF", 9, 5, 2_097e6)]),
    # oracle: VST FY2025 10-K income statements "Operating revenues" 2023: 14,779; 2025: 17,738
    ("Revenue", "VST", *Y23, "USD", [("us-gaap:Revenues", "IS", 3, 1, 14_779e6)]),
    ("Revenue", "VST", *Y25, "USD", [("us-gaap:Revenues", "IS", 3, 1, 17_738e6)]),
    # oracle: PG FY2026 10-K balance sheet shows shares issued (4,009.2) and treasury shares,
    # no shares-outstanding line; its parenthetical "Common stock, issued (in shares)" is not one
    ("SharesOutstanding", "PG", None, "2025-06-30", "shares", []),
    # oracle: AAPL FY2025 10-K balance sheet "Term debt" (current) 12,350 and "Term debt"
    # (non-current) 78,328; "Commercial paper" 7,979 is not long-term debt
    ("Debt", "AAPL", None, "2025-09-27", "USD",
     [("us-gaap:LongTermDebtCurrent", "BS", 5, 22, 12_350e6),
      ("us-gaap:LongTermDebtNoncurrent", "BS", 5, 25, 78_328e6)]),
    # oracle: AAPL FY2025 balance sheet "Common stock, shares outstanding (in shares)";
    # 14,773,260 thousand shares issued and outstanding
    ("SharesOutstanding", "AAPL", None, "2025-09-27", "shares",
     [("us-gaap:CommonStockSharesOutstanding", "BS", 5, 30, 14_773_260_000.0)]),
    # oracle: AAPL FY2025 balance sheet current "Marketable securities" 18,763 (the non-current
    # line of the same label, 77,723, sits below "Total current assets")
    ("ShortTermInvestments", "AAPL", None, "2025-09-27", "USD",
     [("us-gaap:MarketableSecuritiesCurrent", "BS", 5, 4, 18_763e6)]),
    # oracle: TSLA FY2025 10-K income statements 2024: "Net income" 7,153 and "Net income
    # attributable to common stockholders" 7,091 (the parent's line)
    ("NetIncome", "TSLA", *Y24, "USD", [("us-gaap:NetIncomeLoss", "IS", 5, 29, 7_091e6)]),
    # oracle: TSLA comprehensive income 2025: "Comprehensive income" 4,886 and "Comprehensive
    # income attributable to common stockholders" 4,825
    ("ComprehensiveIncome", "TSLA", *Y25, "USD",
     [("us-gaap:ComprehensiveIncomeNetOfTax", "CI", 6, 9, 4_825e6)]),
    # oracle: TSLA income statements "Total revenues" 2023: 96,773 (FSDS plabel "Revenues")
    ("Revenue", "TSLA", *Y23, "USD", [(REV, "IS", 5, 12, 96_773e6)]),
    # oracle: INTC FY2025 10-K "Net income (loss) attributable to Intel" 2023: 1,689 (not
    # "Net income (loss)" 1,675 including non-controlling interests)
    ("NetIncome", "INTC", "2023-01-01", "2023-12-30", "USD",
     [("us-gaap:NetIncomeLoss", "IS", 3, 15, 1_689e6)]),
    # oracle: AMZN FY2025 10-K balance sheet "Property and equipment, net" 357,025
    ("PropertyPlantAndEquipment", "AMZN", None, "2025-12-31", "USD",
     [(ROU, "BS", 7, 8, 357_025e6)]),
    # oracle: AMZN income statements "Total operating expenses" 2025: 636,949 (CostsAndExpenses)
    ("OperatingExpenses", "AMZN", *Y25, "USD",
     [("us-gaap:CostsAndExpenses", "IS", 4, 15, 636_949e6)]),
    # oracle: AMZN income statements "Interest expense" 2025: (2,274)
    ("InterestExpense", "AMZN", *Y25, "USD",
     [("us-gaap:InterestExpenseNonoperating", "IS", 4, 18, 2_274e6)]),
    # oracle: AMD FY2025 10-K "Income from continuing operations before income taxes and equity
    # income" 2024: 1,989
    ("PretaxIncome", "AMD", "2023-12-31", "2024-12-28", "USD",
     [(PRETAX_BEFORE_EQUITY, "IS", 3, 14, 1_989e6)]),
    # oracle: ORCL FY2025 10-K balance sheets "Deferred revenues" 2024-05-31: 9,313
    ("DeferredRevenue", "ORCL", None, "2024-05-31", "USD",
     [("us-gaap:ContractWithCustomerLiabilityCurrent", "BS", 2, 21, 9_313e6)]),
    # oracle: LMT FY2025 10-K balance sheets "Property, plant and equipment, net" 2025: 8,875
    ("PropertyPlantAndEquipment", "LMT", None, "2025-12-31", "USD",
     [("us-gaap:PropertyPlantAndEquipmentNet", "BS", 6, 9, 8_875e6)]),
    # oracle: MCD FY2025 balance sheet "Long-term debt" 2025-12-31: 39,973
    ("Debt", "MCD", None, "2025-12-31", "USD",
     [("us-gaap:LongTermDebtNoncurrent", "BS", 7, 30, 39_973e6)]),
    # oracle: KO FY2025 balance sheet "Accounts payable and accrued expenses" 14,813 is broader
    # than accounts payable (5,649 in the note): no accounts-payable line
    ("AccountsPayable", "KO", None, "2025-12-31", "USD", []),
    # oracle: KO FY2025 income statement shows "Selling, general and administrative expenses"
    # only: no selling-and-marketing line
    ("SellingAndMarketing", "KO", *Y25, "USD", []),
    # oracle: META FY2025 income statement "Interest and other income, net" is broader than
    # interest income: no interest-income line
    ("InterestIncome", "META", *Y25, "USD", []),
    # oracle: INTC FY2025 income statement "Interest and other, net": no interest-income line
    ("InterestIncome", "INTC", "2023-01-01", "2023-12-30", "USD", []),
    # oracle: AAPL FY2025 income statements "Total operating expenses" 2024: 57,467
    ("OperatingExpenses", "AAPL", "2023-10-01", "2024-09-28", "USD",
     [("us-gaap:OperatingExpenses", "IS", 3, 13, 57_467e6)]),
]


def _sel(kit, family, ticker, start, end, unit):
    e = kit.env()
    return select_lines(e.tables, e.rules[family], getattr(kit, ticker), start, end, unit)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("family", "ticker", "start", "end", "unit", "expected"), HAND_CHECKED)
def test_rules_select_the_hand_checked_line(kit, family, ticker, start, end, unit, expected):
    # oracle: the rendered statement line named in the table comment, checked by hand
    got = _sel(kit, family, ticker, start, end, unit)
    assert [(ln["concept"], ln["stmt"], ln["report"], ln["line"]) for ln in got] == [
        (c, s, r, n) for c, s, r, n, _v in expected]
    assert [ln["value"] for ln in got] == [pytest.approx(v, rel=REL) for *_x, v in expected]
    for ln in got:
        assert ln["adsh"] == getattr(kit, ticker)


@pytest.mark.criterion("C2")
def test_live_rules_keep_the_locked_families(kit):
    # oracle: the SHA-256 pinned in docs/specs/key-statement-witness.md requirement 2 for the
    # r1 copy; the live file is r2 since docs/specs/witness-rules-r2.md requirement 1, which
    # keeps r1's families
    live = kit.live / "statement_lines.yaml"
    assert kit.sha(kit.rules_path) == RULES_SHA256
    assert live.exists(), "configs/eval/v2/statement_lines.yaml is missing"
    assert set(load_statement_rules(live)) == set(load_statement_rules(kit.rules_path))


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("family", "ticker", "start", "end", "unit", "expected"),
                         [c for c in HAND_CHECKED if c[0] != "Debt"])
def test_live_rules_select_the_hand_checked_line(kit, family, ticker, start, end, unit,
                                                 expected):
    # oracle: the rendered statement line named in the table comment, checked by hand;
    # docs/specs/witness-rules-r2.md requirement 1 holds the live (r2) rules to it too. The
    # live Debt rule is total debt since docs/specs/key-definitions.md (its C1 holds AAPL's
    # live Debt lines: commercial paper and both term-debt lines)
    e = kit.env()
    rules = load_statement_rules(kit.live / "statement_lines.yaml")
    got = select_lines(e.tables, rules[family], getattr(kit, ticker), start, end, unit)
    assert [(ln["concept"], ln["stmt"], ln["report"], ln["line"]) for ln in got] == [
        (c, s, r, n) for c, s, r, n, _v in expected]
    assert [ln["value"] for ln in got] == [pytest.approx(v, rel=REL) for *_x, v in expected]


@pytest.mark.criterion("C2")
def test_rules_name_no_mapping_and_cover_every_family(kit):
    # oracle: requirement 2 (no concept_mapping reference) and the 51 concept families of
    # configs/eval/v2/lexicon.yaml metrics (the derived ratios have no concept of their own)
    text = kit.rules_path.read_text(encoding="utf-8")
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for word in ("concept_mapping", "pref_rank", "preference_rank", "mv_financial_facts", "rank:"):
        assert word not in body
    rules = load_statement_rules(kit.rules_path)
    assert len(rules) == 51
    assert rules["TotalAssets"] == {
        "statements": ["BS"], "parenthetical": False, "include": ["^total assets"],
        "require": [], "exclude": [], "section": None, "prefer": [], "combine": "one",
        "substitutes": {}}
    assert rules["Debt"]["combine"] == "sum"
    assert rules["OperatingExpenses"]["substitutes"] == {
        "us-gaap:CostsAndExpenses": {
            "code": "includes_cost_of_revenue",
            "note": "Total costs and expenses, including cost of revenue."}}


BAD_RULES = [
    "version: 2\nfamilies:\n  X: {statements: [IS], include: ['a']}\n",
    "version: 1\nfamilies:\n  X: {statements: [IS], include: ['a'], colour: red}\n",
    "version: 1\nfamilies:\n  X: {statements: [XX], include: ['a']}\n",
    "version: 1\nfamilies:\n  X: {statements: [IS], include: ['(']}\n",
    "version: 1\nfamilies:\n  X: {statements: [IS]}\n",
    "version: 1\nfamilies:\n  X: {include: ['a']}\n",
    "version: 1\nfamilies:\n  X: {statements: [BS], include: ['a'], section: noncurrent}\n",
    "version: 1\nfamilies:\n  X: {statements: [BS], include: ['a'], combine: max}\n",
]
GOOD_RULES = "version: 1\nfamilies:\n  X: {statements: [IS], include: ['a']}\n"


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("text", BAD_RULES)
def test_malformed_rules_are_refused(tmp_path: Path, text):
    # oracle: requirement 2, the loader's ValueError cases (version, unknown field, statement
    # code, pattern, missing statements/include, section, combine)
    p = tmp_path / "rules.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_statement_rules(p)
    ok = tmp_path / "ok.yaml"
    ok.write_text(GOOD_RULES, encoding="utf-8")
    assert list(load_statement_rules(ok)) == ["X"]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_statement_preference_falls_back_to_the_next_statement(kit):
    # oracle: requirement 2 step 2 with MCD's hand-checked lines: without its cash-flow rows the
    # income-statement "Depreciation and amortization" sub-line (447, report 3 line 12) is next
    e = kit.env()
    tables = {k: list(v) for k, v in e.tables.items()}
    tables["pre"] = [r for r in tables["pre"] if not (r["adsh"] == kit.MCD and r["stmt"] == "CF")]
    rule = e.rules["DepreciationAndAmortization"]
    got = select_lines(tables, rule, kit.MCD, *Y24, "USD")
    assert [(ln["concept"], ln["stmt"], ln["report"], ln["line"]) for ln in got] == [
        ("us-gaap:DepreciationDepletionAndAmortization", "IS", 3, 12)]
    assert got[0]["value"] == pytest.approx(447e6, rel=REL)


@pytest.mark.criterion("C2")
@given(case=st.sampled_from(HAND_CHECKED), upper=st.booleans())
def test_selection_ignores_label_case_and_unrelated_lines(kit, case, upper):
    # oracle: the hand-checked selection of the same case; patterns are case-insensitive and a
    # line no rule could match ("Total widgets") changes nothing
    family, ticker, start, end, unit, expected = case
    e = kit.env()
    adsh = getattr(kit, ticker)
    tables = {k: [dict(r) for r in v] for k, v in e.tables.items()}
    for r in tables["pre"]:
        if r["adsh"] == adsh:
            r["plabel"] = r["plabel"].upper() if upper else r["plabel"].lower()
    tables["pre"].append({"adsh": adsh, "report": "99", "line": "1", "stmt": "IS", "inpth": "0",
                          "rfile": "H", "tag": "Widgets", "version": adsh,
                          "plabel": "Total widgets", "negating": "0"})
    got = select_lines(tables, e.rules[family], adsh, start, end, unit)
    assert [(ln["concept"], ln["report"], ln["line"]) for ln in got] == [
        (c, r, n) for c, _s, r, n, _v in expected]
