"""C2: with no current debt line, the presented sum answers and the note total is disclosed."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

COMBINED = "us-gaap:DebtLongtermAndShorttermCombinedAmount"
LTD = "us-gaap:LongTermDebt"


@pytest.mark.criterion("C2")
def test_filer_g_presented_sum_with_note_total_reading(s4):
    # oracle: maintainer decision 3 and key-definitions requirement 2 step 5: Filer G presents
    # only "Long-term debt" 46,500 (M); its tagged DebtLongtermAndShorttermCombinedAmount
    # 49,000 (preferred over LongTermDebt 48,800) is larger, so it is the disclosed reading
    f = s4.build(s4.filer_g(), s4.filer_g_notes())
    m = s4.system("Debt", f)
    assert m["status"] == "defined"
    assert m["value"] == pytest.approx(46_500 * s4.M, rel=1e-12)  # one line: exact
    assert m["fact_ids"] == [f.ids["us-gaap:LongTermDebtNoncurrent"]]
    assert m["parts"][0]["section"] == "noncurrent"
    assert len(m["readings"]) == 1
    r = m["readings"][0]
    assert r["reading"] == "note_total"
    assert r["value"] == pytest.approx(49_000 * s4.M, rel=1e-12)  # the tagged value
    assert r["concept"] == COMBINED
    assert r["fact_ids"] == [f.ids[COMBINED]]
    assert "DebtLongtermAndShorttermCombinedAmount" in m["disclosure"]
    assert f.ids[COMBINED] not in m["fact_ids"]


@pytest.mark.criterion("C2")
def test_no_reading_when_a_current_line_is_presented(s4):
    # oracle: requirement 5 step 3: Filer H presents current debt lines, so its debt note
    # (LongTermDebt 51,400, below nothing it states) is never a reading
    m = s4.system("Debt", s4.build(s4.filer_h(), s4.filer_h_notes()))
    assert m["value"] == pytest.approx(53_400 * s4.M, rel=1e-12)  # whole millions: exact
    assert m["readings"] == []


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("combined", [46_500, 45_000])
def test_no_reading_when_the_note_is_not_larger(s4, combined):
    # oracle: requirement 5 step 3: the first present note tag (the combined amount) decides;
    # equal (46,500) or smaller (45,000) than the presented 46,500 gives no reading, and
    # LongTermDebt 48,800 is not consulted
    notes = [s4.note(COMBINED, combined * s4.M), s4.note(LTD, 48_800 * s4.M)]
    m = s4.system("Debt", s4.build(s4.filer_g(), notes))
    assert m["value"] == pytest.approx(46_500 * s4.M, rel=1e-12)  # one line: exact
    assert m["readings"] == []


@pytest.mark.criterion("C2")
def test_long_term_debt_when_no_combined_amount(s4):
    # oracle: requirement 5 step 3 order: without the combined amount, LongTermDebt 48,800 >
    # 46,500 is the reading
    f = s4.build(s4.filer_g(), [s4.note(LTD, 48_800 * s4.M)])
    m = s4.system("Debt", f)
    assert [(r["reading"], r["concept"]) for r in m["readings"]] == [("note_total", LTD)]
    assert m["readings"][0]["value"] == pytest.approx(48_800 * s4.M, rel=1e-12)  # tagged
    assert m["readings"][0]["fact_ids"] == [f.ids[LTD]]
    assert "LongTermDebt" in m["disclosure"]


@pytest.mark.criterion("C2")
@given(note_m=st.integers(1, 120_000), short_m=st.none() | st.integers(1, 9_000))
def test_reading_exactly_when_no_current_part_and_note_larger(s4, note_m, short_m):
    # oracle: requirement 5 step 3 by hand: value = 46,500 (+ short-term borrowings when
    # presented above Total current liabilities); a reading exists iff no current line and the
    # combined note is larger than the value (whole millions: "the same" is equality)
    lines = s4.filer_g()
    if short_m is not None:
        lines.insert(0, s4.line("Short-term borrowings", "us-gaap:ShortTermBorrowings",
                                short_m * s4.M))
    m = s4.system("Debt", s4.build(lines, [s4.note(COMBINED, note_m * s4.M)]))
    expected = 46_500 + (short_m or 0)
    assert m["value"] == pytest.approx(expected * s4.M, rel=1e-12)  # whole millions: exact
    has_reading = short_m is None and note_m > expected
    assert [r["reading"] for r in m["readings"]] == (["note_total"] if has_reading else [])
    if has_reading:
        assert m["readings"][0]["value"] == pytest.approx(note_m * s4.M, rel=1e-12)  # tagged
