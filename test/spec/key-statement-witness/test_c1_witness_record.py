"""C1: ``statement_witness`` returns the witness record of requirement 1.

Oracle: the fixtures' raw ``pre``/``num`` rows (split by the conftest, not by the code under
test) and the rendered statements, checked by hand (spec Notes, "Hand checks").
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import statement_witness

# tolerance: FSDS and companyfacts carry the filed integer amounts; rel 1e-12 absorbs only
# binary floating point, never a wrong figure.
REL = 1e-12

FY25 = ("2024-09-29", "2025-09-27")  # AAPL fiscal 2025 as companyfacts writes it


def _aapl_opex(kit):
    return kit.key("AAPL", "us-gaap:OperatingExpenses", kit.AAPL, FY25[1], FY25[0], 2025)


@pytest.mark.criterion("C1")
def test_presented_key_record(kit):
    # oracle: AAPL FY2025 10-K income statement, "Total operating expenses" 62,151 (millions),
    # hand-checked; FSDS pre row report 3 line 13 and its num row (ddate 20250930, qtrs 4)
    e = kit.env()
    w = statement_witness(e.tables, e.rules, e.notes, "OperatingExpenses", _aapl_opex(kit),
                          kit.AAPL)
    pre = kit.raw_pre(kit.AAPL, 3, 13)
    raw = kit.raw_value(kit.AAPL, "OperatingExpenses", "us-gaap/2025", "20250930", "4", "USD")
    assert pre["tag"] == "OperatingExpenses" and pre["plabel"] == "Total operating expenses"
    assert raw == pytest.approx(62_151_000_000.0, rel=REL)
    assert w == {
        "status": "agree", "family": "OperatingExpenses", "cik": "0000320193", "fiscal_year": 2025,
        "adsh": kit.AAPL,
        "lines": [{"adsh": kit.AAPL, "stmt": "IS", "report": 3, "line": 13,
                   "tag": "OperatingExpenses", "version": "us-gaap/2025",
                   "concept": "us-gaap:OperatingExpenses", "plabel": "Total operating expenses",
                   "negating": False, "value": pytest.approx(raw, rel=REL)}],
        "value": pytest.approx(62_151_000_000.0, rel=REL),
        "key_concept": "us-gaap:OperatingExpenses",
        "key_value": pytest.approx(62_151_000_000.0, rel=REL), "key_accession": kit.AAPL,
        "substitution": None, "reason": None, "resolution": None,
    }


@pytest.mark.criterion("C1")
def test_negating_line_keeps_the_tagged_value(kit):
    # oracle: AMZN FY2025 10-K income statement "Interest expense" printed (2,274); FSDS pre
    # report 4 line 18 has negating = 1 and num carries +2,274,000,000 (readme: value as tagged)
    e = kit.env()
    k = kit.key("AMZN", "us-gaap:InterestExpenseNonoperating", kit.AMZN, "2025-12-31",
                "2025-01-01", 2025)
    w = statement_witness(e.tables, e.rules, e.notes, "InterestExpense", k, kit.AMZN)
    assert kit.raw_pre(kit.AMZN, 4, 18)["negating"] == "1"
    assert w["status"] == "agree"
    assert [(ln["report"], ln["line"], ln["negating"]) for ln in w["lines"]] == [(4, 18, True)]
    assert w["lines"][0]["value"] == pytest.approx(2_274_000_000.0, rel=REL)
    assert w["value"] == pytest.approx(2_274_000_000.0, rel=REL)


@pytest.mark.criterion("C1")
def test_note_only_key_record(kit):
    # oracle: ABT FY2025 10-K: the income statement has "Earnings before Taxes" and "Taxes on
    # Earnings" but no rate line; the tax note prints "Effective tax rate on earnings" 14.1% for
    # 2023 (hand check); companyfacts EffectiveIncomeTaxRateContinuingOperations 0.141
    e = kit.env()
    k = kit.key("ABT", "us-gaap:EffectiveIncomeTaxRateContinuingOperations", kit.ABT, "2023-12-31",
                "2023-01-01", 2023, unit="pure")
    w = statement_witness(e.tables, e.rules, e.notes, "EffectiveTaxRate", k, kit.ABT)
    assert w["status"] == "note_witnessed"
    assert w["lines"] == []
    assert w["value"] is None
    assert w["adsh"] == kit.ABT
    assert w["key_value"] == pytest.approx(0.141, rel=REL)
    assert (w["reason"], w["substitution"], w["resolution"]) == (None, None, None)


@pytest.mark.criterion("C1")
def test_missing_filing_is_no_fsds(kit):
    # oracle: requirement 1 step 2; the accession is in no fixture quarter's sub.tsv
    e = kit.env()
    assert all(r["adsh"] != kit.ABSENT for r in kit.raw_rows("sub"))
    k = kit.key("AAPL", "us-gaap:OperatingExpenses", kit.AAPL, FY25[1], FY25[0], 2025)
    k["accession"] = kit.ABSENT  # constructed: a 10-K newer than the FSDS data
    w = statement_witness(e.tables, e.rules, e.notes, "OperatingExpenses", k, kit.ABSENT)
    assert (w["status"], w["adsh"], w["lines"], w["value"]) == ("no_fsds", kit.ABSENT, [], None)
    assert w["key_accession"] == kit.ABSENT


@pytest.mark.criterion("C1")
def test_unknown_family_raises(kit):
    # oracle: requirement 1, "A family with no rule raises ValueError"
    e = kit.env()
    with pytest.raises(ValueError):
        statement_witness(e.tables, e.rules, e.notes, "NoSuchFamily", _aapl_opex(kit), kit.AAPL)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_line_fields_equal_the_raw_rows_for_every_presented_fixture_key(kit):
    # oracle: each WitnessLine's fields are the raw pre row's and its value the raw num row's
    # (requirement 1); the keys are the hand-checked presented fixture keys
    e = kit.env()
    cases = [
        ("OperatingExpenses", _aapl_opex(kit), kit.AAPL, "20250930", "4"),
        ("Revenue", kit.key("VST", "us-gaap:Revenues", kit.VST, "2023-12-31", "2023-01-01", 2023),
         kit.VST, "20231231", "4"),
        ("PropertyPlantAndEquipment",
         kit.key("LMT", "us-gaap:PropertyPlantAndEquipmentNet", kit.LMT, "2024-12-31", None, 2024),
         kit.LMT, "20241231", "0"),
        ("Debt", kit.key("AAPL", "us-gaap:LongTermDebt", kit.AAPL, "2025-09-27", None, 2025),
         kit.AAPL, "20250930", "0"),
    ]
    for family, k, adsh, ddate, qtrs in cases:
        w = statement_witness(e.tables, e.rules, e.notes, family, k, adsh)
        assert w["lines"], family
        for ln in w["lines"]:
            pre = kit.raw_pre(adsh, ln["report"], ln["line"])
            assert (ln["adsh"], ln["stmt"], ln["tag"], ln["version"], ln["plabel"]) == (
                pre["adsh"], pre["stmt"], pre["tag"], pre["version"], pre["plabel"])
            assert ln["negating"] is (pre["negating"] == "1")
            assert ln["concept"] == "us-gaap:" + pre["tag"]
            raw = kit.raw_value(adsh, pre["tag"], pre["version"], ddate, qtrs, "USD")
            assert ln["value"] == pytest.approx(raw, rel=REL)


@pytest.mark.criterion("C1")
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_row_order_does_not_change_the_record(kit, seed):
    # oracle: the unshuffled record of the same key (requirement 2: lines are taken in
    # (report, line) order, so table row order cannot matter)
    e = kit.env()
    fam, k = "OperatingExpenses", _aapl_opex(kit)
    base = statement_witness(e.tables, e.rules, e.notes, fam, k, kit.AAPL)
    rng = random.Random(seed)
    shuffled = {name: rng.sample(rows, len(rows)) for name, rows in e.tables.items()}
    got = statement_witness(shuffled, e.rules, e.notes, fam, k, kit.AAPL)
    assert got == base
    assert got["status"] == "agree"
