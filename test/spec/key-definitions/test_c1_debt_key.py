"""C1: a Debt key is the sum of the borrowing lines the balance sheet presents.

Oracle: requirement 2 of docs/specs/key-definitions.md and the printed balance-sheet lines of
the fixture 10-Ks, hand-checked against the rendered primary documents (spec Notes, "Hand
checks"). Amounts are the filed values in USD; FSDS and companyfacts both carry the value as
filed, so equality is checked at 1e-9 relative (the spec's "the same").
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

SAME = 1e-9  # FSDS and companyfacts carry the filed value; only float rounding may differ

# ticker, period end, FY label, [(concept, report, line, section, lease_inclusive, value)], total
CASES = [
    # oracle: AAPL FY2025 10-K balance sheet "Commercial paper" 7,979, "Term debt" (current)
    # 12,350, "Term debt" (non-current) 78,328: 7,979 + 12,350 + 78,328 = 98,657
    ("AAPL", "2025-09-27", 2025,
     [("us-gaap:CommercialPaper", 5, 21, "current", False, 7_979e6),
      ("us-gaap:LongTermDebtCurrent", 5, 22, "current", False, 12_350e6),
      ("us-gaap:LongTermDebtNoncurrent", 5, 25, "noncurrent", False, 78_328e6)], 98_657e6),
    # oracle: KO FY2025 "Loans and notes payable" 1,551, "Current maturities of long-term
    # debt" 1,822, "Long-term debt" 42,119: 45,492 (the elements' names carry "lease")
    ("KO", "2025-12-31", 2025,
     [("us-gaap:NotesAndLoansPayable", 5, 24, "current", False, 1_551e6),
      ("us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent", 5, 25, "current", True, 1_822e6),
      ("us-gaap:LongTermDebtAndCapitalLeaseObligations", 5, 29, "noncurrent", True, 42_119e6)],
     45_492e6),
    # oracle: WMT FY2025 (2025-01-31) "Short-term borrowings" 3,068, "Long-term debt due within
    # one year" 2,598, "Long-term debt" 33,401: 39,067; its finance-lease lines (800, 5,923)
    # and operating-lease lines (1,499, 12,825) are not debt lines
    ("WMT", "2025-01-31", 2025,
     [("us-gaap:ShortTermBorrowings", 5, 16, "current", False, 3_068e6),
      ("us-gaap:LongTermDebtCurrent", 5, 20, "current", False, 2_598e6),
      ("us-gaap:LongTermDebtNoncurrent", 5, 24, "noncurrent", False, 33_401e6)], 39_067e6),
    # oracle: TSLA FY2025 "Current portion of debt and finance leases" 1,640 and "Debt and
    # finance leases, net of current portion" 6,736: 8,376, both lines include finance leases
    ("TSLA", "2025-12-31", 2025,
     [("ext:LongTermDebtAndFinanceLeasesCurrent", 3, 28, "current", True, 1_640e6),
      ("ext:LongTermDebtAndFinanceLeasesNoncurrent", 3, 30, "noncurrent", True, 6_736e6)],
     8_376e6),
    # oracle: MCD FY2025 "Long-term debt" 39,973; "Current lease liability" 694 and
    # "Long-term lease liability" 14,147 are lease lines, not debt
    ("MCD", "2025-12-31", 2025,
     [("us-gaap:LongTermDebtNoncurrent", 7, 30, "noncurrent", False, 39_973e6)], 39_973e6),
    # oracle: LLY FY2025 "Short-term borrowings and current maturities of long-term debt"
    # 1,635 and "Long-term debt" 40,868: 42,503, the note's "Total debt"
    ("LLY", "2025-12-31", 2025,
     [("us-gaap:DebtCurrent", 5, 20, "current", False, 1_635e6),
      ("us-gaap:LongTermDebtNoncurrent", 5, 27, "noncurrent", False, 40_868e6)], 42_503e6),
]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("ticker", "end", "fy", "lines", "total"), CASES)
def test_debt_is_the_sum_of_presented_borrowing_lines(kit, ticker, end, fy, lines, total):
    # oracle: hand addition of the printed lines named in the CASES comments
    d = kit.debt(ticker, end, fy)
    assert d["status"] == "defined"
    assert d["family"] == "Debt"
    assert d["definition"] == "debt_total"
    assert d["basis"] == "presented"
    assert d["value"] == pytest.approx(total, rel=SAME)
    got = [(c["concept"], c["report"], c["line"], c["section"], c["lease_inclusive"])
           for c in d["components"]]
    assert got == [(c, r, n, s, lease) for c, r, n, s, lease, _v in lines]
    assert [c["value"] for c in d["components"]] == [
        pytest.approx(v, rel=SAME) for *_x, v in lines]
    assert all(c["source"] == "statement" for c in d["components"])
    assert d["key"] is not None
    assert d["key"]["value"] == pytest.approx(total, rel=SAME)


@pytest.mark.criterion("C1")
def test_composite_key_record(kit):
    # oracle: requirement 2 step 4 — the composite key's fields, for AAPL FY2025 (98,657)
    d = kit.debt("AAPL", "2025-09-27", 2025)
    k = d["key"]
    assert k["concept"] == "s4:debt_total"
    assert k["accession"] == kit.FILINGS["AAPL"][1]
    assert k["cik"] == "0000320193"
    assert (k["period_start"], k["period_end"], k["fiscal_year"]) == (None, "2025-09-27", 2025)
    assert k["unit"] == "USD"
    assert k["selection"] == "statement_sum"
    assert k["policies"] == ["S1", "S4", "S6"]
    assert k["key_source"] == "sec_companyfacts"
    assert k["form"] == "10-K"
    assert k["decimals"] is None


@pytest.mark.criterion("C1")
def test_extension_lines_take_the_fsds_value(kit):
    # oracle: requirement 2 step 3 — companyfacts holds no extension facts, so TSLA's two
    # extension debt lines are valued from the FSDS rows (1,640 and 6,736) and the key names
    # that source
    d = kit.debt("TSLA", "2025-12-31", 2025)
    assert [c["source"] for c in d["components"]] == ["statement", "statement"]
    assert d["key"]["key_source"] == "sec_fsds"
    raw = {r["tag"]: float(r["value"]) for r in kit.raw_rows("num")
           if r["adsh"] == kit.FILINGS["TSLA"][1] and r["ddate"] == "20251231"
           and r["qtrs"] == "0" and r["tag"].startswith("LongTermDebtAndFinanceLeases")}
    assert d["value"] == pytest.approx(sum(raw.values()), rel=SAME)
    assert d["value"] == pytest.approx(8_376e6, rel=SAME)


@pytest.mark.criterion("C1")
def test_components_are_the_raw_statement_lines(kit):
    # oracle: the fixtures' raw pre rows — each component's label is the filer's own line
    d = kit.debt("KO", "2025-12-31", 2025)
    adsh = kit.FILINGS["KO"][1]
    for c in d["components"]:
        assert c["plabel"] == kit.raw_pre(adsh, c["report"], c["line"])["plabel"]
        assert c["stmt"] == "BS"
    assert [c["plabel"] for c in d["components"]] == [
        "Loans and notes payable", "Current maturities of long-term debt", "Long-term debt"]


NON_DEBT = st.sampled_from([
    "Operating lease liabilities", "Operating lease liabilities, current", "Total liabilities",
    "Marketable securities", "Finance lease right-of-use assets", "Lease liabilities",
    "Deferred income taxes", "Accrued interest", "Debt securities", "Total stockholders' equity",
    "Unamortized debt issuance costs",
])


@pytest.mark.criterion("C1")
@settings(max_examples=25, deadline=None)
@given(label=NON_DEBT, value=st.integers(min_value=1, max_value=10**12))
def test_non_debt_lines_never_change_the_key(kit, label, value):
    # oracle: requirement 2 step 1 — an operating-lease, asset, total, tax, interest,
    # securities, equity or issuance-cost line is never a borrowing line, so AAPL's key stays
    # the hand sum 98,657
    t = kit.tables_copy()
    kit.add_line(t, kit.FILINGS["AAPL"][1], 5, 26, label, "SyntheticNonDebt", "20250930",
                 float(value))
    d = kit.debt("AAPL", "2025-09-27", 2025, tables=t)
    assert d["status"] == "defined"
    assert d["value"] == pytest.approx(98_657e6, rel=SAME)
    assert len(d["components"]) == 3


@pytest.mark.criterion("C1")
@settings(max_examples=20, deadline=None)
@given(factor=st.floats(min_value=1.001, max_value=3.0))
def test_a_disagreeing_companyfacts_value_blocks(kit, factor):
    # oracle: requirement 2 step 3 — a standard line whose companyfacts value is not the same
    # as its statement value is component_mismatch; no value is keyed
    cf = kit.copy(kit.companyfacts("AAPL"))
    for e in kit.cf_entries(cf, "CommercialPaper"):
        e["val"] = e["val"] * factor
    d = kit.debt("AAPL", "2025-09-27", 2025, cf=cf)
    assert d["status"] == "component_mismatch"
    assert d["key"] is None
    assert d["value"] is None


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_missing_filing_and_missing_lines(kit):
    # oracle: requirement 2 steps 2-3 — an absent filing is no_fsds; a filing whose debt
    # lines are removed is not_presented; a standard line without a companyfacts entry is
    # component_missing
    from src.eval.keys.key_definitions import debt_key

    e = kit.env()
    gone = debt_key(e.tables, e.rules, kit.companyfacts("MCD"), "0000063908", 2025, kit.ABSENT,
                    "2025-12-31", "USD")
    assert (gone["status"], gone["value"], gone["components"]) == ("no_fsds", None, [])
    t = kit.tables_copy()
    kit.drop_lines(t, kit.FILINGS["MCD"][1], {"LongTermDebtNoncurrent"})
    none = kit.debt("MCD", "2025-12-31", 2025, tables=t)
    assert (none["status"], none["value"], none["key"]) == ("not_presented", None, None)
    cf = kit.copy(kit.companyfacts("MCD"))
    del cf["facts"]["us-gaap"]["LongTermDebtNoncurrent"]
    miss = kit.debt("MCD", "2025-12-31", 2025, cf=cf)
    assert (miss["status"], miss["value"]) == ("component_missing", None)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_live_debt_rule_selects_aapl_borrowing_lines(kit):
    # oracle: AAPL FY2025 balance sheet "Commercial paper" 7,979, "Term debt" 12,350 and
    # 78,328 — the key-statement-witness HAND_CHECKED live row for Debt, moved here
    # (spec Notes, "Retired tests touched")
    from src.eval.keys.statement_witness import load_statement_rules, select_lines

    live = load_statement_rules(kit.live / "statement_lines.yaml")
    got = select_lines(kit.env().tables, live["Debt"], kit.FILINGS["AAPL"][1], None,
                       "2025-09-27", "USD")
    assert [(ln["concept"], ln["report"], ln["line"]) for ln in got] == [
        ("us-gaap:CommercialPaper", 5, 21), ("us-gaap:LongTermDebtCurrent", 5, 22),
        ("us-gaap:LongTermDebtNoncurrent", 5, 25)]
    d = kit.debt("AAPL", "2025-09-27", 2025)
    assert d["value"] == pytest.approx(sum(ln["value"] for ln in got), rel=SAME)
