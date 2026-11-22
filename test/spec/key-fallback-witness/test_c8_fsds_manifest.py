"""C8: the manifest records the not-published keys and the FSDS coverage.

Oracle: requirements 4-5 of docs/specs/key-fallback-witness.md; hand-written gaps and the
conftest's FILINGS (FALL's FY2025 10-K and FNOP's FY2025 10-K are in no quarter).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.v2_suite import build_v2_suites
from src.eval.suites.v2_witness import WitnessDrop, fsds_gap_manifest

FIN = "us-gaap:NetCashProvidedByUsedInFinancingActivities"
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
COVERAGE = {"n": 3, "first": "2023q1", "last": "2025q1",
            "missing": ["2023q2", "2023q3", "2023q4", "2024q2", "2024q3", "2024q4"]}


def _gap(family, cik, year, accession):
    return {"family": family, "cik": cik, "fiscal_year": year, "accession": accession}


@pytest.mark.criterion("C8")
def test_gap_manifest_deduplicates_counts_and_sorts():
    # oracle: four hand-written gaps, one repeated: three distinct, two of family Capex,
    # sorted by (family, cik, fiscal_year, accession)
    a = _gap("Capex", "0000810005", 2025, "0000810005-26-000050")
    b = _gap("FinancingCashFlow", "0000810001", 2025, "0000810001-26-000010")
    c = _gap("Capex", "0000810001", 2025, "0000810001-26-000010")
    got = fsds_gap_manifest(COVERAGE, [b, a, c, dict(a)])
    assert got == {"coverage": COVERAGE, "not_published": {
        "n": 3, "by_family": {"Capex": 2, "FinancingCashFlow": 1}, "keys": [c, a, b]}}
    assert fsds_gap_manifest(COVERAGE, []) == {
        "coverage": COVERAGE, "not_published": {"n": 0, "by_family": {}, "keys": []}}


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("bad", [
    {"family": "Capex", "cik": "0000810005", "fiscal_year": 2025},
    {"family": "Capex", "cik": "0000810005", "fiscal_year": 2025,
     "accession": "0000810005-26-000050", "ticker": "FNOP"}])
def test_a_malformed_gap_raises(bad):
    # oracle: requirement 5: a gap has exactly family, cik, fiscal_year and accession
    with pytest.raises(ValueError):
        fsds_gap_manifest(COVERAGE, [bad])


@pytest.mark.criterion("C8")
def test_the_witness_and_the_build_report_the_gaps_and_coverage(kit):
    # oracle: the two not-published inputs (FALL FY2025 financing on its FY2025 10-K, FNOP
    # FY2025 capex on its FY2025 10-K) and the fixture's three quarters
    w = kit.witness()
    for ticker, family, concept in (("FALL", "FinancingCashFlow", FIN),
                                    ("FNOP", "Capex", CAPEX)):
        with pytest.raises(WitnessDrop):
            w.fy_key(kit.companyfacts(ticker), kit.labels(ticker), kit.CIK[ticker], family,
                     kit.spec(concept), 2025)
    want = {"coverage": COVERAGE, "not_published": {
        "n": 2, "by_family": {"Capex": 1, "FinancingCashFlow": 1},
        "keys": [_gap("Capex", kit.CIK["FNOP"], 2025, kit.FN25),
                 _gap("FinancingCashFlow", kit.CIK["FALL"], 2025, kit.FA25)]}}
    assert w.fsds_manifest() == want

    built = kit.witness()
    build = build_v2_suites(_inventory(kit, ["FALL", "FNOP"]), {}, kit.FakeSec(), kit.NoStore(),
                            None, None, quotas={"lookup": 10}, per_intent=1, share=0.5,
                            salt="kfw", witness=built)
    assert build["manifest"]["fsds"] == want
    plain = build_v2_suites(_inventory(kit, ["FALL", "FNOP"]), {}, kit.FakeSec(), kit.NoStore(),
                            None, None, quotas={}, per_intent=1, share=0.5, salt="kfw")
    assert "fsds" not in plain["manifest"]


def _inventory(kit, tickers):
    rows = {"FALL": ("FinancingCashFlow", FIN), "FNOP": ("Capex", CAPEX)}
    return {t: {"cik": kit.CIK[t], "years": {2025: {rows[t][0]: kit.spec(rows[t][1])}}}
            for t in tickers}


@pytest.mark.criterion("C8")
@settings(max_examples=8, deadline=None)
@given(tickers=st.sets(st.sampled_from(["FALL", "FNOP"])))
def test_drops_and_gaps_count_the_inventory(kit, tickers):
    # oracle: each not-published lookup in the inventory is one drop and one gap
    w = kit.witness()
    build = build_v2_suites(_inventory(kit, sorted(tickers)), {}, kit.FakeSec(), kit.NoStore(),
                            None, None, quotas={"lookup": 10}, per_intent=1, share=0.5,
                            salt="kfw", witness=w)
    dropped = build["manifest"]["intents"]["dropped"]
    fsds = build["manifest"].get("fsds") or {}
    gaps = (fsds.get("not_published") or {})
    family = {"FALL": "FinancingCashFlow", "FNOP": "Capex"}
    got = (dropped.get("fsds_not_published", 0), sum(dropped.values()), gaps.get("n"),
           [g.get("cik") for g in gaps.get("keys", [])], (fsds.get("coverage") or {}).get("last"))
    want = (len(tickers), len(tickers), len(tickers),
            [kit.CIK[t] for t in sorted(tickers, key=lambda t: family[t])], "2025q1")
    assert got == want
