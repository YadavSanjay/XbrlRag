"""C3: the element list is general: every listed element counts, nothing left out does.

Oracle: requirements 2-3 of docs/specs/debt-ifrs-elements.md; synthetic IFRS balance sheets
built in conftest.py (made-up accessions, hand-chosen values summed in the tests); for the
us-gaap check, key-definitions' hand-checked fixture filings and its locked rule.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# tolerance: integer amounts; rel 1e-12 absorbs only binary floating point of the sum.
REL = 1e-12
NEUTRAL = "Item"   # a label the Debt label rule never takes (no include term)


def _select(kit, adsh, tables, unit="EUR"):
    from src.eval.keys.statement_witness import select_lines

    return select_lines(tables, kit.env().live["Debt"], adsh, None, "2024-12-31", unit)


@pytest.mark.criterion("C3")
def test_each_listed_element_counts_on_a_neutral_label(kit):
    # oracle: requirement 2 - one synthetic balance sheet presenting every listed element
    # (labelled "Item", values 1,000, 1,001, ...) and every left-out element (labelled "Item",
    # values 5,000, ...): the live Debt rule selects exactly the 29 listed lines; the sum is
    # 29 * 1,000 + (0 + 1 + ... + 28) = 29,406
    rows = [(e.split(":")[1], "ifrs/2024", NEUTRAL, 1000.0 + i)
            for i, e in enumerate(kit.listed)]
    rows += [(e.split(":")[1], "ifrs/2024", NEUTRAL, 5000.0 + i)
             for i, e in enumerate(kit.excluded)]
    adsh, t = kit.synthetic(1, rows)
    got = _select(kit, adsh, t)
    assert [ln["concept"] for ln in got] == kit.listed
    assert sum(ln["value"] for ln in got) == pytest.approx(29_406.0, rel=REL)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("version", ["ext", "us-gaap/2024"])
def test_the_same_local_name_outside_ifrs_full_never_counts_by_element(kit, version):
    # oracle: requirement 2 and statement-rules-by-element requirement 2 - an extension line
    # or a us-gaap line whose local name equals a listed element is not that element; on a
    # neutral label only the ifrs-full line (300) is selected
    rows = [("CurrentPortionOfLongtermBorrowings", version, NEUTRAL, 100.0),
            ("Borrowings", version, NEUTRAL, 200.0),
            ("LongtermBorrowings", "ifrs/2024", NEUTRAL, 300.0)]
    adsh, t = kit.synthetic(2, rows)
    got = _select(kit, adsh, t)
    assert [(ln["concept"], ln["value"]) for ln in got] == [
        ("ifrs-full:LongtermBorrowings", pytest.approx(300.0, rel=REL))]


@pytest.mark.criterion("C3")
@settings(max_examples=60, deadline=None)
@given(data=st.data())
def test_any_mix_of_listed_and_left_out_elements_sums_the_listed_ones(kit, data):
    # oracle: requirement 3 (property) - for any non-empty set of listed elements and any set
    # of left-out ones, on neutral or arbitrary non-total labels in any order, the selected
    # lines are exactly the listed ones and the key is their hand sum
    listed = data.draw(st.lists(st.sampled_from(kit.listed), min_size=1, unique=True))
    left_out = data.draw(st.lists(st.sampled_from(kit.excluded), unique=True))
    labels = st.sampled_from([NEUTRAL, "Others", "Financial liabilities", "Lease liabilities",
                              "Deposits received", "Long-term liabilities - current portion"])
    rows = [(e.split(":")[1], "ifrs/2024", data.draw(labels),
             float(data.draw(st.integers(1, 10**9)))) for e in listed + left_out]
    order = data.draw(st.permutations(range(len(rows))))
    rows = [rows[i] for i in order]
    adsh, t = kit.synthetic(3, rows)
    got = _select(kit, adsh, t)
    want = [r for r in rows if "ifrs-full:" + r[0] in listed]
    assert sorted(ln["concept"] for ln in got) == sorted("ifrs-full:" + r[0] for r in want)
    assert sum(ln["value"] for ln in got) == pytest.approx(sum(r[3] for r in want), rel=REL)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_us_gaap_filings_do_not_move(kit):
    # oracle: requirement 3 (metamorphic) - the element list names only ifrs-full elements, so
    # on key-definitions' us-gaap filings (AAPL, KO, WMT, TSLA, MCD, LLY, AMZN, GOOGL FY2025)
    # the live Debt rule selects exactly the lines key-definitions' locked rule selects
    from src.eval.keys.statement_witness import select_lines

    e = kit.env()
    assert e.live["Debt"].get("elements") == kit.listed
    for ticker, (adsh, end) in kit.KD_FILINGS.items():
        old = select_lines(e.kd_tables, e.kd["Debt"], adsh, None, end, "USD")
        new = select_lines(e.kd_tables, e.live["Debt"], adsh, None, end, "USD")
        assert old, ticker
        assert [(ln["report"], ln["line"]) for ln in new] == [
            (ln["report"], ln["line"]) for ln in old], ticker
