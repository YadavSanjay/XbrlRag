"""Fixtures, fakes and the hand-written synthetic filings of the key-currency-and-definition tests.

Spec: docs/specs/key-currency-and-definition.md. Every filing below is made up (CIKs 0000830001
to 0000830006, December fiscal years, round amounts). The companyfacts documents (SEC API
shape) and the FSDS-shaped ``sub``/``pre``/``num`` rows are built from the two tables here, so
every expected value in the tests is read off them. The only fakes sit at the I/O seams
(``SecSource``, ``FactStoreSource``). No network, no ``data/``, no database, no clock.

All filings are the FY2023 annual report, in FSDS quarter 2024q1. Amounts in millions:

    USDA  10-K, reports USD. Revenues 8,000 / 9,000 (FY2022 / FY2023).
          CF "Proceeds from issuance of long-term debt" (ProceedsFromIssuanceOfLongTermDebt)
          600 / 1,000. BS "Short-term borrowings" (ShortTermBorrowings) 150 / 200 and
          "Long-term debt" (LongTermDebtNoncurrent) 2,800 / 3,000.
    USDB  10-K, reports USD. Revenues 4,000 / 5,000. CF "Proceeds from issuance of debt"
          (ProceedsFromIssuanceOfDebt) 300 / 500. BS "Long-term debt" 1,400 / 1,500.
    TWDC  20-F, reports TWD, with a USD convenience translation of FY2023 only (TSMC's
          pattern). ifrs-full:Revenue TWD 60,000 / 70,000, USD 2,286 (FY2023).
          CF "Proceeds from long-term borrowings" (ProceedsFromNoncurrentBorrowings, not a
          stated substitute) TWD 50,000 / 85,700, USD 2,799. BS "Current portion of
          long-term borrowings" (CurrentPortionOfLongtermBorrowings) TWD 9,000 / 10,000, USD 326;
          "Bonds payable"
          (NoncurrentPortionOfNoncurrentBondsIssued) TWD 90,000 / 100,000, USD 3,260.
    TWDK  20-F, reports TWD with a FY2023 USD translation. ifrs-full:Revenue TWD 30,000 /
          32,000, USD 1,045. CF "Proceeds from long-term bank loans"
          (ProceedsFromNoncurrentBorrowings) TWD 2,400 / 2,450, USD 80.
    IFRU  20-F, IFRS, reports USD. ifrs-full:Revenue 7,000 / 7,500. CF "Proceeds from
          issuance of bonds" (ProceedsFromIssueOfBondsNotesAndDebentures) 650 / 700.
    AMDX  10-K, reports USD. Revenues 6,000 / 6,500. CF "Proceeds from debt, net of
          issuance costs" tagged us-gaap:ProceedsFromShortTermDebt 991 / 250 (AMD's pattern).

The build inventory keys USDA's DebtIssued and Debt and TWDK's DebtIssued (on its USD row, as
an inventory read from a USD-filtered view would); USDB and TWDC are in the corpus for FY2023
with no key, so they reach the statement-line and defined-family paths. Universe ``semis`` is
[TWDC, TWDK, USDA, USDB].
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
from src.eval.suites.intents import load_lexicon, load_phrasebook
from src.eval.suites.v2_suite import FactStoreSource
from src.eval.suites.v2_witness import KeyWitness

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
REPO = HERE.parents[2]
LIVE = REPO / "configs" / "eval" / "v2"
LIVE_COVERAGE = REPO / "configs" / "concept_mapping" / "concept_family_coverage.yaml"
RULES = FIX / "statement_lines.yaml"
NOTES = FIX / "note_concepts.yaml"
RESOLUTIONS = FIX / "key_resolutions.yaml"
ELEMENT_FIT = FIX / "element_fit.yaml"

M = 1_000_000
G = "us-gaap:"
I = "ifrs-full:"  # noqa: E741 - the taxonomy prefix, read beside G

CIK = {"USDA": "0000830001", "USDB": "0000830002", "TWDC": "0000830003",
       "TWDK": "0000830004", "IFRU": "0000830005", "AMDX": "0000830006"}
NAMES = {"USDA": "Usda Inc.", "USDB": "Usdb Corp.", "TWDC": "Twdc Ltd.", "TWDK": "Twdk Ltd.",
         "IFRU": "Ifru plc", "AMDX": "Amdx Inc."}
ADSH = {"USDA": "0000830001-24-000010", "USDB": "0000830002-24-000020",
        "TWDC": "0000830003-24-000030", "TWDK": "0000830004-24-000040",
        "IFRU": "0000830005-24-000050", "AMDX": "0000830006-24-000060"}
FORM = {"USDA": "10-K", "USDB": "10-K", "TWDC": "20-F", "TWDK": "20-F", "IFRU": "20-F",
        "AMDX": "10-K"}
FILED = {"USDA": "2024-02-10", "USDB": "2024-02-12", "TWDC": "2024-04-18",
         "TWDK": "2024-04-19", "IFRU": "2024-04-20", "AMDX": "2024-02-14"}
YEAR = 2023
QUARTER = "2024q1"

BONDS = I + "ProceedsFromIssueOfBondsNotesAndDebentures"
BANK_LOANS = I + "ProceedsFromNoncurrentBorrowings"
SHORT_TERM = G + "ProceedsFromShortTermDebt"

# companyfacts: ticker -> [(concept, kind "D" duration | "I" instant, fiscal year, unit,
# millions)]; every fact is filed in the ticker's FY2023 annual report.
FACTS = {
    "USDA": [(G + "Revenues", "D", 2022, "USD", 8000), (G + "Revenues", "D", 2023, "USD", 9000),
             (G + "ProceedsFromIssuanceOfLongTermDebt", "D", 2022, "USD", 600),
             (G + "ProceedsFromIssuanceOfLongTermDebt", "D", 2023, "USD", 1000),
             (G + "ShortTermBorrowings", "I", 2022, "USD", 150),
             (G + "ShortTermBorrowings", "I", 2023, "USD", 200),
             (G + "LongTermDebtNoncurrent", "I", 2022, "USD", 2800),
             (G + "LongTermDebtNoncurrent", "I", 2023, "USD", 3000)],
    "USDB": [(G + "Revenues", "D", 2022, "USD", 4000), (G + "Revenues", "D", 2023, "USD", 5000),
             (G + "ProceedsFromIssuanceOfDebt", "D", 2022, "USD", 300),
             (G + "ProceedsFromIssuanceOfDebt", "D", 2023, "USD", 500),
             (G + "LongTermDebtNoncurrent", "I", 2022, "USD", 1400),
             (G + "LongTermDebtNoncurrent", "I", 2023, "USD", 1500)],
    "TWDC": [(I + "Revenue", "D", 2022, "TWD", 60000), (I + "Revenue", "D", 2023, "TWD", 70000),
             (I + "Revenue", "D", 2023, "USD", 2286),
             (BANK_LOANS, "D", 2022, "TWD", 50000), (BANK_LOANS, "D", 2023, "TWD", 85700),
             (BANK_LOANS, "D", 2023, "USD", 2799),
             (I + "CurrentPortionOfLongtermBorrowings", "I", 2022, "TWD", 9000),
             (I + "CurrentPortionOfLongtermBorrowings", "I", 2023, "TWD", 10000),
             (I + "CurrentPortionOfLongtermBorrowings", "I", 2023, "USD", 326),
             (I + "NoncurrentPortionOfNoncurrentBondsIssued", "I", 2022, "TWD", 90000),
             (I + "NoncurrentPortionOfNoncurrentBondsIssued", "I", 2023, "TWD", 100000),
             (I + "NoncurrentPortionOfNoncurrentBondsIssued", "I", 2023, "USD", 3260)],
    "TWDK": [(I + "Revenue", "D", 2022, "TWD", 30000), (I + "Revenue", "D", 2023, "TWD", 32000),
             (I + "Revenue", "D", 2023, "USD", 1045),
             (BANK_LOANS, "D", 2022, "TWD", 2400), (BANK_LOANS, "D", 2023, "TWD", 2450),
             (BANK_LOANS, "D", 2023, "USD", 80)],
    "IFRU": [(I + "Revenue", "D", 2022, "USD", 7000), (I + "Revenue", "D", 2023, "USD", 7500),
             (BONDS, "D", 2022, "USD", 650), (BONDS, "D", 2023, "USD", 700)],
    "AMDX": [(G + "Revenues", "D", 2022, "USD", 6000), (G + "Revenues", "D", 2023, "USD", 6500),
             (SHORT_TERM, "D", 2022, "USD", 991), (SHORT_TERM, "D", 2023, "USD", 250)],
}

# FSDS presentation: ticker -> [(stmt, report, line, concept, plabel)]; each line's values are
# its concept's FACTS rows (every unit), so the statement and companyfacts agree.
LINES = {
    "USDA": [("IS", 2, 1, G + "Revenues", "Total revenues"),
             ("BS", 3, 1, G + "ShortTermBorrowings", "Short-term borrowings"),
             ("BS", 3, 2, G + "LongTermDebtNoncurrent", "Long-term debt"),
             ("CF", 4, 1, G + "ProceedsFromIssuanceOfLongTermDebt",
              "Proceeds from issuance of long-term debt")],
    "USDB": [("IS", 2, 1, G + "Revenues", "Total revenues"),
             ("BS", 3, 1, G + "LongTermDebtNoncurrent", "Long-term debt"),
             ("CF", 4, 1, G + "ProceedsFromIssuanceOfDebt", "Proceeds from issuance of debt")],
    "TWDC": [("IS", 2, 1, I + "Revenue", "Net revenue"),
             ("BS", 3, 1, I + "CurrentPortionOfLongtermBorrowings",
              "Current portion of long-term borrowings"),
             ("BS", 3, 2, I + "NoncurrentPortionOfNoncurrentBondsIssued", "Bonds payable"),
             ("CF", 4, 1, BANK_LOANS, "Proceeds from long-term borrowings")],
    "TWDK": [("IS", 2, 1, I + "Revenue", "Net revenue"),
             ("CF", 4, 1, BANK_LOANS, "Proceeds from long-term bank loans")],
    "IFRU": [("IS", 2, 1, I + "Revenue", "Revenue"),
             ("CF", 4, 1, BONDS, "Proceeds from issuance of bonds")],
    "AMDX": [("IS", 2, 1, G + "Revenues", "Net revenue"),
             ("CF", 4, 1, SHORT_TERM, "Proceeds from debt, net of issuance costs")],
}

SUB_COLS = ("adsh", "cik", "name", "form", "period", "fy", "fp", "filed")
PRE_COLS = ("adsh", "report", "line", "stmt", "inpth", "rfile", "tag", "version", "plabel",
            "negating")
NUM_COLS = ("adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value",
            "footnote")
TAG_COLS = ("tag", "version", "custom", "abstract", "datatype", "iord", "crdr", "tlabel", "doc")


def period(kind: str, year: int) -> tuple[str | None, str]:
    """A December fiscal year's companyfacts period: a duration or the year-end instant."""
    return (f"{year}-01-01" if kind == "D" else None), f"{year}-12-31"


def labels(ticker: str) -> dict[str, int]:
    """The filer's fiscal-year labels, written by hand: its one annual report is FY2023."""
    return {ADSH[ticker]: YEAR}


def companyfacts(ticker: str, drop_units: frozenset[str] = frozenset()) -> dict:
    """The filer's companyfacts document (SEC API shape), from FACTS.

    Args:
        ticker: The filer.
        drop_units: Units whose entries are left out (the metamorphic USD-only documents).
    """
    facts: dict = {}
    for concept, kind, year, unit, millions in FACTS[ticker]:
        if unit in drop_units:
            continue
        tax, tag = concept.split(":")
        start, end = period(kind, year)
        entry = {"end": end, "val": millions * M, "accn": ADSH[ticker], "fy": YEAR,
                 "fp": "FY", "form": FORM[ticker], "filed": FILED[ticker]}
        if start is not None:
            entry["start"] = start
        units = facts.setdefault(tax, {}).setdefault(tag, {"units": {}})["units"]
        units.setdefault(unit, []).append(entry)
    return {"cik": int(CIK[ticker]), "entityName": NAMES[ticker], "facts": facts}


def fsds_rows(ticker: str) -> dict[str, list[dict[str, str]]]:
    """One filing's FSDS rows (sub, pre, num) from its presentation lines and FACTS."""
    adsh = ADSH[ticker]
    sub = [{"adsh": adsh, "cik": str(int(CIK[ticker])), "name": NAMES[ticker].upper(),
            "form": FORM[ticker], "period": f"{YEAR}1231", "fy": str(YEAR), "fp": "FY",
            "filed": FILED[ticker].replace("-", "")}]
    pre, num = [], []
    for stmt, report, line, concept, plabel in LINES[ticker]:
        tax, tag = concept.split(":")
        version = ("ifrs/2023" if tax == "ifrs-full" else "us-gaap/2023")
        pre.append({"adsh": adsh, "report": str(report), "line": str(line), "stmt": stmt,
                    "inpth": "0", "rfile": "H", "tag": tag, "version": version,
                    "plabel": plabel, "negating": "0"})
        for c, kind, year, unit, millions in FACTS[ticker]:
            if c != concept:
                continue
            num.append({"adsh": adsh, "tag": tag, "version": version, "ddate": f"{year}1231",
                        "qtrs": "4" if kind == "D" else "0", "uom": unit, "segments": "",
                        "coreg": "", "value": f"{millions * M:.4f}", "footnote": ""})
    return {"sub": sub, "pre": pre, "num": num}


def tables(*tickers: str) -> dict[str, list[dict[str, str]]]:
    """In-memory FSDS tables of the named filers (``FsdsTables`` shape)."""
    out: dict[str, list[dict[str, str]]] = {"sub": [], "pre": [], "num": [], "tag": []}
    for t in tickers or tuple(ADSH):
        rows = fsds_rows(t)
        for name in ("sub", "pre", "num"):
            out[name].extend(rows[name])
    return out


def _write_tsv(path: Path, cols: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    text = "\t".join(cols) + "\n" + "".join("\t".join(r[c] for c in cols) + "\n" for r in rows)
    path.write_bytes(text.encode("utf-8"))


def write_quarter(root: Path) -> Path:
    """Writes the FSDS quarter directory holding every fixture filing."""
    d = root / QUARTER
    d.mkdir(parents=True, exist_ok=True)
    rows = tables()
    _write_tsv(d / "sub.tsv", SUB_COLS, rows["sub"])
    _write_tsv(d / "pre.tsv", PRE_COLS, rows["pre"])
    _write_tsv(d / "num.tsv", NUM_COLS, rows["num"])
    _write_tsv(d / "tag.tsv", TAG_COLS, [])
    return d


class FakeSec(SecSource):
    """Serves the fixture companyfacts documents (a copy per call); frames are never read.

    Args:
        drop_units: ticker -> units left out of that filer's document.
    """

    def __init__(self, drop_units: dict[str, frozenset[str]] | None = None) -> None:
        self.drop_units = drop_units or {}

    def company_facts(self, cik: str) -> dict:
        ticker = next(t for t, c in CIK.items() if c == str(cik).zfill(10))
        return copy.deepcopy(companyfacts(ticker, self.drop_units.get(ticker, frozenset())))

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("the build must not read frames")


class NoStore(FactStoreSource):
    """A fact store that holds nothing (the cross-check is not under test)."""

    def value(self, cik, concept, unit, period_start, period_end, accession):
        return None


def spec(concept: str) -> dict[str, str]:
    """An inventory spec ``{"concept", "unit"}`` in USD."""
    return {"concept": concept, "unit": "USD"}


def inventory() -> dict:
    """The build inventory (module docstring): USDA and TWDK keyed, USDB and TWDC not."""
    return {
        "USDA": {"cik": CIK["USDA"], "years": {YEAR: {
            "DebtIssued": spec(G + "ProceedsFromIssuanceOfLongTermDebt"),
            "Debt": spec(G + "LongTermDebtNoncurrent")}}},
        "USDB": {"cik": CIK["USDB"], "years": {YEAR: {}}},
        "TWDC": {"cik": CIK["TWDC"], "years": {YEAR: {}}},
        "TWDK": {"cik": CIK["TWDK"], "years": {YEAR: {"DebtIssued": spec(BANK_LOANS)}}},
    }


UNIVERSES = {"semis": ["TWDC", "TWDK", "USDA", "USDB"]}

# --- RSTD (requirement 6): a defined-family key read from the latest annual report's column.
#
#     RSTD  0000830007, reports USD. FY2023 10-K R23 (filed 2024-02-20): BS "Long-term debt"
#           (LongTermDebtNoncurrent) FY2022 5,500 / FY2023 5,713, no current line.
#           FY2024 10-K R24 (filed 2025-02-20): BS "Current portion of long-term debt"
#           (LongTermDebtCurrent) FY2023 75 / FY2024 80 and "Long-term debt" FY2023 5,713 /
#           FY2024 6,000. So FY2023's total debt is 5,713 in its own report and 5,788 as the
#           FY2024 10-K presents it (COST's FY2025 pattern). Revenues 3,000 / 3,200 / 3,500.
RSTD_CIK = "0000830007"
R23, R24 = "0000830007-24-000070", "0000830007-25-000070"
RSTD_FILINGS = {R23: (2023, "2024-02-20"), R24: (2024, "2025-02-20")}


def rstd_facts(current_2023: int = 75, noncurrent_2023_later: int = 5713) -> list[tuple]:
    """RSTD's facts ``(accession, concept, kind, fiscal year, millions)``; the two arguments
    are the FY2024 10-K's FY2023 comparative lines (the property test varies them)."""
    return [
        (R23, G + "Revenues", "D", 2022, 3000), (R23, G + "Revenues", "D", 2023, 3200),
        (R23, G + "LongTermDebtNoncurrent", "I", 2022, 5500),
        (R23, G + "LongTermDebtNoncurrent", "I", 2023, 5713),
        (R24, G + "Revenues", "D", 2023, 3200), (R24, G + "Revenues", "D", 2024, 3500),
        (R24, G + "LongTermDebtCurrent", "I", 2023, current_2023),
        (R24, G + "LongTermDebtCurrent", "I", 2024, 80),
        (R24, G + "LongTermDebtNoncurrent", "I", 2023, noncurrent_2023_later),
        (R24, G + "LongTermDebtNoncurrent", "I", 2024, 6000),
    ]


RSTD_LINES = {R23: [("BS", 3, 1, G + "LongTermDebtNoncurrent", "Long-term debt")],
              R24: [("BS", 3, 1, G + "LongTermDebtCurrent", "Current portion of long-term debt"),
                    ("BS", 3, 2, G + "LongTermDebtNoncurrent", "Long-term debt")]}


def rstd_companyfacts(facts: list[tuple], accessions: tuple[str, ...] = (R23, R24)) -> dict:
    """RSTD's companyfacts document from ``facts``, keeping only the named filings."""
    out: dict = {}
    for accn, concept, kind, year, millions in facts:
        if accn not in accessions:
            continue
        fy, filed = RSTD_FILINGS[accn]
        tax, tag = concept.split(":")
        start, end = period(kind, year)
        entry = {"end": end, "val": millions * M, "accn": accn, "fy": fy, "fp": "FY",
                 "form": "10-K", "filed": filed}
        if start is not None:
            entry["start"] = start
        units = out.setdefault(tax, {}).setdefault(tag, {"units": {}})["units"]
        units.setdefault("USD", []).append(entry)
    return {"cik": int(RSTD_CIK), "entityName": "Rstd Inc.", "facts": out}


def rstd_tables(facts: list[tuple], accessions: tuple[str, ...] = (R23, R24)) -> dict:
    """FSDS rows of RSTD's named filings (each line's values are its concept's facts)."""
    out: dict[str, list[dict[str, str]]] = {"sub": [], "pre": [], "num": [], "tag": []}
    for adsh in accessions:
        fy, filed = RSTD_FILINGS[adsh]
        out["sub"].append({"adsh": adsh, "cik": str(int(RSTD_CIK)), "name": "RSTD INC.",
                           "form": "10-K", "period": f"{fy}1231", "fy": str(fy), "fp": "FY",
                           "filed": filed.replace("-", "")})
        for stmt, report, line, concept, plabel in RSTD_LINES[adsh]:
            tag = concept.split(":")[1]
            out["pre"].append({"adsh": adsh, "report": str(report), "line": str(line),
                               "stmt": stmt, "inpth": "0", "rfile": "H", "tag": tag,
                               "version": "us-gaap/2023", "plabel": plabel, "negating": "0"})
            for accn, c, kind, year, millions in facts:
                if accn == adsh and c == concept:
                    out["num"].append({"adsh": adsh, "tag": tag, "version": "us-gaap/2023",
                                       "ddate": f"{year}1231",
                                       "qtrs": "4" if kind == "D" else "0", "uom": "USD",
                                       "segments": "", "coreg": "",
                                       "value": f"{millions * M:.4f}", "footnote": ""})
    return out


def rstd_labels(accessions: tuple[str, ...] = (R23, R24)) -> dict[str, int]:
    """RSTD's fiscal-year labels, by hand."""
    return {a: RSTD_FILINGS[a][0] for a in accessions}


@pytest.fixture(scope="session")
def kit(tmp_path_factory):
    root = tmp_path_factory.mktemp("fsds")
    quarter = write_quarter(root)

    def witness() -> KeyWitness:
        """A real witness from the quarter directory, built as the suite build builds it."""
        return KeyWitness.from_paths([quarter], rules=RULES, notes=NOTES,
                                     resolutions=RESOLUTIONS, locked=RESOLUTIONS)

    def plain_witness(rules=None) -> KeyWitness:
        """A witness built by the constructor (no mapping merged unless ``rules`` is merged)."""
        return KeyWitness(tables(), load_statement_rules(RULES) if rules is None else rules,
                          load_note_concepts(NOTES), load_resolutions(RESOLUTIONS))

    return SimpleNamespace(
        M=M, G=G, I=I, CIK=CIK, ADSH=ADSH, YEAR=YEAR, BONDS=BONDS, BANK_LOANS=BANK_LOANS,
        SHORT_TERM=SHORT_TERM, FACTS=FACTS, LINES=LINES, RULES=RULES, ELEMENT_FIT=ELEMENT_FIT,
        LIVE=LIVE, LIVE_COVERAGE=LIVE_COVERAGE, UNIVERSES=UNIVERSES,
        labels=labels, companyfacts=companyfacts, tables=tables, spec=spec,
        inventory=inventory, witness=witness, plain_witness=plain_witness,
        FakeSec=FakeSec, NoStore=NoStore,
        RSTD_CIK=RSTD_CIK, R23=R23, R24=R24, rstd_facts=rstd_facts,
        rstd_companyfacts=rstd_companyfacts, rstd_tables=rstd_tables, rstd_labels=rstd_labels,
        rules=lambda: load_statement_rules(RULES),
        lexicon=lambda: load_lexicon(FIX / "lexicon.yaml"),
        phrasebook=lambda: load_phrasebook(FIX / "phrasebook.yaml"),
    )
