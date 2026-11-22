"""Fixtures, fakes and independent oracles for the v2-suite-build spec tests.

Spec: docs/specs/v2-suite-build.md. The companyfacts payloads are synthetic (SEC API shape,
made-up CIKs, values chosen by hand) and built here as Python data, so a test can copy and
change them. The only fakes sit at the two seams: ``FakeSecSource`` (``SecSource``) and
``FakeFactStore`` (``FactStoreSource``), both reading those payloads. The oracles restate
the spec's rules with ``hashlib`` and ``json``. Everything is exposed through one
session-scoped ``kit`` fixture so hypothesis tests can use it. No network, no data
directory, no clock.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.eval.keys.base import SecSource
from src.eval.suites.intents import load_lexicon, load_phrasebook
from src.eval.suites.v2_suite import FactStoreSource

HERE = Path(__file__).resolve().parent

SHAPES = (
    "lookup", "compute.ratio",
    "screen.top", "screen.bottom", "screen.sum", "screen.median", "peer_relative",
    "compute.yoy_growth", "compute.difference", "compute.cagr", "trend", "point_in_time",
)
CLASSES = (
    ("single_filer", 35, ("lookup", "compute.ratio")),
    ("cross_filer", 15, ("screen.top", "screen.bottom", "screen.sum", "screen.median",
                         "peer_relative")),
    ("cross_period", 10, ("compute.yoy_growth", "compute.difference", "compute.cagr",
                          "trend", "point_in_time")),
)

AAA_CIK, BBB_CIK, TWX_CIK, CCC_CIK = "0000900101", "0000900102", "0000900103", "0000900104"
EEE_CIK = "0000900105"
# AAA, September 52/53-week filer: three 10-Ks, each recasting the prior years.
A22 = "0000900101-22-000010"  # FY2022 10-K, filed 2022-10-28
A23 = "0000900101-23-000020"  # FY2023 10-K, filed 2023-11-03
A24 = "0000900101-24-000030"  # FY2024 10-K, filed 2024-11-01
P22 = ("2021-09-26", "2022-09-24")
P23 = ("2022-09-25", "2023-09-30")
P24 = ("2023-10-01", "2024-09-28")
# BBB, December filer: a 10-K/A of FY2023 filed after the FY2024 10-K.
B23 = "0000900102-24-000011"   # FY2023 10-K, filed 2024-02-15
B24 = "0000900102-25-000021"   # FY2024 10-K, filed 2025-02-14
B23A = "0000900102-25-000031"  # FY2023 10-K/A, filed 2025-03-01
Q22 = ("2022-01-01", "2022-12-31")
Q23 = ("2023-01-01", "2023-12-31")
Q24 = ("2024-01-01", "2024-12-31")
# CCC, June filer: revenue only in EUR, no net income at all.
C24 = "0000900104-24-000040"   # FY2024 10-K, filed 2024-08-20
J24 = ("2023-07-01", "2024-06-30")
# TWX, 20-F filer reporting in TWD.
T25 = "0000900103-25-000002"   # FY2024 20-F, filed 2025-04-15
# EEE, December filer reporting in EUR (only used to extend a universe).
E25 = "0000900105-25-000007"   # FY2024 10-K, filed 2025-02-20

BN = 1_000_000_000

FILED = {A22: "2022-10-28", A23: "2023-11-03", A24: "2024-11-01", B23: "2024-02-15",
         B24: "2025-02-14", B23A: "2025-03-01", C24: "2024-08-20", T25: "2025-04-15",
         E25: "2025-02-20"}
FY = {A22: 2022, A23: 2023, A24: 2024, B23: 2023, B24: 2024, B23A: 2023, C24: 2024, T25: 2024,
      E25: 2024}
FORM = {A22: "10-K", A23: "10-K", A24: "10-K", B23: "10-K", B24: "10-K", B23A: "10-K/A",
        C24: "10-K", T25: "20-F", E25: "10-K"}

KEY_FIELDS = {
    "value", "unit", "decimals", "concept", "cik", "period_start", "period_end",
    "fiscal_year", "calendar_year", "accession", "filed", "form", "key_source",
    "selection", "policies", "status", "verified_by",
}
KEYED_FIELDS = {
    "intent", "shape", "value", "rows", "not_meaningful", "definition", "same_filing",
    "inputs", "disagreements", "key_status", "key_source", "policies", "disclosures",
    "subject",
}
TAG_PREFIXES = ("split", "intent", "family", "template", "key_source", "key_status", "shape")


def entry(period: tuple[str | None, str], val: int | None, accn: str, **extra) -> dict:
    """One companyfacts entry; ``period`` is (start, end), start ``None`` for an instant."""
    start, end = period
    e = {"end": end, "val": val, "accn": accn, "fy": FY[accn], "fp": "FY",
         "form": FORM[accn], "filed": FILED[accn]}
    if start is not None:
        e["start"] = start
    e.update(extra)
    return e


def _doc(cik: str, name: str, facts: dict) -> dict:
    return {"cik": int(cik), "entityName": name, "facts": facts}


def companyfacts_docs() -> dict[str, dict]:
    """Fresh companyfacts payloads of the four (five) synthetic filers, by 10-digit CIK."""
    aaa = _doc(AAA_CIK, "Alpha Holdings Inc.", {"us-gaap": {
        "Revenues": {"units": {"USD": [
            entry(P22, 100 * BN, A22),
            entry(P23, 110 * BN, A23), entry(P22, 100 * BN, A23),
            entry(P24, 121 * BN, A24), entry(P23, 111 * BN, A24), entry(P22, 102 * BN, A24),
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            entry(P22, -5 * BN, A22),
            entry(P23, 8 * BN, A23), entry(P22, -5 * BN, A23),
            entry(P24, 12 * BN, A24), entry(P23, 8 * BN, A24), entry(P22, -5 * BN, A24),
        ]}},
        "Assets": {"units": {"USD": [
            entry((None, "2022-09-24"), 300 * BN, A22),
            entry((None, "2023-09-30"), 320 * BN, A23),
            entry((None, "2022-09-24"), 301 * BN, A23),
            entry((None, "2024-09-28"), 350 * BN, A24),
            entry((None, "2023-09-30"), 321 * BN, A24),
        ]}},
    }})
    bbb = _doc(BBB_CIK, "Beta Corporation", {"us-gaap": {
        "Revenues": {"units": {"USD": [
            entry(Q23, 200 * BN, B23), entry(Q22, 180 * BN, B23),
            entry(Q24, 220 * BN, B24), entry(Q23, 205 * BN, B24),
            entry(Q23, 206 * BN, B23A),
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            entry(Q23, 20 * BN, B23), entry(Q22, 18 * BN, B23),
            entry(Q24, 25 * BN, B24), entry(Q23, 20 * BN, B24),
        ]}},
    }})
    ccc = _doc(CCC_CIK, "Gamma SE", {"us-gaap": {
        "Revenues": {"units": {"EUR": [entry(J24, 50 * BN, C24)]}},
    }})
    twx = _doc(TWX_CIK, "Twix Manufacturing Co., Ltd.", {"ifrs-full": {
        "Revenue": {"units": {"TWD": [entry(Q24, 2_894_307_699_000, T25)]}},
    }})
    eee = _doc(EEE_CIK, "Epsilon Ltd.", {"us-gaap": {
        "Revenues": {"units": {"EUR": [entry(Q24, 90 * BN, E25)]}},
    }})
    return {AAA_CIK: aaa, BBB_CIK: bbb, CCC_CIK: ccc, TWX_CIK: twx, EEE_CIK: eee}


def _m(concept: str, unit: str = "USD") -> dict:
    return {"concept": concept, "unit": unit}


REV = _m("us-gaap:Revenues")
NI = _m("us-gaap:NetIncomeLoss")
TA = _m("us-gaap:Assets")


def inventory() -> dict:
    """A fresh corpus inventory over AAA, BBB, CCC and TWX (spec requirement 1)."""
    return {
        "AAA": {"cik": AAA_CIK, "years": {
            y: {"Revenue": dict(REV), "NetIncome": dict(NI), "TotalAssets": dict(TA)}
            for y in (2022, 2023, 2024)}},
        "BBB": {"cik": BBB_CIK, "years": {
            y: {"Revenue": dict(REV), "NetIncome": dict(NI)} for y in (2023, 2024)}},
        "CCC": {"cik": CCC_CIK, "years": {2024: {"Revenue": dict(REV), "NetIncome": dict(NI)}}},
        "TWX": {"cik": TWX_CIK, "years": {2024: {"Revenue": _m("ifrs-full:Revenue", "TWD")}}},
    }


def eee_inventory_entry() -> dict:
    """EEE's inventory entry: FY2024 revenue in EUR."""
    return {"cik": EEE_CIK, "years": {2024: {"Revenue": _m("us-gaap:Revenues", "EUR")}}}


def universes() -> dict:
    """The universes: every inventory filer, and a named group with a non-corpus member."""
    return {"covered": ["AAA", "BBB", "CCC", "TWX"], "peers": ["AAA", "BBB", "ZZZ"]}


class FakeSecSource(SecSource):
    """Serves companyfacts payloads from a dict; records every call. Frames are unused."""

    def __init__(self, docs: dict[str, dict]) -> None:
        self.docs = docs
        self.calls: list[tuple] = []

    def company_facts(self, cik: str) -> dict:
        self.calls.append(("company_facts", cik))
        return copy.deepcopy(self.docs[str(cik).zfill(10)])

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        self.calls.append(("frames", taxonomy, tag, unit, period))
        raise AssertionError("v2-suite-build must not read frames")


class FakeFactStore(FactStoreSource):
    """Serves each payload entry's value by (cik, concept, unit, period, accession).

    ``overrides`` maps ``(cik, concept, period_start, period_end, accession)`` to a value;
    ``missing`` lists such tuples the store does not hold. Every call is recorded.
    """

    def __init__(self, docs: dict[str, dict], overrides: dict | None = None,
                 missing: tuple = ()) -> None:
        self.index: dict[tuple, float] = {}
        for cik, doc in docs.items():
            for taxonomy, tags in doc["facts"].items():
                for tag, node in tags.items():
                    for unit, entries in node["units"].items():
                        for e in entries:
                            k = (cik, f"{taxonomy}:{tag}", unit, e.get("start"), e["end"],
                                 e["accn"])
                            self.index[k] = e["val"]
        self.overrides = dict(overrides or {})
        self.missing = set(missing)
        self.calls: list[tuple] = []

    def value(self, cik, concept, unit, period_start, period_end, accession):
        self.calls.append((cik, concept, unit, period_start, period_end, accession))
        short = (cik, concept, period_start, period_end, accession)
        if short in self.missing:
            return None
        val = self.overrides.get(short, self.index.get(
            (cik, concept, unit, period_start, period_end, accession)))
        if val is None:
            return None
        return {"value": val, "unit": unit, "decimals": -6, "concept": concept, "cik": cik,
                "period_start": period_start, "period_end": period_end, "fiscal_year": None,
                "calendar_year": None, "accession": accession, "filed": None, "form": None,
                "key_source": "fact_store", "selection": "as_reported", "policies": [],
                "status": "single-source", "verified_by": []}


def intent_id_oracle(intent) -> str:
    """eval-intents C3, restated: ``int-`` + 12 hex of SHA-1 of the canonical intent JSON."""
    d = {"kind": intent.kind, "tickers": list(intent.tickers),
         "fiscal_years": list(intent.fiscal_years), "metrics": list(intent.metrics),
         "dimension": intent.dimension, "operation": intent.operation,
         "universe": intent.universe, "n": intent.n, "filing_year": intent.filing_year}
    blob = json.dumps(d, sort_keys=True, separators=(",", ":"))
    return "int-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def shape_of(intent) -> str:
    """eval-intents' shape rule, restated."""
    if intent.kind in ("compute", "screen"):
        return f"{intent.kind}.{intent.operation}"
    return intent.kind


def canonical(obj) -> str:
    """Spec requirement 15's canonical JSON, restated."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The payloads, inventory, fakes, fixture YAML and oracles, as one namespace."""
    return SimpleNamespace(
        SHAPES=SHAPES, CLASSES=CLASSES, BN=BN,
        AAA_CIK=AAA_CIK, BBB_CIK=BBB_CIK, CCC_CIK=CCC_CIK, TWX_CIK=TWX_CIK, EEE_CIK=EEE_CIK,
        A22=A22, A23=A23, A24=A24, B23=B23, B24=B24, B23A=B23A, C24=C24, T25=T25, E25=E25,
        P22=P22, P23=P23, P24=P24, Q22=Q22, Q23=Q23, Q24=Q24, J24=J24,
        KEY_FIELDS=KEY_FIELDS, KEYED_FIELDS=KEYED_FIELDS, TAG_PREFIXES=TAG_PREFIXES,
        entry=entry, companyfacts_docs=companyfacts_docs, inventory=inventory,
        eee_inventory_entry=eee_inventory_entry, universes=universes,
        sec=lambda docs=None: FakeSecSource(companyfacts_docs() if docs is None else docs),
        store=lambda docs=None, **kw: FakeFactStore(
            companyfacts_docs() if docs is None else docs, **kw),
        phrasebook=load_phrasebook(HERE / "phrasebook.yaml"),
        lexicon=load_lexicon(HERE / "lexicon.yaml"),
        intent_id=intent_id_oracle, shape_of=shape_of, canonical=canonical,
        sha256_hash=sha256_hash,
    )
