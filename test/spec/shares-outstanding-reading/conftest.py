"""Fixtures and fakes for the shares-outstanding-reading spec tests.

Spec: docs/specs/shares-outstanding-reading.md. The companyfacts payloads are synthetic (SEC
API shape, made-up CIKs). Filer SSS copies Union Pacific's FY2023-25 share counts as quoted in
the spec's Notes; filer PPP tags only issued shares, like Procter & Gamble. The only fakes sit
at the two I/O seams: ``FakeSecSource`` (``SecSource``) and ``NoStore`` (``FactStoreSource``,
which has no values, so no cross-check runs). No network, no data directory, no clock.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.eval.keys.base import SecSource
from src.eval.suites.intents import load_lexicon, load_phrasebook
from src.eval.suites.v2_suite import FactStoreSource

HERE = Path(__file__).resolve().parent
M = 1_000_000
BN = 1_000_000_000

SSS_CIK = "0000900201"
PPP_CIK = "0000900202"
S23 = "0000900201-24-000010"   # FY2023 10-K, filed 2024-02-09
S24 = "0000900201-25-000020"   # FY2024 10-K, filed 2025-02-07
S25 = "0000900201-26-000030"   # FY2025 10-K, filed 2026-02-06
S25A = "0000900201-26-000040"  # FY2025 10-K/A, filed 2026-04-15 (its own cover date)
P25 = "0000900202-26-000050"   # PPP FY2025 10-K, filed 2026-02-20

FILED = {S23: "2024-02-09", S24: "2025-02-07", S25: "2026-02-06", S25A: "2026-04-15",
         P25: "2026-02-20"}
FY = {S23: 2023, S24: 2024, S25: 2025, S25A: 2025, P25: 2025}
FORM = {S23: "10-K", S24: "10-K", S25: "10-K", S25A: "10-K/A", P25: "10-K"}
# The filings' fiscal-year labels, by hand (each filing's own DEI year).
LABELS = {S23: 2023, S24: 2024, S25: 2025, S25A: 2025, P25: 2025}

YEAR_END = {2022: "2022-12-31", 2023: "2023-12-31", 2024: "2024-12-31", 2025: "2025-12-31"}
# Union Pacific's year-end outstanding shares and cover counts (spec Notes).
KEY = {2023: 609_703_814, 2024: 604_241_260, 2025: 593_245_884}
COVER = {2023: 609_777_914, 2024: 604_286_378, 2025: 593_391_460}
COVER_DATE = {2023: "2024-02-02", 2024: "2025-01-31", 2025: "2026-01-30"}
OWN_10K = {2023: S23, 2024: S24, 2025: S25}
AMENDED_COVER = 590_000_000  # the 10-K/A's later cover count (made up)
ISSUED_2025 = 1_113_161_191
REVENUE = {2023: 24_100 * M, 2024: 24_250 * M, 2025: 24_500 * M}

COVER_CONCEPT = "dei:EntityCommonStockSharesOutstanding"


def entry(start: str | None, end: str, val: int | None, accn: str, **extra) -> dict:
    """One companyfacts entry; ``start`` is ``None`` for an instant."""
    e = {"end": end, "val": val, "accn": accn, "fy": FY[accn], "fp": "FY", "form": FORM[accn],
         "filed": FILED[accn]}
    if start is not None:
        e["start"] = start
    e.update(extra)
    return e


def _revenue(accn: str, year: int) -> dict:
    return entry(f"{year}-01-01", YEAR_END[year], REVENUE[year], accn)


def sss_doc() -> dict:
    """SSS: three 10-Ks, each with the year-end balance, its prior year and a cover count."""
    outstanding = [
        entry(None, YEAR_END[2022], 612_393_321, S23), entry(None, YEAR_END[2023], KEY[2023], S23),
        entry(None, YEAR_END[2023], KEY[2023], S24), entry(None, YEAR_END[2024], KEY[2024], S24),
        entry(None, YEAR_END[2024], KEY[2024], S25), entry(None, YEAR_END[2025], KEY[2025], S25),
    ]
    cover = [entry(None, COVER_DATE[y], COVER[y], OWN_10K[y]) for y in (2023, 2024, 2025)]
    cover.append(entry(None, "2026-04-10", AMENDED_COVER, S25A))
    revenue = [_revenue(S23, 2023), _revenue(S24, 2023), _revenue(S24, 2024),
               _revenue(S25, 2024), _revenue(S25, 2025), _revenue(S25A, 2025)]
    return {"cik": int(SSS_CIK), "entityName": "Sigma Railways Inc.", "facts": {
        "us-gaap": {
            "CommonStockSharesOutstanding": {"units": {"shares": outstanding}},
            "Revenues": {"units": {"USD": revenue}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": cover}}},
    }}


def ppp_doc() -> dict:
    """PPP: issued shares and a cover count, no outstanding-shares fact."""
    return {"cik": int(PPP_CIK), "entityName": "Pi Household Products Co.", "facts": {
        "us-gaap": {
            "CommonStockSharesIssued": {"units": {"shares": [
                entry(None, YEAR_END[2025], 4_000 * M, P25)]}},
            "Revenues": {"units": {"USD": [_revenue(P25, 2025)]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            entry(None, "2026-02-13", 2_300 * M, P25)]}}},
    }}


def docs() -> dict[str, dict]:
    return {SSS_CIK: sss_doc(), PPP_CIK: ppp_doc()}


def inventory() -> dict:
    shares = {"concept": "us-gaap:CommonStockSharesOutstanding", "unit": "shares"}
    revenue = {"concept": "us-gaap:Revenues", "unit": "USD"}
    return {
        "SSS": {"cik": SSS_CIK, "years": {
            y: {"SharesOutstanding": dict(shares), "Revenue": dict(revenue)}
            for y in (2023, 2024, 2025)}},
        "PPP": {"cik": PPP_CIK, "years": {2025: {
            "SharesOutstanding": {"concept": "us-gaap:CommonStockSharesIssued",
                                  "unit": "shares"},
            "Revenue": dict(revenue)}}},
    }


class FakeSecSource(SecSource):
    """Serves the synthetic companyfacts documents (copies, so a test cannot alter them)."""

    def __init__(self, documents: dict[str, dict]) -> None:
        self._docs = documents

    def company_facts(self, cik: str) -> dict:
        return copy.deepcopy(self._docs[str(cik).zfill(10)])

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("the v2 build reads companyfacts only")


class NoStore(FactStoreSource):
    """A fact store with no values: every key stays single-source."""

    def value(self, cik, concept, unit, period_start, period_end, accession):
        return None


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The payloads, inventory, fakes and fixture YAML, as one namespace."""
    return SimpleNamespace(
        M=M, BN=BN, SSS_CIK=SSS_CIK, PPP_CIK=PPP_CIK, S23=S23, S24=S24, S25=S25, S25A=S25A,
        P25=P25, LABELS=LABELS, YEAR_END=YEAR_END, KEY=KEY, COVER=COVER, COVER_DATE=COVER_DATE,
        OWN_10K=OWN_10K, AMENDED_COVER=AMENDED_COVER, ISSUED_2025=ISSUED_2025, FILED=FILED,
        COVER_CONCEPT=COVER_CONCEPT, entry=entry, sss_doc=sss_doc, ppp_doc=ppp_doc, docs=docs,
        inventory=inventory, sec=lambda d=None: FakeSecSource(docs() if d is None else d),
        store=NoStore, phrasebook=load_phrasebook(HERE / "phrasebook.yaml"),
        lexicon=load_lexicon(HERE / "lexicon.yaml"),
    )
