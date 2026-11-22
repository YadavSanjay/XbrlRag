"""Fixtures, fakes and independent oracles for the hf-suite-build spec tests.

Spec: docs/specs/hf-suite-build.md. The companyfacts table is the compound-shape spec's
(synthetic SEC API shape, made-up CIKs, round values chosen by hand), rebuilt here as Python
data. DEVB and DEVD play the held-out filers: the base lexicon names DEVA, DEVC and DEVE,
the overlay DEVB and DEVD, and ``lexicon_full.yaml`` (written by hand) all five. The only
fakes sit at the two I/O seams (``SecSource``, ``FactStoreSource``). No network, no data
directory, no database, no clock; no fixture names a real held-out filer.

The fixture, in millions of USD (December filers; each FY2024 10-K also reports FY2023, each
FY2023 10-K reports FY2023 only):

    filer  Revenue 23/24   OperatingIncome 23/24   NetIncome 23/24
    DEVA   450 / 500       90 / 100 (*)            50 / 60
    DEVB   420 / 400       100 / 120               40 / 48
    DEVC   280 / 300       70 / 75                 -10 / 30
    DEVD   150 / 200       5 / 10                  4 / 5
    DEVE   - / 900 EUR     -                       -

(*) DEVA's FY2023 10-K/A, filed after its FY2024 10-K, restates FY2023 operating income as 92.

Universes: ``covered`` = all five; ``pair`` = DEVA and DEVC.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.eval.keys.base import SecSource
from src.eval.keys.statement_witness import (
    load_note_concepts,
    load_resolutions,
    load_statement_rules,
)
from src.eval.sec_reference.fsds import load_tables
from src.eval.suites.heldout_build import build_heldout_compound_suites, build_heldout_suites
from src.eval.suites.intents import load_lexicon, load_phrasebook
from src.eval.suites.v2_suite import (
    COMPOUND_SHAPES,
    GENERATED_SHAPES,
    FactStoreSource,
    build_compound_suites,
    build_v2_suites,
)
from src.eval.suites.v2_witness import KeyWitness

HERE = Path(__file__).resolve().parent
W = HERE / "witness"

M = 1_000_000
SALT = "hf-test"
TICKERS = ("DEVA", "DEVB", "DEVC", "DEVD", "DEVE")
CIK = {"DEVA": "0000800001", "DEVB": "0000800002", "DEVC": "0000800003",
       "DEVD": "0000800004", "DEVE": "0000800005"}
NAMES = {"DEVA": "Deva Holdings Inc.", "DEVB": "Devb Corporation", "DEVC": "Devc Inc.",
         "DEVD": "Devd Ltd.", "DEVE": "Deve SE"}
SHORT = {"DEVA": "Deva", "DEVB": "Devb", "DEVC": "Devc", "DEVD": "Devd", "DEVE": "Deve"}
A23, A24, A23A = "0000800001-24-000001", "0000800001-25-000002", "0000800001-25-000003"
B23, B24 = "0000800002-24-000001", "0000800002-25-000002"
C23, C24 = "0000800003-24-000001", "0000800003-25-000002"
D23, D24 = "0000800004-24-000001", "0000800004-25-000002"
E24 = "0000800005-25-000002"
FILED = {A23: "2024-02-12", A24: "2025-02-10", A23A: "2025-03-03", B23: "2024-02-13",
         B24: "2025-02-11", C23: "2024-02-14", C24: "2025-02-12", D23: "2024-02-15",
         D24: "2025-02-13", E24: "2025-02-14"}
FY = {A23: 2023, A24: 2024, A23A: 2023, B23: 2023, B24: 2024, C23: 2023, C24: 2024,
      D23: 2023, D24: 2024, E24: 2024}
FORM = {**{a: "10-K" for a in FILED}, A23A: "10-K/A"}
Q23 = ("2023-01-01", "2023-12-31")
Q24 = ("2024-01-01", "2024-12-31")
FILINGS = {"DEVA": (A23, A24), "DEVB": (B23, B24), "DEVC": (C23, C24), "DEVD": (D23, D24)}
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
KEYS_INTENT = ("kind", "tickers", "fiscal_years", "metrics", "dimension", "operation",
               "universe", "n", "filing_year")
MAIN_QUOTAS = {s: 1000 for s in GENERATED_SHAPES}
COMPOUND_QUOTAS = {s: 1000 for s in COMPOUND_SHAPES}


def entry(period: tuple[str, str], val: float, accn: str) -> dict:
    """One companyfacts duration entry."""
    start, end = period
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": FY[accn], "fp": "FY",
            "form": FORM[accn], "filed": FILED[accn]}


def companyfacts_docs() -> dict[str, dict]:
    """Fresh companyfacts payloads of DEVA..DEVE, keyed by 10-digit CIK."""
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
    return docs


def _m(metric: str, unit: str = "USD") -> dict:
    return {"concept": CONCEPT[metric], "unit": unit}


def inventory() -> dict:
    """A fresh corpus inventory over DEVA..DEVE."""
    inv: dict = {}
    for t in ("DEVA", "DEVB", "DEVC", "DEVD"):
        inv[t] = {"cik": CIK[t], "years": {y: {m: _m(m) for m in CONCEPT} for y in (2023, 2024)}}
    inv["DEVE"] = {"cik": CIK["DEVE"], "years": {2024: {"Revenue": _m("Revenue", "EUR")}}}
    return inv


def universes() -> dict:
    """``covered`` (all five filers) and ``pair`` (DEVA, DEVC)."""
    return {"covered": list(TICKERS), "pair": ["DEVA", "DEVC"]}


class FakeSecSource(SecSource):
    """Serves companyfacts payloads from a dict (a copy per call). Frames are never read."""

    def __init__(self, docs: dict[str, dict]) -> None:
        self.docs = docs

    def company_facts(self, cik: str) -> dict:
        return copy.deepcopy(self.docs[str(cik).zfill(10)])

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("the build must not read frames")


class PinnedSec(SecSource):
    """Serves one fixed companyfacts document per CIK (the same object on every call)."""

    def __init__(self, docs: dict[str, dict]) -> None:
        self.docs = copy.deepcopy(docs)

    def company_facts(self, cik: str) -> dict:
        return self.docs[str(cik).zfill(10)]

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("the build must not read frames")


class FakeFactStore(FactStoreSource):
    """Serves each payload entry's value by (cik, concept, unit, period, accession)."""

    def __init__(self, docs: dict[str, dict]) -> None:
        self.index: dict[tuple, float] = {}
        for cik, doc in docs.items():
            for taxonomy, tags in doc["facts"].items():
                for tag, node in tags.items():
                    for unit, entries in node["units"].items():
                        for e in entries:
                            k = (cik, f"{taxonomy}:{tag}", unit, e.get("start"), e["end"],
                                 e["accn"])
                            self.index[k] = e["val"]

    def value(self, cik, concept, unit, period_start, period_end, accession):
        val = self.index.get((cik, concept, unit, period_start, period_end, accession))
        if val is None:
            return None
        return {"value": val, "unit": unit, "decimals": -6, "concept": concept, "cik": cik,
                "period_start": period_start, "period_end": period_end, "fiscal_year": None,
                "calendar_year": None, "accession": accession, "filed": None, "form": None,
                "key_source": "fact_store", "selection": "as_reported", "policies": [],
                "status": "single-source", "verified_by": []}


def witness(resolutions: str = "none") -> KeyWitness:
    """A real witness over the fixture FSDS quarter, rules, notes and a resolution list."""
    return KeyWitness(load_tables([W / "fsds" / "2025q1"]),
                      load_statement_rules(W / "statement_lines.yaml"),
                      load_note_concepts(W / "note_concepts.yaml"),
                      load_resolutions(W / f"key_resolutions_{resolutions}.yaml"))


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


def oracle_involves(intent, unis: dict, heldout) -> bool:
    """Requirement 5, restated: a held-out ticker is the filer or a universe member."""
    held = {t.strip().upper() for t in heldout}
    names = set(intent.tickers)
    if intent.universe is not None:
        names |= {t.strip().upper() for t in unis[intent.universe]}
    return bool(names & held)


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def file_text(doc) -> bytes:
    """The main writer's format (v2-suite-build requirement 16), restated."""
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
            + "\n").encode("utf-8")


def intent_tag(record: dict) -> str:
    return next(t for t in record["tags"] if t.startswith("intent:"))


def as_dev(record: dict, suite: str) -> dict:
    """A held-out record turned back into the development form (suite and split tag)."""
    out = copy.deepcopy(record)
    out["suite"] = suite
    out["tags"] = ["split:dev", *out["tags"][1:]]
    return out


_DIGITS7 = re.compile(r"\d{7,}")
_DECIMAL = re.compile(r"\d\.\d")


def leaks(lines, extra=()) -> list[str]:
    """Every forbidden fragment found in ``lines`` (requirement 9), restated.

    The short names (``Deva`` ...) are left to the questions in ``extra``: alone they are
    prefixes of ordinary words such as "Development".
    """
    forbidden = [*TICKERS, *CIK.values(), *(str(int(c)) for c in CIK.values()),
                 *NAMES.values(), "us-gaap", "int-", *extra]
    found = []
    for line in lines:
        text = str(line)
        found += [f for f in forbidden if f and f in text]
        bare = " ".join(tok for tok in text.split() if "sha256:" not in tok)
        found += _DIGITS7.findall(bare) + _DECIMAL.findall(bare)
    return found


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The payloads, inventory, fakes, fixture paths and oracles, as one namespace."""
    lexicon_full = load_lexicon(HERE / "lexicon_full.yaml")
    phrasebook = load_phrasebook(HERE / "phrasebook_main.yaml")
    compound_phrasebook = load_phrasebook(HERE / "phrasebook_compound.yaml")

    def dev_build(*, docs=None, inv=None, uni=None, lexicon=None, quotas=None, per_intent=2,
                  share=0.0, salt=SALT, wit=None, sec=None):
        docs = companyfacts_docs() if docs is None else docs
        return build_v2_suites(
            inventory() if inv is None else inv, universes() if uni is None else uni,
            FakeSecSource(docs) if sec is None else sec, FakeFactStore(docs), phrasebook,
            lexicon_full if lexicon is None else lexicon,
            quotas=MAIN_QUOTAS if quotas is None else quotas, per_intent=per_intent,
            share=share, salt=salt, witness=wit)

    def dev_compound(*, docs=None, inv=None, uni=None, lexicon=None, quotas=None,
                     per_intent=2, share=0.0, salt=SALT, wit=None, sec=None):
        docs = companyfacts_docs() if docs is None else docs
        return build_compound_suites(
            inventory() if inv is None else inv, universes() if uni is None else uni,
            FakeSecSource(docs) if sec is None else sec, FakeFactStore(docs),
            compound_phrasebook, lexicon_full if lexicon is None else lexicon,
            quotas=COMPOUND_QUOTAS if quotas is None else quotas, per_intent=per_intent,
            share=share, salt=salt, witness=wit)

    def hf_build(heldout, *, docs=None, inv=None, uni=None, lexicon=None, quotas=None,
                 per_intent=2, salt=SALT, wit=None, sec=None):
        docs = companyfacts_docs() if docs is None else docs
        return build_heldout_suites(
            inventory() if inv is None else inv, universes() if uni is None else uni,
            FakeSecSource(docs) if sec is None else sec, FakeFactStore(docs), phrasebook,
            lexicon_full if lexicon is None else lexicon, heldout=list(heldout),
            quotas=MAIN_QUOTAS if quotas is None else quotas, per_intent=per_intent,
            salt=salt, witness=wit)

    def hf_compound(heldout, *, docs=None, inv=None, uni=None, lexicon=None, quotas=None,
                    per_intent=2, salt=SALT, wit=None, sec=None):
        docs = companyfacts_docs() if docs is None else docs
        return build_heldout_compound_suites(
            inventory() if inv is None else inv, universes() if uni is None else uni,
            FakeSecSource(docs) if sec is None else sec, FakeFactStore(docs),
            compound_phrasebook, lexicon_full if lexicon is None else lexicon,
            heldout=list(heldout), quotas=COMPOUND_QUOTAS if quotas is None else quotas,
            per_intent=per_intent, salt=salt, witness=wit)

    return SimpleNamespace(
        hf_build=hf_build, hf_compound=hf_compound,
        HERE=HERE, W=W, M=M, SALT=SALT, TICKERS=TICKERS, CIK=CIK, NAMES=NAMES, SHORT=SHORT,
        CONCEPT=CONCEPT, KEYS_INTENT=KEYS_INTENT, MAIN_QUOTAS=MAIN_QUOTAS,
        COMPOUND_QUOTAS=COMPOUND_QUOTAS, GENERATED_SHAPES=GENERATED_SHAPES,
        COMPOUND_SHAPES=COMPOUND_SHAPES,
        companyfacts_docs=companyfacts_docs, inventory=inventory, universes=universes,
        sec=FakeSecSource, pinned_sec=PinnedSec, store=FakeFactStore, witness=witness,
        lexicon_full=lexicon_full, phrasebook=phrasebook,
        compound_phrasebook=compound_phrasebook,
        lexicon_base=lambda: load_lexicon(HERE / "lexicon_base.yaml"),
        norm=norm, oracle_id=oracle_id, oracle_involves=oracle_involves,
        canonical=canonical, sha256_hash=sha256_hash, file_text=file_text,
        intent_tag=intent_tag, as_dev=as_dev, leaks=leaks,
        dev_build=dev_build, dev_compound=dev_compound,
    )
