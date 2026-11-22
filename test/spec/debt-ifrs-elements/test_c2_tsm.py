"""C2: TSM's FY2024 Debt key is its three borrowing lines, the current portion by element.

Oracle: TSM's FY2024 20-F consolidated balance sheet (NT$ millions, read by hand from the
primary document d896993d20f.htm; spec Notes, "Hand checks"): "Long-term liabilities - current
portion" 9,293.3 (2023) / 59,857.9 (2024) / US$1,825.5; "Bonds payable" 913,899.8 / 926,604.5
/ US$28,258.7; "Long-term bank loans" 4,383.0 / 31,824.4 / US$970.5; "Lease liabilities"
(non-current) 28,681.8 / 28,755.3. FSDS report 2, lines 38, 42, 43 and 45; the first sits
above "Total current liabilities" (line 40). key-definitions requirement 2 for the key record.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# tolerance: filed amounts with one decimal in millions; rel 1e-12 absorbs only binary
# floating point of the sum.
REL = 1e-12
CURRENT = "ifrs-full:CurrentPortionOfLongtermBorrowings"
BONDS = "ifrs-full:NoncurrentPortionOfNoncurrentBondsIssued"
LOANS = "ifrs-full:LongtermBorrowings"
COLUMNS = (
    ("2024-12-31", "TWD", (59_857.9e6, 926_604.5e6, 31_824.4e6), 1_018_286.8e6),
    ("2023-12-31", "TWD", (9_293.3e6, 913_899.8e6, 4_383.0e6), 927_576.1e6),
    ("2024-12-31", "USD", (1_825.5e6, 28_258.7e6, 970.5e6), 31_054.7e6),
)


def _debt_key(kit, tables=None, rules=None, end="2024-12-31", year=2024, unit="TWD"):
    from src.eval.keys.key_definitions import debt_key

    e = kit.env()
    return debt_key(tables if tables is not None else e.tables,
                    rules if rules is not None else e.live, kit.companyfacts(), kit.TSM_CIK,
                    year, kit.TSM, end, unit)


@pytest.mark.criterion("C2")
def test_live_debt_rule_selects_tsm_three_borrowing_lines(kit):
    # oracle: hand check (module docstring) - 59,857.9 + 926,604.5 + 31,824.4 = 1,018,286.8;
    # 9,293.3 + 913,899.8 + 4,383.0 = 927,576.1; US$ 1,825.5 + 28,258.7 + 970.5 = 31,054.7
    from src.eval.keys.statement_witness import select_lines

    e = kit.env()
    for end, unit, values, total in COLUMNS:
        got = select_lines(e.tables, e.live["Debt"], kit.TSM, None, end, unit)
        assert [(ln["concept"], ln["line"]) for ln in got] == [
            (CURRENT, 38), (BONDS, 42), (LOANS, 43)]
        assert [ln["value"] for ln in got] == [pytest.approx(v, rel=REL) for v in values]
        assert sum(ln["value"] for ln in got) == pytest.approx(total, rel=REL)


@pytest.mark.criterion("C2")
def test_tsm_debt_key_is_defined_with_the_current_portion(kit):
    # oracle: hand check and key-definitions requirement 2 - status defined, basis presented,
    # 1,018,286.8 (NT$ millions) in TWD; line 38 is current (above line 40), 42 and 43
    # non-current; none names a lease; every value from the filing fact; no note reading
    d = _debt_key(kit)
    assert (d["status"], d["basis"], d["definition"]) == ("defined", "presented", "debt_total")
    assert d["value"] == pytest.approx(1_018_286.8e6, rel=REL)
    assert [(c["concept"], c["section"], c["line"], c["lease_inclusive"])
            for c in d["components"]] == [(CURRENT, "current", 38, False),
                                          (BONDS, "noncurrent", 42, False),
                                          (LOANS, "noncurrent", 43, False)]
    assert d["readings"] == []
    k = d["key"]
    assert (k["concept"], k["unit"], k["accession"], k["period_end"], k["fiscal_year"]) == (
        "s4:debt_total", "TWD", kit.TSM, "2024-12-31", 2024)
    assert k["value"] == pytest.approx(1_018_286.8e6, rel=REL)
    assert k["key_source"] == "sec_companyfacts"
    prior = _debt_key(kit, end="2023-12-31", year=2023)
    assert prior["value"] == pytest.approx(927_576.1e6, rel=REL)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_without_the_elements_tsm_debt_omits_the_current_portion(kit):
    # oracle: the defect (key-definitions review point "TSM current debt") - the same rule
    # without `elements` (key-definitions' locked rule) keys 926,604.5 + 31,824.4 = 958,428.9;
    # the live rule adds exactly line 38 (59,857.9)
    e = kit.env()
    old = _debt_key(kit, rules=e.kd)
    new = _debt_key(kit)
    assert old["value"] == pytest.approx(958_428.9e6, rel=REL)
    assert new["value"] == pytest.approx(1_018_286.8e6, rel=REL)
    added = [c for c in new["components"] if c["line"] not in
             {o["line"] for o in old["components"]}]
    assert [(c["line"], c["value"]) for c in added] == [(38, pytest.approx(59_857.9e6, rel=REL))]


@pytest.mark.criterion("C2")
@settings(max_examples=40, deadline=None)
@given(label=st.text(max_size=50))
def test_tsm_key_is_invariant_to_line_labels_of_listed_elements(kit, label):
    # oracle: requirement 3 (property) - relabelling any of TSM's three borrowing lines
    # (here all three, to any text) leaves the key 1,018,286.8; unless the new label starts
    # with "total debt"/"total borrowings", which the rule's `prefer` would narrow to
    from hypothesis import assume

    assume(not label.lower().lstrip().startswith(("total debt", "total borrowings")))
    t = kit.tables_copy()
    for line in (38, 42, 43):
        kit.set_pre(t, kit.TSM, line, plabel=label)
    d = _debt_key(kit, tables=t)
    assert d["status"] == "defined"
    assert d["value"] == pytest.approx(1_018_286.8e6, rel=REL)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@pytest.mark.parametrize("label", ["Others", "Long-term borrowings", "Lease liabilities"])
def test_tsm_lease_line_never_joins_by_element(kit, label):
    # oracle: requirement 2 (lease liabilities are left out) - TSM's non-current lease line
    # (line 45, NoncurrentLeaseLiabilities, 28,755.3) relabelled to a neutral or a lease label
    # is not selected; the key stays 1,018,286.8. ("Long-term borrowings" names borrowings,
    # so the label rule takes it: 1,018,286.8 + 28,755.3 = 1,047,042.1, the label route the
    # element list does not change)
    t = kit.tables_copy()
    kit.set_pre(t, kit.TSM, 45, plabel=label)
    d = _debt_key(kit, tables=t)
    expected = 1_047_042.1e6 if label == "Long-term borrowings" else 1_018_286.8e6
    assert d["value"] == pytest.approx(expected, rel=REL)
    assert _debt_key(kit)["value"] == pytest.approx(1_018_286.8e6, rel=REL)
