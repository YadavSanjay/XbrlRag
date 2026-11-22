"""Fixtures, fakes and independent oracles for the compound-shape spec tests.

Spec: docs/specs/compound-shape.md. The companyfacts payloads are synthetic (SEC API shape,
made-up CIKs, round values chosen by hand) and built here as Python data so a test can copy
and change them. The only fakes sit at the two I/O seams, as in the v2-suite-build spec
tests: ``FakeSecSource`` (``SecSource``) and ``FakeFactStore`` (``FactStoreSource``), both
reading those payloads. The oracles restate the spec's rules with ``hashlib`` and ``json``.
No network, no data directory, no clock.

The fixture, in millions of USD (December filers; each FY2024 10-K also reports FY2023, each
FY2023 10-K reports FY2023 only):

    filer  Revenue 23/24   OperatingIncome 23/24   NetIncome 23/24
    DEVA   450 / 500       90 / 100 (*)            50 / 60
    DEVB   420 / 400       100 / 120               40 / 48
    DEVC   280 / 300       70 / 75                 -10 / 30
    DEVD   150 / 200       5 / 10                  4 / 5
    DEVE   - / 900 EUR     -                       -
    DEVF   - / 250         -                       -          (only in the "extra" test)

(*) DEVA's FY2023 10-K/A, filed after its FY2024 10-K, restates FY2023 operating income as
92: FY2023's own (latest-filed) key is 92, while the FY2024 10-K reports FY2023 as 90.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.eval.keys.base import SecSource
from src.eval.suites.intents import Intent, load_lexicon, load_phrasebook
from src.eval.suites.v2_suite import FactStoreSource, build_compound_suites

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

M = 1_000_000
RBR = "compound.rank_by_ratio"
LG = "compound.leader_growth"
RATIO_IDS = ("operating_margin", "net_margin", "rd_intensity", "current_ratio")
SALT = "compound-test"

CIK = {"DEVA": "0000800001", "DEVB": "0000800002", "DEVC": "0000800003",
       "DEVD": "0000800004", "DEVE": "0000800005", "DEVF": "0000800006"}
A23, A24, A23A = "0000800001-24-000001", "0000800001-25-000002", "0000800001-25-000003"
B23, B24 = "0000800002-24-000001", "0000800002-25-000002"
C23, C24 = "0000800003-24-000001", "0000800003-25-000002"
D23, D24 = "0000800004-24-000001", "0000800004-25-000002"
E24 = "0000800005-25-000002"
F24 = "0000800006-25-000002"
FILED = {A23: "2024-02-12", A24: "2025-02-10", A23A: "2025-03-03", B23: "2024-02-13",
         B24: "2025-02-11", C23: "2024-02-14", C24: "2025-02-12", D23: "2024-02-15",
         D24: "2025-02-13", E24: "2025-02-14", F24: "2025-02-18"}
FY = {A23: 2023, A24: 2024, A23A: 2023, B23: 2023, B24: 2024, C23: 2023, C24: 2024,
      D23: 2023, D24: 2024, E24: 2024, F24: 2024}
FORM = {**{a: "10-K" for a in FILED}, A23A: "10-K/A"}
Q23 = ("2023-01-01", "2023-12-31")
Q24 = ("2024-01-01", "2024-12-31")
FILINGS = {"DEVA": (A23, A24), "DEVB": (B23, B24), "DEVC": (C23, C24), "DEVD": (D23, D24)}
# filer -> concept tag -> (FY2023 value, FY2024 value), in millions.
VALUES = {
    "DEVA": {"Revenues": (450, 500), "OperatingIncomeLoss": (90, 100),
             "NetIncomeLoss": (50, 60)},
    "DEVB": {"Revenues": (420, 400), "OperatingIncomeLoss": (100, 120),
             "NetIncomeLoss": (40, 48)},
    "DEVC": {"Revenues": (280, 300), "OperatingIncomeLoss": (70, 75),
             "NetIncomeLoss": (-10, 30)},
    "DEVD": {"Revenues": (150, 200), "OperatingIncomeLoss": (5, 10),
             "NetIncomeLoss": (4, 5)},
}
CONCEPT = {"Revenue": "us-gaap:Revenues", "OperatingIncome": "us-gaap:OperatingIncomeLoss",
           "NetIncome": "us-gaap:NetIncomeLoss"}
NAMES = {"DEVA": "Deva Holdings Inc.", "DEVB": "Devb Corporation", "DEVC": "Devc Inc.",
         "DEVD": "Devd Ltd.", "DEVE": "Deve SE", "DEVF": "Devf Corp."}

KEYS_INTENT = ("kind", "tickers", "fiscal_years", "metrics", "dimension", "operation",
               "universe", "n", "filing_year")


def entry(period: tuple[str, str], val: float, accn: str) -> dict:
    """One companyfacts duration entry."""
    start, end = period
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": FY[accn], "fp": "FY",
            "form": FORM[accn], "filed": FILED[accn]}


def companyfacts_docs() -> dict[str, dict]:
    """Fresh companyfacts payloads of DEVA..DEVF, keyed by 10-digit CIK."""
    docs: dict[str, dict] = {}
    for ticker, tags in VALUES.items():
        f23, f24 = FILINGS[ticker]
        facts: dict[str, dict] = {}
        for tag, (v23, v24) in tags.items():
            rows = [entry(Q23, v23 * M, f23), entry(Q24, v24 * M, f24), entry(Q23, v23 * M, f24)]
            facts[tag] = {"units": {"USD": rows}}
        docs[CIK[ticker]] = {"cik": int(CIK[ticker]), "entityName": NAMES[ticker],
                             "facts": {"us-gaap": facts}}
    deva = docs[CIK["DEVA"]]["facts"]["us-gaap"]
    deva["OperatingIncomeLoss"]["units"]["USD"].append(entry(Q23, 92 * M, A23A))
    docs[CIK["DEVE"]] = {"cik": int(CIK["DEVE"]), "entityName": NAMES["DEVE"], "facts": {
        "us-gaap": {"Revenues": {"units": {"EUR": [entry(Q24, 900 * M, E24)]}}}}}
    docs[CIK["DEVF"]] = {"cik": int(CIK["DEVF"]), "entityName": NAMES["DEVF"], "facts": {
        "us-gaap": {"Revenues": {"units": {"USD": [entry(Q24, 250 * M, F24)]}}}}}
    return docs


def facts_of(docs: dict, ticker: str, tag: str, unit: str = "USD") -> list[dict]:
    """The mutable entry list of one filer's concept in one unit."""
    return docs[CIK[ticker]]["facts"]["us-gaap"][tag]["units"][unit]


def _m(metric: str, unit: str = "USD") -> dict:
    return {"concept": CONCEPT[metric], "unit": unit}


def inventory() -> dict:
    """A fresh corpus inventory over DEVA..DEVE (DEVF is added by the tests that need it)."""
    inv: dict = {}
    for t in ("DEVA", "DEVB", "DEVC", "DEVD"):
        inv[t] = {"cik": CIK[t], "years": {y: {m: _m(m) for m in CONCEPT} for y in (2023, 2024)}}
    inv["DEVE"] = {"cik": CIK["DEVE"], "years": {2024: {"Revenue": _m("Revenue", "EUR")}}}
    return inv


def devf_entry() -> dict:
    """DEVF's inventory entry: FY2024 revenue in USD."""
    return {"cik": CIK["DEVF"], "years": {2024: {"Revenue": _m("Revenue")}}}


def universes() -> dict:
    """One universe, ``big``: the four USD filers and the EUR filer DEVE."""
    return {"big": ["DEVA", "DEVB", "DEVC", "DEVD", "DEVE"]}


class FakeSecSource(SecSource):
    """Serves companyfacts payloads from a dict. Frames are never read."""

    def __init__(self, docs: dict[str, dict]) -> None:
        self.docs = docs

    def company_facts(self, cik: str) -> dict:
        return copy.deepcopy(self.docs[str(cik).zfill(10)])

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("the compound build must not read frames")


class FakeFactStore(FactStoreSource):
    """Serves each payload entry's value by (cik, concept, unit, period, accession).

    ``missing`` lists ``(cik, concept, period_start, period_end, accession)`` tuples the
    store does not hold.
    """

    def __init__(self, docs: dict[str, dict], missing: tuple = ()) -> None:
        self.index: dict[tuple, float] = {}
        for cik, doc in docs.items():
            for taxonomy, tags in doc["facts"].items():
                for tag, node in tags.items():
                    for unit, entries in node["units"].items():
                        for e in entries:
                            k = (cik, f"{taxonomy}:{tag}", unit, e.get("start"), e["end"],
                                 e["accn"])
                            self.index[k] = e["val"]
        self.missing = set(missing)

    def value(self, cik, concept, unit, period_start, period_end, accession):
        if (cik, concept, period_start, period_end, accession) in self.missing:
            return None
        val = self.index.get((cik, concept, unit, period_start, period_end, accession))
        if val is None:
            return None
        return {"value": val, "unit": unit, "decimals": -6, "concept": concept, "cik": cik,
                "period_start": period_start, "period_end": period_end, "fiscal_year": None,
                "calendar_year": None, "accession": accession, "filed": None, "form": None,
                "key_source": "fact_store", "selection": "as_reported", "policies": [],
                "status": "single-source", "verified_by": []}


# --------------------------------------------------------------------------- oracles


def norm(kw: dict) -> dict:
    """The nine normalised intent fields of a kwargs dict (eval-intents' rules)."""
    return {
        "kind": kw["kind"],
        "tickers": [t.strip().upper() for t in kw.get("tickers", ())],
        "fiscal_years": sorted(set(kw.get("fiscal_years", ()))),
        "metrics": list(kw.get("metrics", ())),
        "dimension": kw.get("dimension"),
        "operation": kw.get("operation"),
        "universe": kw.get("universe"),
        "n": kw.get("n"),
        "filing_year": kw.get("filing_year"),
    }


def oracle_id(kw: dict) -> str:
    """eval-intents C3, restated: ``int-`` + 12 hex of SHA-1 of the canonical intent JSON."""
    blob = json.dumps(norm(kw), sort_keys=True, separators=(",", ":"))
    return "int-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def oracle_axis(key: str, share: float, salt: str) -> bool:
    """eval-intents' split axis, restated."""
    return int(hashlib.sha1(f"{salt}:{key}".encode()).hexdigest()[:8], 16) / 2**32 < share


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def rbr(year: int, metric: str, ratio: str, universe: str = "big", n: int = 3) -> dict:
    """The kwargs of a rank_by_ratio intent."""
    return {"kind": "compound", "operation": "rank_by_ratio", "universe": universe, "n": n,
            "fiscal_years": [year], "metrics": [metric, ratio]}


def lg(year: int, metric: str, growth: str, universe: str = "big", n: int = 3) -> dict:
    """The kwargs of a leader_growth intent ranking in ``year``."""
    return {"kind": "compound", "operation": "leader_growth", "universe": universe, "n": n,
            "fiscal_years": [year - 1, year], "metrics": [metric, growth]}


def make_intent(kw: dict) -> Intent:
    """An ``Intent`` from kwargs; a refusal of a valid compound intent fails the test."""
    try:
        return Intent(**kw)
    except ValueError as exc:
        pytest.fail(f"a valid intent was refused: {kw!r}: {exc}")


def compound_phrasebook(path: Path | None = None):
    """The fixture compound phrasebook; a refusal of a valid file fails the test."""
    try:
        return load_phrasebook(path or HERE / "phrasebook_compound.yaml")
    except ValueError as exc:
        pytest.fail(f"a valid compound phrasebook was refused: {exc}")


def build(*, docs=None, inv=None, uni=None, missing=(), quotas=None, per_intent=1,
          share=0.0, salt=SALT, lexicon=None):
    """``build_compound_suites`` over the fixture (all keyable intents kept by default)."""
    pb = compound_phrasebook()
    docs = companyfacts_docs() if docs is None else docs
    return build_compound_suites(
        inventory() if inv is None else inv, universes() if uni is None else uni,
        FakeSecSource(docs), FakeFactStore(docs, missing=missing), pb,
        load_lexicon(HERE / "lexicon.yaml") if lexicon is None else lexicon,
        quotas={RBR: 100, LG: 100} if quotas is None else quotas,
        per_intent=per_intent, share=share, salt=salt)


def records(result) -> list[tuple[str, dict]]:
    """(split, record) for every case of a build."""
    return [(s, c) for s in ("dev", "validation") for c in result[s]["cases"]]


def cases_of(result, kw: dict) -> list[dict]:
    """The records of one intent, found by its ``intent:`` tag."""
    tag = f"intent:{oracle_id(kw)}"
    return [c for _s, c in records(result) if tag in c["tags"]]


def case_of(result, kw: dict) -> dict:
    """The one record of an intent (per_intent = 1)."""
    found = cases_of(result, kw)
    assert len(found) == 1, (kw, len(found))
    return found[0]


def dropped(result, shape: str) -> dict:
    """The build manifest's drop counts of one shape ({} when none)."""
    return result["manifest"]["intents"]["dropped_by_shape"].get(shape, {})


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The payloads, inventory, fakes, fixture YAML paths and oracles, as one namespace."""
    return SimpleNamespace(
        HERE=HERE, REPO=REPO, M=M, RBR=RBR, LG=LG, RATIO_IDS=RATIO_IDS, SALT=SALT, CIK=CIK,
        A23=A23, A24=A24, A23A=A23A, B24=B24, C24=C24, D24=D24, Q23=Q23, Q24=Q24,
        CONCEPT=CONCEPT, KEYS_INTENT=KEYS_INTENT,
        companyfacts_docs=companyfacts_docs, facts_of=facts_of, inventory=inventory,
        devf_entry=devf_entry, universes=universes,
        sec=FakeSecSource, store=FakeFactStore,
        norm=norm, oracle_id=oracle_id, oracle_axis=oracle_axis, canonical=canonical,
        sha256_hash=sha256_hash, rbr=rbr, lg=lg, make_intent=make_intent,
        compound_phrasebook=compound_phrasebook, build=build, records=records,
        cases_of=cases_of, case_of=case_of, dropped=dropped,
        lexicon=lambda: load_lexicon(HERE / "lexicon.yaml"),
        lexicon_main=lambda: load_lexicon(HERE / "lexicon_main.yaml"),
        phrasebook_main=lambda: load_phrasebook(HERE / "phrasebook_main.yaml"),
    )
