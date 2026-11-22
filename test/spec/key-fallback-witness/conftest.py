"""Fixtures, fakes and the hand-written synthetic filings of the key-fallback-witness tests.

Spec: docs/specs/key-fallback-witness.md. Every filing below is made up (CIKs 0000810001 to
0000810005, December fiscal years, round amounts). The companyfacts documents (SEC API shape)
and the FSDS-shaped ``sub``/``pre``/``num``/``tag`` rows are built from the two tables here,
so every expected value in the tests is read off them. The only fakes sit at the I/O seams
(``SecSource``, ``FactStoreSource``). No network, no ``data/``, no database, no clock.

Amounts in millions of USD (FY = the filer's fiscal year, the calendar year here):

    FALL  FY2024 10-K 0000810001-25-000010 (FSDS 2025q1)
          FY2025 10-K 0000810001-26-000010 (not in the FSDS data)
          financing cash flow   FY2024 -10,764 (both 10-Ks)   FY2025 -9,000 (FY2025 10-K)
          D&A on DepreciationDepletionAndAmortization FY2024 1,000 (both; the FY2024 10-K's
              cash-flow line "Depreciation, depletion and amortization")
          Depreciation          FY2024 800 (both; no statement line)
          capex                 FY2024 500 (FY2024 10-K), restated 520 (FY2025 10-K);
                                FY2025 600
          InterestAndOtherIncome FY2024 169 (both; the FY2024 10-K's income-statement line
              "Interest income and other, net", which the InterestIncome rule excludes)
    FEXT  FY2022/23/24 10-Ks (FSDS 2023q1/2024q1/2025q1). Capex on the extension element
          PurchasesOfPropertyAndEquipmentAndIntangibleAssets in the FY2022 and FY2023 10-Ks
          (FY2022 976, FY2023 1,833); the FY2024 10-K presents FY2022/23/24 on
          us-gaap:PaymentsToAcquireProductiveAssets (976, 1,833, 1,069), which companyfacts
          carries from the FY2024 10-K only.
    FRST  as FEXT, but the FY2024 10-K presents FY2022 as 990 (restated), FY2023 1,833,
          FY2024 1,100.
    FDEF  as FEXT with own extension lines "Purchases of property and equipment" (FY2022 700,
          FY2023 800); the FY2024 10-K's standard line is "Purchases of property and equipment
          and businesses" (700, 800, 900), which the Capex rule excludes.
    FNOP  FY2025 10-K 0000810005-26-000050 (not in the FSDS data): capex 300.

Every filing also reports revenue for its own year and the prior year (so each carries an
annual-length duration and its fiscal-year label).
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.eval.keys.base import SecSource
from src.eval.keys.statement_witness import (
    load_note_concepts,
    load_resolutions,
    load_statement_rules,
)
from src.eval.suites.v2_suite import FactStoreSource
from src.eval.suites.v2_witness import KeyWitness

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
RULES = FIX / "statement_lines.yaml"
NOTES = FIX / "note_concepts.yaml"
RESOLUTIONS = {r: FIX / f"key_resolutions_{r}.yaml" for r in ("none", "drop", "key")}

M = 1_000_000
G = "us-gaap:"

CIK = {"FALL": "0000810001", "FEXT": "0000810002", "FRST": "0000810003",
       "FDEF": "0000810004", "FNOP": "0000810005"}
NAMES = {"FALL": "Fall Industries Inc.", "FEXT": "Fext Systems Inc.", "FRST": "Frst Corp.",
         "FDEF": "Fdef Holdings Inc.", "FNOP": "Fnop Ltd."}

FA24, FA25 = "0000810001-25-000010", "0000810001-26-000010"
FE22, FE23, FE24 = "0000810002-23-000020", "0000810002-24-000020", "0000810002-25-000020"
FR22, FR23, FR24 = "0000810003-23-000030", "0000810003-24-000030", "0000810003-25-000030"
FD22, FD23, FD24 = "0000810004-23-000040", "0000810004-24-000040", "0000810004-25-000040"
FN25 = "0000810005-26-000050"

# accession -> (ticker, form, fiscal year, filed, FSDS quarter or None when not published)
FILINGS = {
    FA24: ("FALL", "10-K", 2024, "2025-02-10", "2025q1"),
    FA25: ("FALL", "10-K", 2025, "2026-02-10", None),
    FE22: ("FEXT", "10-K", 2022, "2023-02-20", "2023q1"),
    FE23: ("FEXT", "10-K", 2023, "2024-02-20", "2024q1"),
    FE24: ("FEXT", "10-K", 2024, "2025-02-20", "2025q1"),
    FR22: ("FRST", "10-K", 2022, "2023-02-21", "2023q1"),
    FR23: ("FRST", "10-K", 2023, "2024-02-21", "2024q1"),
    FR24: ("FRST", "10-K", 2024, "2025-02-21", "2025q1"),
    FD22: ("FDEF", "10-K", 2022, "2023-02-22", "2023q1"),
    FD23: ("FDEF", "10-K", 2023, "2024-02-22", "2024q1"),
    FD24: ("FDEF", "10-K", 2024, "2025-02-22", "2025q1"),
    FN25: ("FNOP", "10-K", 2025, "2026-02-25", None),
}
QUARTERS = ("2023q1", "2024q1", "2025q1")

# companyfacts: (accession, concept, fiscal year of the period, millions)
FACTS = [
    (FA24, G + "Revenues", 2023, 4000), (FA24, G + "Revenues", 2024, 5000),
    (FA25, G + "Revenues", 2024, 5000), (FA25, G + "Revenues", 2025, 5500),
    (FA24, G + "NetCashProvidedByUsedInFinancingActivities", 2024, -10764),
    (FA25, G + "NetCashProvidedByUsedInFinancingActivities", 2024, -10764),
    (FA25, G + "NetCashProvidedByUsedInFinancingActivities", 2025, -9000),
    (FA24, G + "Depreciation", 2024, 800), (FA25, G + "Depreciation", 2024, 800),
    (FA24, G + "DepreciationDepletionAndAmortization", 2024, 1000),
    (FA25, G + "DepreciationDepletionAndAmortization", 2024, 1000),
    (FA24, G + "PaymentsToAcquirePropertyPlantAndEquipment", 2024, 500),
    (FA25, G + "PaymentsToAcquirePropertyPlantAndEquipment", 2024, 520),
    (FA25, G + "PaymentsToAcquirePropertyPlantAndEquipment", 2025, 600),
    (FA24, G + "InterestAndOtherIncome", 2024, 169),
    (FA25, G + "InterestAndOtherIncome", 2024, 169),
    (FE22, G + "Revenues", 2021, 9000), (FE22, G + "Revenues", 2022, 10000),
    (FE23, G + "Revenues", 2022, 10000), (FE23, G + "Revenues", 2023, 12000),
    (FE24, G + "Revenues", 2023, 12000), (FE24, G + "Revenues", 2024, 15000),
    (FE24, G + "PaymentsToAcquireProductiveAssets", 2022, 976),
    (FE24, G + "PaymentsToAcquireProductiveAssets", 2023, 1833),
    (FE24, G + "PaymentsToAcquireProductiveAssets", 2024, 1069),
    (FR22, G + "Revenues", 2021, 9000), (FR22, G + "Revenues", 2022, 10000),
    (FR23, G + "Revenues", 2022, 10000), (FR23, G + "Revenues", 2023, 12000),
    (FR24, G + "Revenues", 2023, 12000), (FR24, G + "Revenues", 2024, 15000),
    (FR24, G + "PaymentsToAcquireProductiveAssets", 2022, 990),
    (FR24, G + "PaymentsToAcquireProductiveAssets", 2023, 1833),
    (FR24, G + "PaymentsToAcquireProductiveAssets", 2024, 1100),
    (FD22, G + "Revenues", 2021, 9000), (FD22, G + "Revenues", 2022, 10000),
    (FD23, G + "Revenues", 2022, 10000), (FD23, G + "Revenues", 2023, 12000),
    (FD24, G + "Revenues", 2023, 12000), (FD24, G + "Revenues", 2024, 15000),
    (FD24, G + "PaymentsToAcquireProductiveAssets", 2022, 700),
    (FD24, G + "PaymentsToAcquireProductiveAssets", 2023, 800),
    (FD24, G + "PaymentsToAcquireProductiveAssets", 2024, 900),
    (FN25, G + "Revenues", 2025, 2000),
    (FN25, G + "PaymentsToAcquirePropertyPlantAndEquipment", 2025, 300),
]

EXT_CAPEX = "PurchasesOfPropertyAndEquipmentAndIntangibleAssets"
# FSDS presentation: accession -> [(stmt, report, line, tag, taxonomy ("ext" = the filing's own
# extension), plabel, {fiscal year: millions})]
LINES = {
    FA24: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2024: 5000, 2023: 4000}),
           ("IS", 2, 2, "InterestAndOtherIncome", "us-gaap", "Interest income and other, net",
            {2024: 169}),
           ("CF", 4, 1, "DepreciationDepletionAndAmortization", "us-gaap",
            "Depreciation, depletion and amortization", {2024: 1000}),
           ("CF", 4, 2, "PaymentsToAcquirePropertyPlantAndEquipment", "us-gaap",
            "Purchases of property and equipment", {2024: 500}),
           ("CF", 4, 3, "NetCashProvidedByUsedInFinancingActivities", "us-gaap",
            "Net cash used in financing activities", {2024: -10764})],
    FE22: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2022: 10000, 2021: 9000}),
           ("CF", 4, 1, EXT_CAPEX, "ext",
            "Purchases of property and equipment and intangible assets", {2022: 976})],
    FE23: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2023: 12000, 2022: 10000}),
           ("CF", 4, 1, EXT_CAPEX, "ext",
            "Purchases of property and equipment and intangible assets",
            {2023: 1833, 2022: 976})],
    FE24: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2024: 15000, 2023: 12000}),
           ("CF", 4, 1, "PaymentsToAcquireProductiveAssets", "us-gaap",
            "Purchases related to property and equipment and intangible assets",
            {2024: 1069, 2023: 1833, 2022: 976})],
    FR22: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2022: 10000, 2021: 9000}),
           ("CF", 4, 1, EXT_CAPEX, "ext",
            "Purchases of property and equipment and intangible assets", {2022: 976})],
    FR23: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2023: 12000, 2022: 10000}),
           ("CF", 4, 1, EXT_CAPEX, "ext",
            "Purchases of property and equipment and intangible assets",
            {2023: 1833, 2022: 976})],
    FR24: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2024: 15000, 2023: 12000}),
           ("CF", 4, 1, "PaymentsToAcquireProductiveAssets", "us-gaap",
            "Purchases related to property and equipment and intangible assets",
            {2024: 1100, 2023: 1833, 2022: 990})],
    FD22: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2022: 10000, 2021: 9000}),
           ("CF", 4, 1, "PurchasesOfPropertyAndEquipment", "ext",
            "Purchases of property and equipment", {2022: 700})],
    FD23: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2023: 12000, 2022: 10000}),
           ("CF", 4, 1, "PurchasesOfPropertyAndEquipment", "ext",
            "Purchases of property and equipment", {2023: 800, 2022: 700})],
    FD24: [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2024: 15000, 2023: 12000}),
           ("CF", 4, 1, "PaymentsToAcquireProductiveAssets", "us-gaap",
            "Purchases of property and equipment and businesses",
            {2024: 900, 2023: 800, 2022: 700})],
}
# The FY2025 10-K of FALL, as the FSDS quarter SEC has not published yet would hold it (only the
# metamorphic test of C6 writes it, into its own quarter 2026q1).
FA25_LINES = [("IS", 2, 1, "Revenues", "us-gaap", "Total revenues", {2025: 5500, 2024: 5000}),
              ("CF", 4, 3, "NetCashProvidedByUsedInFinancingActivities", "us-gaap",
               "Net cash used in financing activities", {2025: -9000, 2024: -10764})]

SUB_COLS = ("adsh", "cik", "name", "form", "period", "fy", "fp", "filed")
PRE_COLS = ("adsh", "report", "line", "stmt", "inpth", "rfile", "tag", "version", "plabel",
            "negating")
NUM_COLS = ("adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value",
            "footnote")
TAG_COLS = ("tag", "version", "custom", "abstract", "datatype", "iord", "crdr", "tlabel", "doc")


def period(year: int) -> tuple[str, str]:
    """A December fiscal year's companyfacts period."""
    return f"{year}-01-01", f"{year}-12-31"


def labels(ticker: str) -> dict[str, int]:
    """The filer's fiscal-year labels, written by hand from FILINGS (DEI fiscal year)."""
    return {a: fy for a, (t, _f, fy, _d, _q) in FILINGS.items() if t == ticker}


def companyfacts(ticker: str) -> dict:
    """The filer's companyfacts document (SEC API shape), from FACTS."""
    facts: dict = {}
    for accn, concept, year, millions in FACTS:
        t, form, fy, filed, _q = FILINGS[accn]
        if t != ticker:
            continue
        tax, tag = concept.split(":")
        start, end = period(year)
        facts.setdefault(tax, {}).setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(
            {"start": start, "end": end, "val": millions * M, "accn": accn, "fy": fy,
             "fp": "FY", "form": form, "filed": filed})
    return {"cik": int(CIK[ticker]), "entityName": NAMES[ticker], "facts": facts}


def _version(taxonomy: str, adsh: str) -> str:
    return adsh if taxonomy == "ext" else f"{taxonomy}/2024"


def fsds_rows(adsh: str, lines: list) -> dict[str, list[dict[str, str]]]:
    """One filing's FSDS rows (sub, pre, num) from its presentation lines."""
    ticker, form, fy, filed, _q = FILINGS[adsh]
    sub = [{"adsh": adsh, "cik": str(int(CIK[ticker])), "name": NAMES[ticker].upper(),
            "form": form, "period": f"{fy}1231", "fy": str(fy), "fp": "FY",
            "filed": filed.replace("-", "")}]
    pre, num = [], []
    for stmt, report, line, tag, taxonomy, plabel, values in lines:
        version = _version(taxonomy, adsh)
        pre.append({"adsh": adsh, "report": str(report), "line": str(line), "stmt": stmt,
                    "inpth": "0", "rfile": "H", "tag": tag, "version": version,
                    "plabel": plabel, "negating": "0"})
        for year, millions in values.items():
            num.append({"adsh": adsh, "tag": tag, "version": version, "ddate": f"{year}1231",
                        "qtrs": "4", "uom": "USD", "segments": "", "coreg": "",
                        "value": f"{millions * M:.4f}", "footnote": ""})
    return {"sub": sub, "pre": pre, "num": num}


def tables(*adshs: str) -> dict[str, list[dict[str, str]]]:
    """In-memory FSDS tables of the named published filings (``FsdsTables`` shape)."""
    out: dict[str, list[dict[str, str]]] = {"sub": [], "pre": [], "num": [], "tag": []}
    for a in adshs:
        rows = fsds_rows(a, LINES[a])
        for name in ("sub", "pre", "num"):
            out[name].extend(rows[name])
    return out


def all_tables() -> dict[str, list[dict[str, str]]]:
    """Every published filing's rows."""
    return tables(*LINES)


def _write_tsv(path: Path, cols: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    text = "\t".join(cols) + "\n" + "".join("\t".join(r[c] for c in cols) + "\n" for r in rows)
    path.write_bytes(text.encode("utf-8"))


def write_quarter(root: Path, quarter: str, filings: dict[str, list]) -> Path:
    """Writes one FSDS quarter directory holding the given filings' rows."""
    d = root / quarter
    d.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[dict[str, str]]] = {"sub": [], "pre": [], "num": []}
    for adsh, lines in filings.items():
        for name, part in fsds_rows(adsh, lines).items():
            rows[name].extend(part)
    _write_tsv(d / "sub.tsv", SUB_COLS, rows["sub"])
    _write_tsv(d / "pre.tsv", PRE_COLS, rows["pre"])
    _write_tsv(d / "num.tsv", NUM_COLS, rows["num"])
    _write_tsv(d / "tag.tsv", TAG_COLS, [])
    return d


class FakeSec(SecSource):
    """Serves the fixture companyfacts documents (a copy per call); frames are never read."""

    def company_facts(self, cik: str) -> dict:
        ticker = next(t for t, c in CIK.items() if c == str(cik).zfill(10))
        return copy.deepcopy(companyfacts(ticker))

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("the build must not read frames")


class NoStore(FactStoreSource):
    """A fact store that holds nothing (the cross-check is not under test)."""

    def value(self, cik, concept, unit, period_start, period_end, accession):
        return None


def spec(concept: str) -> dict[str, str]:
    """An inventory spec ``{"concept", "unit"}`` in USD."""
    return {"concept": concept, "unit": "USD"}


@pytest.fixture(scope="session")
def kit(tmp_path_factory):
    root = tmp_path_factory.mktemp("fsds")
    dirs = {q: write_quarter(root, q, {a: LINES[a] for a, f in FILINGS.items() if f[4] == q})
            for q in QUARTERS}
    extra = tmp_path_factory.mktemp("fsds_extra")
    q2026 = write_quarter(extra, "2026q1", {FA25: FA25_LINES})

    def witness(quarters=QUARTERS, resolutions: str = "none", candidates=None,
                more=()) -> KeyWitness:
        """A real witness from quarter directories (``more``: further directories)."""
        paths = [dirs[q] for q in quarters] + list(more)
        res = RESOLUTIONS[resolutions]
        return KeyWitness.from_paths(paths, rules=RULES, notes=NOTES, resolutions=res,
                                     candidates=candidates, locked=res)

    def env():
        return SimpleNamespace(rules=load_statement_rules(RULES),
                               notes=load_note_concepts(NOTES),
                               resolutions={r: load_resolutions(p)
                                            for r, p in RESOLUTIONS.items()})

    return SimpleNamespace(
        M=M, G=G, CIK=CIK, FILINGS=FILINGS, LINES=LINES, QUARTERS=QUARTERS,
        FA24=FA24, FA25=FA25, FE22=FE22, FE23=FE23, FE24=FE24, FR22=FR22, FR23=FR23,
        FR24=FR24, FD22=FD22, FD23=FD23, FD24=FD24, FN25=FN25, EXT_CAPEX=EXT_CAPEX,
        period=period, labels=labels, companyfacts=companyfacts, tables=tables,
        all_tables=all_tables, fsds_rows=fsds_rows, witness=witness, env=env, spec=spec,
        FakeSec=FakeSec, NoStore=NoStore, q2026=q2026, FA25_LINES=FA25_LINES,
    )
