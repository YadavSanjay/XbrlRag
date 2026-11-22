"""C1: Debt is the sum of the presented borrowing lines (requirements 3, 5, 7)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

# Lines the Debt rule never selects (accounting vocabulary: payables, leases, taxes, deferred
# revenue, interest), each on its own concept.
DISTRACTORS = [
    ("Accrued compensation", "us-gaap:EmployeeRelatedLiabilitiesCurrent"),
    ("Operating lease liabilities, current", "us-gaap:OperatingLeaseLiabilityCurrent"),
    ("Accrued interest", "us-gaap:InterestPayableCurrent"),
    ("Income taxes payable", "us-gaap:AccruedIncomeTaxesCurrent"),
    ("Other accrued liabilities", "us-gaap:OtherAccruedLiabilitiesCurrent"),
    ("Deferred income taxes and other", "us-gaap:DeferredTaxLiabilitiesNoncurrent"),
]


@pytest.mark.criterion("C1")
def test_filer_h_debt_is_the_presented_borrowing_lines(s4):
    # oracle: hand addition of Filer H's three borrowing lines: Short-term borrowings 300 +
    # Current installments of long-term debt 4,600 + Long-term debt, excluding current
    # installments 48,500 = 53,400 (M); the debt note's LongTermDebt 51,400 is not a line
    f = s4.build(s4.filer_h(), s4.filer_h_notes())
    m = s4.system("Debt", f)
    assert m["status"] == "defined"
    assert m["family"] == "Debt"
    assert m["definition"] == "debt_total"
    assert m["concept"] == "s4:debt_total"
    assert m["basis"] == "presented"
    assert m["unit"] == "USD"
    assert m["value"] == pytest.approx(53_400 * s4.M, rel=1e-12)  # whole millions: exact
    concepts = ["us-gaap:CommercialPaper", "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent",
                "us-gaap:LongTermDebtAndCapitalLeaseObligations"]
    assert [p["concept"] for p in m["parts"]] == concepts
    assert [p["section"] for p in m["parts"]] == ["current", "current", "noncurrent"]
    assert m["fact_ids"] == [f.ids[c] for c in concepts]
    assert [p["fact_id"] for p in m["parts"]] == m["fact_ids"]
    assert [p["value"] for p in m["parts"]] == pytest.approx(
        [300 * s4.M, 4_600 * s4.M, 48_500 * s4.M], rel=1e-12)  # the lines' own values
    assert m["parts"][0]["label"] == "Short-term borrowings"
    assert f.ids["us-gaap:LongTermDebt"] not in m["fact_ids"]
    assert m["readings"] == []
    assert m["reason"] is None


@pytest.mark.criterion("C1")
def test_extension_lines_are_summed(s4):
    # oracle: hand addition of Filer T's extension lines: Current portion of debt and finance
    # leases 1,600 + Debt and finance leases, net of current portion 6,700 = 8,300 (M)
    f = s4.build(s4.filer_t(), s4.filer_t_notes())
    m = s4.system("Debt", f)
    assert m["status"] == "defined"
    assert m["value"] == pytest.approx(8_300 * s4.M, rel=1e-12)  # whole millions: exact
    assert [p["concept"] for p in m["parts"]] == [
        "ext:CurrentPortionOfDebtAndFinanceLeases", "ext:DebtAndFinanceLeasesNetOfCurrentPortion"]
    assert m["fact_ids"] == [f.ids["ext:CurrentPortionOfDebtAndFinanceLeases"],
                             f.ids["ext:DebtAndFinanceLeasesNetOfCurrentPortion"]]


@pytest.mark.criterion("C1")
def test_a_total_debt_line_narrows_to_itself(s4):
    # oracle: the rule's prefer pattern '^total (debt|borrowings)' (key-definitions Notes):
    # the "Total debt" line 1,000 answers alone, not 100 + 900 + 1,000
    lines = [
        s4.line("Short-term debt", "us-gaap:ShortTermBorrowings", 100 * s4.M),
        s4.line(s4.TCL, "us-gaap:LiabilitiesCurrent", 5_000 * s4.M),
        s4.line("Long-term debt", "us-gaap:LongTermDebtNoncurrent", 900 * s4.M),
        s4.line("Total debt", "us-gaap:DebtInstrumentCarryingAmount", 1_000 * s4.M),
    ]
    f = s4.build(lines, [])
    m = s4.system("Debt", f)
    assert m["value"] == pytest.approx(1_000 * s4.M, rel=1e-12)  # one line: exact
    assert m["fact_ids"] == [f.ids["us-gaap:DebtInstrumentCarryingAmount"]]


@pytest.mark.criterion("C1")
def test_no_borrowing_line_is_not_presented(s4):
    # oracle: requirement 5 step 1 (no note fallback): a filing with no borrowing line has no
    # defined Debt, whatever its debt note tags
    lines = [
        s4.line("Accounts payable", "us-gaap:AccountsPayableCurrent", 900 * s4.M),
        s4.line(s4.TCL, "us-gaap:LiabilitiesCurrent", 2_000 * s4.M),
        s4.line("Operating lease liabilities", "us-gaap:OperatingLeaseLiabilityNoncurrent",
                700 * s4.M),
    ]
    m = s4.system("Debt", s4.build(lines, [s4.note("us-gaap:LongTermDebt", 5_000 * s4.M)]))
    assert m["status"] == "not_presented"
    assert m["value"] is None
    assert m["basis"] is None
    assert m["parts"] == []
    assert m["fact_ids"] == []
    assert m["readings"] == []


def _h_variant(s4, extra: list[int], order: list[int], k: int):
    """Filer H with distractor lines added, its sections reordered, values scaled by k."""
    base = s4.filer_h()
    tcl = next(i for i, d in enumerate(base) if d["label"] == s4.TCL)
    current, rest = base[:tcl], base[tcl + 1:]
    current = [current[i] for i in order]
    current += [s4.line(lbl, c, (i + 1) * 10 * s4.M) for i, (lbl, c) in enumerate(DISTRACTORS)
                if i in extra]
    lines = [*current, base[tcl], *reversed(rest)]
    for d in lines:
        if isinstance(d["value"], float):
            d["value"] = d["value"] * k
    notes = [s4.note(n["concept"], n["value"] * k) for n in s4.filer_h_notes()]
    return s4.build(lines, notes)


@pytest.mark.criterion("C1")
@given(extra=st.sets(st.integers(0, len(DISTRACTORS) - 1)),
       order=st.permutations(range(5)),
       k=st.sampled_from([1, 2, 3, 7, 1000]))
def test_distractors_order_and_scale_change_only_the_value(s4, extra, order, k):
    # oracle: requirement 3 (selection is by label and element, sections by position): lines
    # no rule selects and the order inside a section never matter; the sum scales by k
    # (53,400 * k, hand addition)
    f = _h_variant(s4, sorted(extra), list(order), k)
    m = s4.system("Debt", f)
    assert m["status"] == "defined"
    assert m["value"] == pytest.approx(53_400 * s4.M * k, rel=1e-12)  # whole millions: exact
    assert sorted(p["concept"] for p in m["parts"]) == sorted([
        "us-gaap:CommercialPaper", "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent",
        "us-gaap:LongTermDebtAndCapitalLeaseObligations"])
    assert m["fact_ids"] == [f.ids[p["concept"]] for p in m["parts"]]
    assert m["readings"] == []
