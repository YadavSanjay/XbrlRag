"""C1: an annual key whose filing has no FSDS data is witnessed on its own annual report.

Oracle: requirement 1 of docs/specs/key-fallback-witness.md and the conftest's hand-written
rows (the FY2024 10-K of FALL presents "Net cash used in financing activities" -10,764 on
report 4 line 3; the FY2025 10-K that re-reports it is not in the FSDS data).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import statement_witness

# tolerance: the fixture amounts are whole millions; rel 1e-12 absorbs only binary floating
# point, never a wrong figure.
REL = 1e-12
FIN = "us-gaap:NetCashProvidedByUsedInFinancingActivities"
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
TENQ = "0000810001-25-000020"  # a made-up FY2025 10-Q of FALL, in no fixture table


def _key(kit, concept, millions, accession, year=2024, form="10-K"):
    start, end = kit.period(year)
    return {"concept": concept, "unit": "USD", "period_start": start, "period_end": end,
            "value": float(millions * kit.M), "accession": accession, "form": form,
            "cik": kit.CIK["FALL"], "fiscal_year": year}


@pytest.mark.criterion("C1")
def test_a_key_from_an_unpublished_10k_is_witnessed_on_the_own_report(kit):
    # oracle: FALL FY2024 financing -10,764 in both 10-Ks (conftest FACTS); the FY2025 10-K
    # is in no quarter, the FY2024 10-K presents CF report 4 line 3 at -10,764 (conftest LINES)
    e = kit.env()
    k = _key(kit, FIN, -10764, kit.FA25)
    w = statement_witness(kit.all_tables(), e.rules, e.notes, "FinancingCashFlow", k,
                          kit.FA24, -10764.0 * kit.M)
    assert w["status"] == "agree"
    assert w["adsh"] == kit.FA24
    assert w.get("fallback") == "own_annual_report"
    assert (w["key_accession"], w["key_concept"]) == (kit.FA25, FIN)
    assert w["key_value"] == pytest.approx(-10_764e6, rel=REL)
    assert w["value"] == pytest.approx(-10_764e6, rel=REL)
    assert [(ln["adsh"], ln["stmt"], ln["report"], ln["line"], ln["concept"], ln["plabel"])
            for ln in w["lines"]] == [
        (kit.FA24, "CF", 4, 3, FIN, "Net cash used in financing activities")]
    assert w["lines"][0]["value"] == pytest.approx(-10_764e6, rel=REL)
    assert (w["reason"], w["substitution"], w["resolution"]) == (None, None, None)


@pytest.mark.criterion("C1")
def test_a_restated_value_on_a_fallback_is_value_recast(kit):
    # oracle: FALL FY2024 capex 500 in the FY2024 10-K, restated to 520 by the unpublished
    # FY2025 10-K: annual_value 500 is not the key's 520 (requirement 1 step 3)
    e = kit.env()
    k = _key(kit, CAPEX, 520, kit.FA25)
    w = statement_witness(kit.all_tables(), e.rules, e.notes, "Capex", k, kit.FA24,
                          500.0 * kit.M)
    assert (w["status"], w["adsh"], w.get("fallback")) == ("value_recast", kit.FA24,
                                                       "own_annual_report")
    assert w["value"] == pytest.approx(500e6, rel=REL)
    assert w["key_value"] == pytest.approx(520e6, rel=REL)


@pytest.mark.criterion("C1")
def test_own_accession_present_is_read_and_absent_everywhere_is_no_fsds(kit):
    # oracle: requirement 1 step 1 (own accession with a submission is read, unchanged) and
    # step 2 (nothing to read: no_fsds on the key's accession, no fallback field); the FY2024
    # 10-K presents -10,764, and the FY2025 10-K is in no table
    e = kit.env()
    own = statement_witness(kit.all_tables(), e.rules, e.notes, "FinancingCashFlow",
                            _key(kit, FIN, -10764, kit.FA24), kit.FA24, -10764.0 * kit.M)
    assert (own["status"], own["adsh"], "fallback" in own) == ("agree", kit.FA24, False)
    gone = statement_witness(kit.all_tables(), e.rules, e.notes, "FinancingCashFlow",
                             _key(kit, FIN, -9000, kit.FA25, year=2025), kit.FA25,
                             -9000.0 * kit.M)
    assert (gone["status"], gone["adsh"], gone["lines"], gone["value"]) == (
        "no_fsds", kit.FA25, [], None)
    assert "fallback" not in gone
    # the own report absent too: the fallback has nothing to read
    none = statement_witness(kit.tables(kit.FE24), e.rules, e.notes, "FinancingCashFlow",
                             _key(kit, FIN, -10764, kit.FA25), kit.FA24, -10764.0 * kit.M)
    assert (none["status"], none["adsh"], "fallback" in none) == ("no_fsds", kit.FA25, False)
    # and the record of a fallback read is the own-accession record plus the fallback field
    fb = statement_witness(kit.all_tables(), e.rules, e.notes, "FinancingCashFlow",
                           _key(kit, FIN, -10764, kit.FA25), kit.FA24, -10764.0 * kit.M)
    assert set(fb) == set(own) | {"fallback"}


def _tables(kit, own_present, own_line, key_present, key_line):
    """FALL's FY2024 10-K with a financing line of ``own_line`` (millions) and, when asked,
    the FY2025 10-K with a FY2024 financing line of ``key_line``."""
    out = {"sub": [], "pre": [], "num": [], "tag": []}
    line = ("CF", 4, 3, "NetCashProvidedByUsedInFinancingActivities", "us-gaap",
            "Net cash used in financing activities")
    if own_present:
        rows = kit.fsds_rows(kit.FA24, [(*line, {2024: own_line})])
        for name in ("sub", "pre", "num"):
            out[name].extend(rows[name])
    if key_present:
        rows = kit.fsds_rows(kit.FA25, [(*line, {2024: key_line})])
        for name in ("sub", "pre", "num"):
            out[name].extend(rows[name])
    return out


AMOUNTS = st.integers(min_value=-3, max_value=3)


@pytest.mark.criterion("C1")
@given(key_value=AMOUNTS, own_line=AMOUNTS, key_line=AMOUNTS,
       annual=st.one_of(st.none(), st.just("same"), AMOUNTS),
       form=st.sampled_from(["10-K", "10-Q"]), key_present=st.booleans(),
       own_present=st.booleans())
def test_filing_read_status_and_marker_follow_the_decision_table(
        kit, key_value, own_line, key_line, annual, form, key_present, own_present):
    # oracle: requirement 1's decision table, restated here: an interim key reads the annual
    # report (recast check); an annual key reads its own accession when present, else the
    # own report (fallback, recast check), else nothing (no_fsds on its accession)
    e = kit.env()
    accession = kit.FA25 if form == "10-K" else TENQ
    k = _key(kit, FIN, key_value, accession, form=form)
    annual_value = (None if annual is None
                    else float(key_value * kit.M) if annual == "same" else float(annual * kit.M))
    t = _tables(kit, own_present, own_line, key_present and form == "10-K", key_line)
    w = statement_witness(t, e.rules, e.notes, "FinancingCashFlow", k, kit.FA24, annual_value)

    recast = annual_value is not None and annual_value != key_value * kit.M
    if form == "10-Q":
        read, fallback, line = kit.FA24, False, own_line
        present, check = own_present, True
    elif key_present:
        read, fallback, line, present, check = kit.FA25, False, key_line, True, False
    elif own_present:
        read, fallback, line, present, check = kit.FA24, True, own_line, True, True
    else:
        read, fallback, line, present, check = kit.FA25, False, None, False, False
    if not present:
        want = "no_fsds"
    elif check and recast:
        want = "value_recast"
    else:
        want = "agree" if line == key_value else "disagree"
    marker = "own_annual_report" if fallback else None
    assert (w["adsh"], w["status"], w.get("fallback"), "fallback" in w, w["key_accession"]) == (
        read, want, marker, fallback, accession)
