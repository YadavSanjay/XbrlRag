"""C6: a key or member no FSDS filing can witness is excluded as ``fsds_not_published``.

Oracle: requirement 4 of docs/specs/key-fallback-witness.md and the conftest's FILINGS: FALL's
FY2025 10-K and FNOP's only 10-K are in no fixture quarter, FALL's FY2024 10-K is in 2025q1.
"""

from __future__ import annotations

import pytest

from src.eval.suites.v2_suite import build_v2_suites
from src.eval.suites.v2_witness import WitnessDrop

# tolerance: whole-million fixture amounts; rel 1e-12 absorbs only binary floating point.
REL = 1e-12
FIN = "us-gaap:NetCashProvidedByUsedInFinancingActivities"
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"


def _fy_key(w, kit, ticker, family, concept, year):
    return w.fy_key(kit.companyfacts(ticker), kit.labels(ticker), kit.CIK[ticker], family,
                    kit.spec(concept), year)


def _drop(w, kit, ticker, family, concept, year):
    with pytest.raises(WitnessDrop) as err:
        _fy_key(w, kit, ticker, family, concept, year)
    return err.value.args


@pytest.mark.criterion("C6")
def test_keys_without_any_fsds_filing_drop_as_fsds_not_published(kit):
    # oracle: FALL FY2025 financing -9,000 and FNOP FY2025 capex 300 come from 10-Ks in no
    # quarter, and those 10-Ks are their own annual reports: nothing to read. FALL FY2024
    # financing -10,764 comes from the same unpublished FY2025 10-K but its own FY2024 report
    # is in 2025q1: keyed through the fallback (requirement 1).
    w = kit.witness()
    assert _drop(w, kit, "FALL", "FinancingCashFlow", FIN, 2025) == ("fsds_not_published",)
    assert _drop(w, kit, "FNOP", "Capex", CAPEX, 2025) == ("fsds_not_published",)
    key, adsh, _v = _fy_key(w, kit, "FALL", "FinancingCashFlow", FIN, 2024)
    assert (key["accession"], adsh) == (kit.FA25, kit.FA24)
    assert key["value"] == pytest.approx(-10_764e6, rel=REL)
    assert w.blocked == []
    w.raise_if_blocked()
    held = w.manifest()["held"]
    assert held == [
        {"family": "Capex", "cik": kit.CIK["FNOP"], "fiscal_year": 2025, "key_concept": CAPEX,
         "status": "no_fsds"},
        {"family": "FinancingCashFlow", "cik": kit.CIK["FALL"], "fiscal_year": 2025,
         "key_concept": FIN, "status": "no_fsds"}]


@pytest.mark.criterion("C6")
def test_an_unkeyed_member_without_its_own_report_is_fsds_not_published(kit):
    # oracle: FNOP's FY2025 10-K (its own report) is in no quarter
    w = kit.witness()
    got = w.unkeyed_member(kit.companyfacts("FNOP"), kit.labels("FNOP"), kit.CIK["FNOP"],
                           "Capex", 2025, "USD")
    assert got == ("fsds_not_published", None)
    assert w.blocked == []


def _inventory(kit, tickers):
    rows = {"FALL": ("FinancingCashFlow", FIN), "FNOP": ("Capex", CAPEX)}
    return {t: {"cik": kit.CIK[t], "years": {2025: {rows[t][0]: kit.spec(rows[t][1])}}}
            for t in tickers}


@pytest.mark.criterion("C6")
def test_a_build_records_the_drops_and_never_raises(kit):
    # oracle: the two lookups of the inventory (FALL FY2025 financing, FNOP FY2025 capex) both
    # have no FSDS filing to read; nothing is keyed, so nothing is phrased
    w = kit.witness()
    build = build_v2_suites(_inventory(kit, ["FALL", "FNOP"]), {}, kit.FakeSec(), kit.NoStore(),
                            None, None, quotas={"lookup": 10}, per_intent=1, share=0.5,
                            salt="kfw", witness=w)
    intents = build["manifest"]["intents"]
    assert intents["selected"]["lookup"] == 0
    assert intents["dropped"] == {"fsds_not_published": 2}
    assert intents["dropped_by_shape"] == {"lookup": {"fsds_not_published": 2}}
    assert build["dev"]["n_cases"] == 0 and build["validation"]["n_cases"] == 0
    assert build["manifest"]["key_independence"]["by_status"]["no_fsds"] == 2


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_publishing_the_missing_quarter_keys_the_input_and_closes_its_gap(kit):
    # oracle: with a quarter 2026q1 holding FALL's FY2025 10-K (conftest FA25_LINES: CF line
    # "Net cash used in financing activities" FY2025 -9,000), FALL FY2025 financing is read on
    # its own accession and keyed; without it the input is fsds_not_published
    without = kit.witness()
    assert _drop(without, kit, "FALL", "FinancingCashFlow", FIN, 2025) == ("fsds_not_published",)
    assert without.fsds_manifest()["not_published"]["n"] == 1
    with_q = kit.witness(more=[kit.q2026])
    key, adsh, _v = _fy_key(with_q, kit, "FALL", "FinancingCashFlow", FIN, 2025)
    assert (key["accession"], adsh) == (kit.FA25, kit.FA25)
    assert key["value"] == pytest.approx(-9_000e6, rel=REL)
    assert with_q.fsds_manifest()["not_published"] == {"n": 0, "by_family": {}, "keys": []}
    assert with_q.fsds_manifest()["coverage"]["last"] == "2026q1"
