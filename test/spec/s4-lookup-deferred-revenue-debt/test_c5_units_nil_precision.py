"""C5: units are never mixed; nil and INF never become 0; conflicting facts refuse."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

COMBINED = "us-gaap:DebtLongtermAndShorttermCombinedAmount"
LTD = "us-gaap:LongTermDebt"
CWCL = "us-gaap:ContractWithCustomerLiability"


def _with(lines, concept, **changes):
    for d in lines:
        if d["concept"] == concept:
            d.update(changes)
    return lines


@pytest.mark.criterion("C5")
def test_a_selected_line_in_another_unit_refuses(s4):
    # oracle: requirement 3 (never add across units): one of Filer H's borrowing lines is in
    # EUR, so no Debt value exists in USD
    lines = _with(s4.filer_h(), "us-gaap:CommercialPaper", unit="EUR")
    m = s4.system("Debt", s4.build(lines, s4.filer_h_notes()))
    assert m["status"] == "unit_mismatch"
    assert m["value"] is None
    assert m["basis"] is None
    assert m["parts"] == []
    assert m["fact_ids"] == []
    assert m["reason"]
    assert "unit" in m["disclosure"].lower()


@pytest.mark.criterion("C5")
def test_a_note_in_another_unit_or_nil_is_absent(s4):
    # oracle: requirement 4: Filer G's combined amount in EUR is absent, so the reading falls
    # to LongTermDebt 48,800 (> 46,500); its nil ContractWithCustomerLiability is absent, so
    # DeferredRevenue is the current line 6,600 (no_noncurrent_reported), never 0
    notes = [s4.note(COMBINED, 49_000 * s4.M, unit="EUR"), s4.note(LTD, 48_800 * s4.M),
             s4.note(CWCL, s4.NIL)]
    f = s4.build(s4.filer_g(), notes)
    debt = s4.system("Debt", f)
    assert debt["value"] == pytest.approx(46_500 * s4.M, rel=1e-12)  # one line: exact
    assert [(r["reading"], r["concept"]) for r in debt["readings"]] == [("note_total", LTD)]
    dr = s4.system("DeferredRevenue", f)
    assert dr["basis"] == "no_noncurrent_reported"
    assert dr["value"] == pytest.approx(6_600 * s4.M, rel=1e-12)  # the line: exact


@pytest.mark.criterion("C5")
def test_nil_lines_are_no_part_and_never_zero(s4):
    # oracle: requirement 3 (a nil is never a value): Filer H with a nil commercial-paper line:
    # 4,600 + 48,500 = 53,100 (M); with every borrowing line nil there is no defined Debt
    lines = _with(s4.filer_h(), "us-gaap:CommercialPaper", value=s4.NIL)
    f = s4.build(lines, [])
    m = s4.system("Debt", f)
    assert m["value"] == pytest.approx(53_100 * s4.M, rel=1e-12)  # whole millions: exact
    assert f.ids["us-gaap:CommercialPaper"] not in m["fact_ids"]
    for c in ("us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent",
              "us-gaap:LongTermDebtAndCapitalLeaseObligations"):
        lines = _with(lines, c, value=s4.NIL)
    none = s4.system("Debt", s4.build(lines, []))
    assert none["status"] == "not_presented"
    assert none["value"] is None


@pytest.mark.criterion("C5")
def test_decimals_is_the_coarsest_finite_part(s4):
    # oracle: requirement 7: parts at -6 and -5 give -6; an exact part (INF or None) beside
    # -5 gives -5; every part exact gives None (INF is exact, never 0)
    def debt(d1, d2):
        lines = _with(s4.filer_t(), "ext:CurrentPortionOfDebtAndFinanceLeases", decimals=d1)
        lines = _with(lines, "ext:DebtAndFinanceLeasesNetOfCurrentPortion", decimals=d2)
        return s4.system("Debt", s4.build(lines, []))

    assert debt(-6, -5)["decimals"] == -6
    assert debt("INF", -5)["decimals"] == -5
    assert debt(None, -5)["decimals"] == -5
    exact = debt("INF", None)
    assert exact["decimals"] is None
    assert exact["value"] == pytest.approx(8_300 * s4.M, rel=1e-12)  # whole millions: exact


@pytest.mark.criterion("C5")
def test_conflicting_facts_refuse_only_where_read(s4):
    # oracle: requirement 4: two different ContractWithCustomerLiability facts (8,600 and
    # 8,700) make DeferredRevenue conflicting; Debt never reads them and stays 46,500
    notes = [*s4.filer_g_notes(), s4.note(CWCL, 8_700 * s4.M)]
    f = s4.build(s4.filer_g(), notes)
    dr = s4.system("DeferredRevenue", f)
    assert dr["status"] == "conflicting_facts"
    assert dr["value"] is None
    assert dr["fact_ids"] == []
    assert dr["reason"]
    assert "conflicting" in dr["disclosure"].lower()
    debt = s4.system("Debt", f)
    assert debt["status"] == "defined"
    assert debt["value"] == pytest.approx(46_500 * s4.M, rel=1e-12)  # one line: exact


@pytest.mark.criterion("C5")
def test_equal_duplicates_count_once_with_the_first_fact_id(s4):
    # oracle: requirement 4: two ContractWithCustomerLiability facts of 8,600 are one fact,
    # cited by the first one's id (NOTE_ID0 + 2, the third note)
    notes = [*s4.filer_g_notes(), s4.note(CWCL, 8_600 * s4.M)]
    f = s4.build(s4.filer_g(), notes)
    m = s4.system("DeferredRevenue", f)
    assert m["value"] == pytest.approx(8_600 * s4.M, rel=1e-12)  # the tagged total
    assert m["fact_ids"] == [f.ids[CWCL]]


@pytest.mark.criterion("C5")
def test_a_valued_line_without_a_fact_id_raises(s4):
    # oracle: requirement 2: every value must be citable, so a valued line with no fact id is
    # an input error
    f = s4.build(s4.filer_h(), [])
    f.lines[2]["fact_id"] = None
    with pytest.raises(ValueError):
        s4.system("Debt", f)


@pytest.mark.criterion("C5")
@given(at=st.lists(st.integers(0, 9), max_size=4))
def test_nil_lines_never_change_the_result(s4, at):
    # oracle: requirement 3: a nil borrowing line (its own concept) anywhere is no part, so
    # Filer H stays 53,400 (M) with the same parts
    lines = s4.filer_h()
    for n, i in enumerate(at):
        lines.insert(i, s4.line("Short-term debt", f"ext:NilBorrowings{n}", s4.NIL))
    f = s4.build(lines, [])
    m = s4.system("Debt", f)
    assert m["value"] == pytest.approx(53_400 * s4.M, rel=1e-12)  # whole millions: exact
    assert [p["concept"] for p in m["parts"]] == [
        "us-gaap:CommercialPaper", "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent",
        "us-gaap:LongTermDebtAndCapitalLeaseObligations"]


@pytest.mark.criterion("C5")
@given(decs=st.lists(st.sampled_from([-9, -6, -5, -3, 0, 2, None, "INF"]), min_size=3,
                     max_size=3))
def test_decimals_is_the_minimum_finite_part_decimals(s4, decs):
    # oracle: requirement 7 by hand: the smallest int among the three parts' decimals, None
    # when none is finite
    lines = s4.filer_h()
    concepts = ["us-gaap:CommercialPaper", "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent",
                "us-gaap:LongTermDebtAndCapitalLeaseObligations"]
    for c, d in zip(concepts, decs, strict=True):
        lines = _with(lines, c, decimals=d)
    m = s4.system("Debt", s4.build(lines, []))
    finite = [d for d in decs if isinstance(d, int)]
    assert m["decimals"] == (min(finite) if finite else None)
    assert m["value"] == pytest.approx(53_400 * s4.M, rel=1e-12)  # whole millions: exact
