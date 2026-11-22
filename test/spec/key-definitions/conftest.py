"""Fixtures for the key-definitions spec tests.

Spec: docs/specs/key-definitions.md. The committed excerpts under ``fixtures/`` are SEC FSDS
rows (byte-for-byte), SEC companyfacts entries of the fixture filings and us-gaap 2025 label
and documentation text, each with its SHA-256 in ``fixtures/provenance.json``; the session
fixture verifies them before any test runs. No network, no database, nothing outside this
directory except the live ``configs/eval/v2`` files requirement 7 names and the retired
key-statement-witness locked note list.

Every pinned fact is a dev key input of the frozen or rebuilt v2 suites, or a fact that is no
key in any split; validation-split keys were swapped for other columns of the same dev
filings (spec Notes, "Split check").
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FSDS_DIRS = sorted(p for p in (FIXTURES / "fsds").iterdir() if p.is_dir())
REPO = Path(__file__).resolve().parents[3]
LIVE = REPO / "configs" / "eval" / "v2"
KSW_FIXTURES = REPO / "test" / "spec" / "key-statement-witness" / "fixtures"

# Fixture filings: ticker -> (10-digit CIK, accession). All annual 10-Ks.
FILINGS = {
    "AAPL": ("0000320193", "0000320193-25-000079"),   # FY2025, 2025-09-27
    "KO": ("0000021344", "0001628280-26-010047"),     # FY2025
    "WMT": ("0000104169", "0000104169-25-000021"),    # FY2025, 2025-01-31
    "TSLA": ("0001318605", "0001628280-26-003952"),   # FY2025
    "MCD": ("0000063908", "0000063908-26-000035"),    # FY2025
    "LLY": ("0000059478", "0000059478-26-000013"),    # FY2025
    "AMZN": ("0001018724", "0001018724-26-000004"),   # FY2025
    "GOOGL": ("0001652044", "0001652044-26-000018"),  # FY2025
    "CSCO": ("0000858877", "0000858877-25-000111"),   # FY2025, 2025-07-26
    "ORCL": ("0001341439", "0000950170-25-087926"),   # FY2025 (2024-05-31 comparative)
    "LMT": ("0000936468", "0001628280-26-004195"),    # FY2025
    "META": ("0001326801", "0001628280-26-003942"),   # FY2025 (2024-12-31 comparative)
    "INTC": ("0000050863", "0000050863-26-000011"),   # FY2025 (FY2023 comparative)
    "AVGO": ("0001730168", "0001730168-25-000121"),   # FY2025 (FY2024 comparative)
    "VST": ("0001692819", "0001692819-26-000006"),    # FY2025
}
# A 10-K accession that no fixture quarter holds.
ABSENT = "0000723125-26-000099"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_rows(table: str) -> list[dict[str, str]]:
    """Every row of ``table`` across the fixture quarters: a plain split on newline and tab."""
    out: list[dict[str, str]] = []
    for d in FSDS_DIRS:
        lines = (d / f"{table}.tsv").read_bytes().decode("utf-8").split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        header = lines[0].split("\t")
        out.extend(dict(zip(header, ln.split("\t"), strict=True)) for ln in lines[1:])
    return out


def raw_pre(adsh: str, report: int, line: int) -> dict[str, str]:
    """The raw ``pre`` row of one statement line."""
    rows = [r for r in raw_rows("pre")
            if r["adsh"] == adsh and r["report"] == str(report) and r["line"] == str(line)]
    assert len(rows) == 1, (adsh, report, line)
    return rows[0]


def companyfacts(ticker: str) -> dict:
    """The filer's companyfacts document (SEC's shape), holding its fixture filing's entries."""
    cik = str(int(FILINGS[ticker][0]))
    doc = json.loads((FIXTURES / "companyfacts_excerpt.json").read_text(encoding="utf-8"))
    return doc["facts_by_cik"][cik]


def cf_entries(doc: dict, tag: str, unit: str = "USD") -> list[dict]:
    """The us-gaap entries of ``tag`` in a companyfacts document (a live list, for editing)."""
    return doc["facts"]["us-gaap"][tag]["units"][unit]


_ENV: list[SimpleNamespace] = []


def env() -> SimpleNamespace:
    """The loaded tables and locked rules; called inside a test body (loaders are product code)."""
    if not _ENV:
        from src.eval.keys.statement_witness import load_statement_rules
        from src.eval.sec_reference.fsds import load_tables

        _ENV.append(SimpleNamespace(
            tables=load_tables(FSDS_DIRS),
            rules=load_statement_rules(FIXTURES / "statement_lines_kd.yaml"),
        ))
    return _ENV[0]


def tables_copy() -> dict:
    """A deep copy of the fixture tables, for metamorphic edits."""
    return {k: [dict(r) for r in v] for k, v in env().tables.items()}


def add_line(tables: dict, adsh: str, report: int, line: int, plabel: str, tag: str,
             ddate: str, value: float, stmt: str = "BS") -> None:
    """Appends one synthetic consolidated statement line (an extension tag) with its value."""
    tables["pre"].append({"adsh": adsh, "report": str(report), "line": str(line), "stmt": stmt,
                          "inpth": "0", "rfile": "H", "tag": tag, "version": adsh,
                          "plabel": plabel, "negating": "0"})
    tables["num"].append({"adsh": adsh, "tag": tag, "version": adsh, "ddate": ddate,
                          "qtrs": "0" if stmt == "BS" else "4", "uom": "USD", "segments": "",
                          "coreg": "", "value": repr(value), "footnote": ""})


def drop_lines(tables: dict, adsh: str, tags: set[str]) -> None:
    """Removes the statement lines (and values) of ``tags`` from one filing."""
    tables["pre"] = [r for r in tables["pre"] if not (r["adsh"] == adsh and r["tag"] in tags)]
    tables["num"] = [r for r in tables["num"] if not (r["adsh"] == adsh and r["tag"] in tags)]


def debt(ticker: str, period_end: str, fiscal_year: int, tables: dict | None = None,
         cf: dict | None = None):
    """``debt_key`` for a fixture filing (optionally on edited tables or companyfacts)."""
    from src.eval.keys.key_definitions import debt_key

    cik, adsh = FILINGS[ticker]
    return debt_key(tables if tables is not None else env().tables, env().rules,
                    cf if cf is not None else companyfacts(ticker), cik, fiscal_year, adsh,
                    period_end, "USD")


def deferred(ticker: str, period_end: str, fiscal_year: int, tables: dict | None = None,
             cf: dict | None = None):
    """``deferred_revenue_key`` for a fixture filing."""
    from src.eval.keys.key_definitions import deferred_revenue_key

    cik, adsh = FILINGS[ticker]
    return deferred_revenue_key(tables if tables is not None else env().tables, env().rules,
                                cf if cf is not None else companyfacts(ticker), cik,
                                fiscal_year, adsh, period_end, "USD")


@pytest.fixture(scope="session")
def kit():
    """Fixture paths, filings and helpers; verifies every fixture file's SHA-256 first."""
    prov = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    for rel, digest in prov["files"].items():
        assert _sha(FIXTURES / rel) == digest, f"fixture {rel} differs from its provenance"
    return SimpleNamespace(
        env=env, fixtures=FIXTURES, live=LIVE, repo=REPO, ksw=KSW_FIXTURES, provenance=prov,
        FILINGS=FILINGS, ABSENT=ABSENT, raw_rows=raw_rows, raw_pre=raw_pre,
        companyfacts=companyfacts, cf_entries=cf_entries, tables_copy=tables_copy,
        add_line=add_line, drop_lines=drop_lines, debt=debt, deferred=deferred,
        copy=copy.deepcopy, sha=_sha,
        rules_path=FIXTURES / "statement_lines_kd.yaml",
        taxonomy=json.loads((FIXTURES / "taxonomy_excerpt.json").read_text(encoding="utf-8")),
    )
