"""C2: the note_total reading of a Debt key, only where the face shows no current debt line.

Oracle: requirement 2 step 5 of docs/specs/key-definitions.md; the printed balance-sheet lines
and debt-note totals of the fixture 10-Ks, hand-checked (spec Notes).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

SAME = 1e-9  # filed values; only float rounding may differ


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("ticker", "key", "note"), [
    # oracle: AMZN FY2025 balance sheet "Long-term debt" 65,648 (no current debt line); debt
    # note "Total face value of long-term debt" 68,836 (us-gaap:LongTermDebt)
    ("AMZN", 65_648e6, 68_836e6),
    # oracle: GOOGL FY2025 balance sheet "Long-term debt" 46,547 (no current debt line); debt
    # note "Total" 49,085 (us-gaap:LongTermDebt)
    ("GOOGL", 46_547e6, 49_085e6),
])
def test_note_total_reading_where_no_current_line(kit, ticker, key, note):
    # oracle: the printed figures in the parametrize comments
    d = kit.debt(ticker, "2025-12-31", 2025)
    assert d["value"] == pytest.approx(key, rel=SAME)
    assert [c["section"] for c in d["components"]] == ["noncurrent"]
    assert len(d["readings"]) == 1
    r = d["readings"][0]
    assert r["reading"] == "note_total"
    assert r["value"] == pytest.approx(note, rel=SAME)
    assert r["concept"] == "us-gaap:LongTermDebt"
    assert (r["period_end"], r["accession"]) == ("2025-12-31", kit.FILINGS[ticker][1])


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("ticker", "end", "key"), [
    # oracle: AAPL presents current debt lines (commercial paper, current term debt): 98,657
    ("AAPL", "2025-09-27", 98_657e6),
    # oracle: LLY presents a current line; its "Total debt" 42,503 equals the key
    ("LLY", "2025-12-31", 42_503e6),
    # oracle: MCD presents no current line, but its LongTermDebt 39,973 equals the key
    ("MCD", "2025-12-31", 39_973e6),
])
def test_no_reading_with_a_current_line_or_an_equal_note(kit, ticker, end, key):
    # oracle: requirement 2 step 5 — no reading when a current debt line is presented, or
    # when the note figure is not larger than the key
    d = kit.debt(ticker, end, 2025)
    assert d["status"] == "defined"
    assert d["value"] == pytest.approx(key, rel=SAME)
    assert d["readings"] == []


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_combined_element_is_preferred_and_a_current_line_removes_the_reading(kit):
    # oracle: requirement 2 step 5 — DebtLongtermAndShorttermCombinedAmount wins over
    # LongTermDebt when both are tagged; adding a presented current debt line above "Total
    # current liabilities" removes the reading and enters the key
    cf = kit.copy(kit.companyfacts("AMZN"))
    [own] = [x for x in kit.cf_entries(cf, "LongTermDebt") if x["end"] == "2025-12-31"]
    entry = dict(own, val=70_000_000_000)
    cf["facts"]["us-gaap"]["DebtLongtermAndShorttermCombinedAmount"] = {"units": {"USD": [entry]}}
    d = kit.debt("AMZN", "2025-12-31", 2025, cf=cf)
    assert [(r["reading"], r["concept"], r["value"]) for r in d["readings"]] == [
        ("note_total", "us-gaap:DebtLongtermAndShorttermCombinedAmount", 70_000_000_000)]
    t = kit.tables_copy()
    kit.add_line(t, kit.FILINGS["AMZN"][1], 7, 16, "Short-term debt", "SyntheticShortTermDebt",
                 "20251231", 3_203e6)
    d2 = kit.debt("AMZN", "2025-12-31", 2025, tables=t)
    assert d2["readings"] == []
    assert d2["value"] == pytest.approx(65_648e6 + 3_203e6, rel=SAME)


@pytest.mark.criterion("C2")
@settings(max_examples=25, deadline=None)
@given(millions=st.integers(min_value=1, max_value=200_000))
def test_reading_exists_exactly_when_the_note_exceeds_the_key(kit, millions):
    # oracle: requirement 2 step 5 — for AMZN (no current line, key 65,648), a reading exists
    # iff the note figure is larger than the key, and it carries the note figure
    note = millions * 1_000_000
    cf = kit.copy(kit.companyfacts("AMZN"))
    for e in kit.cf_entries(cf, "LongTermDebt"):
        e["val"] = note
    d = kit.debt("AMZN", "2025-12-31", 2025, cf=cf)
    assert d["value"] == pytest.approx(65_648e6, rel=SAME)
    if note > 65_648e6:
        assert [r["value"] for r in d["readings"]] == [note]
    else:
        assert d["readings"] == []
