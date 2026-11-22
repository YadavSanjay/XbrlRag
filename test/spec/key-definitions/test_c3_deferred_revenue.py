"""C3: a DeferredRevenue key is total deferred revenue, current plus non-current.

Oracle: requirement 3 of docs/specs/key-definitions.md; the printed balance-sheet lines and
deferred-revenue note figures of the fixture 10-Ks, hand-checked (spec Notes); the us-gaap
2025 definition of ContractWithCustomerLiability (committed taxonomy excerpt).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

SAME = 1e-9  # filed values; only float rounding may differ
CUR = "us-gaap:ContractWithCustomerLiabilityCurrent"

# ticker, period end, FY label, value, basis, presented sections, reading (None or (concept, value))
CASES = [
    # oracle: CSCO FY2025 balance sheet "Deferred revenue" current 16,416 and non-current
    # 12,363: 28,779 (the note's total is the same figure)
    ("CSCO", "2025-07-26", 2025, 28_779e6, "presented_total", ["current", "noncurrent"], None),
    # oracle: TSLA FY2025 "Deferred revenue" 3,424 and "Deferred revenue, net of current
    # portion" 3,631: 7,055; the note's rollforward "Deferred revenue - end of period" 3,867 is
    # not consulted when both parts are presented
    ("TSLA", "2025-12-31", 2025, 7_055e6, "presented_total", ["current", "noncurrent"], None),
    # oracle: ORCL FY2025 10-K, 2024-05-31: balance sheet "Deferred revenues" 9,313; note
    # "Total deferred revenues" 10,546 (9,313 + non-current 1,233 in other liabilities)
    ("ORCL", "2024-05-31", 2024, 10_546e6, "note_total", ["current"], (CUR, 9_313e6)),
    # oracle: AAPL FY2025 balance sheet "Deferred revenue" 9,055; note "total deferred
    # revenue" $13.7 billion (13,700)
    ("AAPL", "2025-09-27", 2025, 13_700e6, "note_total", ["current"], (CUR, 9_055e6)),
    # oracle: GOOGL FY2025 balance sheet "Deferred revenue" 6,578; note "Total deferred
    # revenue" $8.6 billion (8,600)
    ("GOOGL", "2025-12-31", 2025, 8_600e6, "note_total", ["current"], (CUR, 6_578e6)),
    # oracle: LMT FY2025 balance sheet "Contract liabilities" 11,440; no non-current part or
    # total is tagged in the filing
    ("LMT", "2025-12-31", 2025, 11_440e6, "no_noncurrent_reported", ["current"], None),
    # oracle: MCD FY2025 "Deferred revenues - initial franchise fees" 945, below "Total
    # current liabilities"
    ("MCD", "2025-12-31", 2025, 945e6, "presented_total", ["noncurrent"], None),
]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("ticker", "end", "fy", "value", "basis", "sections", "reading"), CASES)
def test_total_deferred_revenue(kit, ticker, end, fy, value, basis, sections, reading):
    # oracle: the printed figures in the CASES comments
    d = kit.deferred(ticker, end, fy)
    assert d["status"] == "defined"
    assert (d["family"], d["definition"]) == ("DeferredRevenue", "deferred_revenue_total")
    assert d["basis"] == basis
    assert d["value"] == pytest.approx(value, rel=SAME)
    assert [c["section"] for c in d["components"] if c["source"] == "statement"] == sections
    if reading is None:
        assert d["readings"] == []
    else:
        assert len(d["readings"]) == 1
        r = d["readings"][0]
        assert (r["reading"], r["concept"]) == ("current_presented", reading[0])
        assert r["value"] == pytest.approx(reading[1], rel=SAME)
        assert (r["period_end"], r["accession"]) == (end, kit.FILINGS[ticker][1])


@pytest.mark.criterion("C3")
def test_note_only_total(kit):
    # oracle: META FY2025 10-K, 2024-12-31: no deferred-revenue line on the balance sheet; its
    # revenue note gives contract liabilities of 772 (us-gaap:ContractWithCustomerLiability)
    d = kit.deferred("META", "2024-12-31", 2024)
    assert (d["status"], d["basis"]) == ("defined", "note_total")
    assert d["value"] == pytest.approx(772e6, rel=SAME)
    assert [(c["source"], c["section"], c["concept"]) for c in d["components"]] == [
        ("note", "note", "us-gaap:ContractWithCustomerLiability")]
    assert d["readings"] == []


@pytest.mark.criterion("C3")
def test_composite_key_record(kit):
    # oracle: requirement 3 — the key of ORCL 2024-05-31 (10,546) in the FY2025 10-K
    k = kit.deferred("ORCL", "2024-05-31", 2024)["key"]
    assert k["concept"] == "s4:deferred_revenue_total"
    assert k["value"] == pytest.approx(10_546e6, rel=SAME)
    assert (k["accession"], k["cik"]) == (kit.FILINGS["ORCL"][1], "0001341439")
    assert (k["period_start"], k["period_end"], k["fiscal_year"]) == (None, "2024-05-31", 2024)
    assert k["policies"] == ["S1", "S4", "S6"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_noncurrent_note_fact_when_no_total(kit):
    # oracle: requirement 3 step 2 — without the tagged total, ORCL's key is the presented
    # current line plus the tagged non-current part: 9,313 + 1,233 = 10,546
    cf = kit.copy(kit.companyfacts("ORCL"))
    del cf["facts"]["us-gaap"]["ContractWithCustomerLiability"]
    d = kit.deferred("ORCL", "2024-05-31", 2024, cf=cf)
    assert d["basis"] == "current_plus_note_noncurrent"
    assert d["value"] == pytest.approx(9_313e6 + 1_233e6, rel=SAME)
    assert [r["value"] for r in d["readings"]] == [pytest.approx(9_313e6, rel=SAME)]
    del cf["facts"]["us-gaap"]["ContractWithCustomerLiabilityNoncurrent"]
    d2 = kit.deferred("ORCL", "2024-05-31", 2024, cf=cf)
    assert (d2["basis"], d2["readings"]) == ("no_noncurrent_reported", [])
    assert d2["value"] == pytest.approx(9_313e6, rel=SAME)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_a_total_below_the_current_line_is_not_the_total(kit):
    # oracle: requirement 3 step 2 — a tagged total smaller than the presented current line
    # is not consulted (AAPL: total set to 9,000 < 9,055 and no non-current fact: the key is
    # the current line, no reading)
    cf = kit.copy(kit.companyfacts("AAPL"))
    for e in kit.cf_entries(cf, "ContractWithCustomerLiability"):
        e["val"] = 9_000_000_000
    d = kit.deferred("AAPL", "2025-09-27", 2025, cf=cf)
    assert (d["basis"], d["readings"]) == ("no_noncurrent_reported", [])
    assert d["value"] == pytest.approx(9_055e6, rel=SAME)


@pytest.mark.criterion("C3")
@settings(max_examples=25, deadline=None)
@given(millions=st.integers(min_value=1, max_value=100_000))
def test_presented_parts_win_and_value_never_below_current(kit, millions):
    # oracle: requirement 3 — CSCO presents both parts, so any tagged total leaves its key at
    # 28,779; for AAPL (current line only) the key is never below its 9,055 line, and a reading
    # exists exactly when the key differs from it
    total = millions * 1_000_000
    cf = kit.copy(kit.companyfacts("CSCO"))
    for e in kit.cf_entries(cf, "ContractWithCustomerLiability"):
        e["val"] = total
    assert kit.deferred("CSCO", "2025-07-26", 2025, cf=cf)["value"] == pytest.approx(
        28_779e6, rel=SAME)
    cfa = kit.copy(kit.companyfacts("AAPL"))
    for e in kit.cf_entries(cfa, "ContractWithCustomerLiability"):
        e["val"] = total
    d = kit.deferred("AAPL", "2025-09-27", 2025, cf=cfa)
    assert d["value"] >= 9_055e6
    assert (len(d["readings"]) == 1) is (d["value"] != pytest.approx(9_055e6, rel=SAME))


@pytest.mark.criterion("C3")
def test_no_line_and_no_note_is_not_presented(kit):
    # oracle: requirement 3 step 4 — KO FY2025 presents no deferred-revenue line and its
    # filing tags no contract-liability total: not_presented, no value
    d = kit.deferred("KO", "2025-12-31", 2025)
    assert (d["status"], d["value"], d["key"], d["readings"]) == ("not_presented", None, None, [])
    assert d["components"] == []
