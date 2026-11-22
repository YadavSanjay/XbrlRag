"""Fixtures for the key-statement-witness spec tests.

Spec: docs/specs/key-statement-witness.md. The committed excerpts under ``fixtures/`` are SEC
FSDS rows (byte-for-byte), SEC companyfacts entries and us-gaap 2025 label/documentation
text, each with its SHA-256 in ``fixtures/provenance.json``; the session fixture verifies
them before any test runs. No network, no database, nothing outside this directory except the
live ``configs/eval/v2`` copies the spec requires.

Every fixture filing is a DEV filing: each pinned fact is a dev key input of the frozen or
rebuilt v2 suites, or a prior-year column of a dev filing that is no key in any split.
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

# Accessions of the fixture filings (spec Notes).
AAPL = "0000320193-25-000079"   # 10-K FY2025
KO = "0001628280-26-010047"     # 10-K FY2025
META = "0001628280-26-003942"   # 10-K FY2025
ORCL = "0000950170-25-087926"   # 10-K FY2025 (2024-05-31 comparative)
MCD = "0000063908-26-000035"    # 10-K FY2025
VST = "0001692819-26-000006"    # 10-K FY2025
PG = "0000080424-26-000103"     # 10-K FY2026 (2025-06-30 comparative)
AMZN = "0001018724-26-000004"   # 10-K FY2025
AMD = "0000002488-26-000018"    # 10-K FY2025
ABT = "0001628280-26-010185"    # 10-K FY2025
LMT = "0001628280-26-004195"    # 10-K FY2025
TSLA = "0001628280-26-003952"   # 10-K FY2025
INTC = "0000050863-26-000011"   # 10-K FY2025
# A 10-K accession that no fixture quarter holds (a filing newer than the FSDS data).
ABSENT = "0000723125-26-000099"

CIK = {"AAPL": "0000320193", "KO": "0000021344", "META": "0001326801", "ORCL": "0001341439",
       "MCD": "0000063908", "VST": "0001692819", "PG": "0000080424", "AMZN": "0001018724",
       "AMD": "0000002488", "ABT": "0000001800", "LMT": "0000936468", "TSLA": "0001318605",
       "INTC": "0000050863"}

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


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


def raw_value(adsh: str, tag: str, version: str, ddate: str, qtrs: str, uom: str) -> float | None:
    """The raw consolidated ``num`` value of one key, or None (fixture rows are consolidated)."""
    vals = {r["value"] for r in raw_rows("num")
            if (r["adsh"], r["tag"], r["version"], r["ddate"], r["qtrs"], r["uom"])
            == (adsh, tag, version, ddate, qtrs, uom) and r["value"] != ""}
    assert len(vals) <= 1
    return float(vals.pop()) if vals else None


def raw_pre(adsh: str, report: int, line: int) -> dict[str, str]:
    """The raw ``pre`` row of one statement line."""
    rows = [r for r in raw_rows("pre")
            if r["adsh"] == adsh and r["report"] == str(report) and r["line"] == str(line)]
    assert len(rows) == 1, (adsh, report, line)
    return rows[0]


def _companyfacts() -> dict:
    return json.loads((FIXTURES / "companyfacts_excerpt.json").read_text(encoding="utf-8"))


def key(ticker: str, concept: str, accession: str, period_end: str, period_start: str | None,
        fiscal_year: int, unit: str = "USD", value: float | None = None) -> dict:
    """An ``AnswerKey`` built from the companyfacts excerpt entry of ``accession``.

    ``value`` overrides the entry's value (a constructed key, named as such in its test).
    """
    cik = CIK[ticker]
    tax, tag = concept.split(":", 1)
    entries = _companyfacts()["facts_by_cik"][str(int(cik))][tax][tag][unit]
    hits = [e for e in entries
            if e["accn"] == accession and e["end"] == period_end and e.get("start") == period_start]
    assert len(hits) == 1, (ticker, concept, accession, period_end)
    e = hits[0]
    return {
        "value": float(e["val"]) if value is None else value, "unit": unit, "decimals": -6,
        "concept": concept, "cik": cik, "period_start": period_start, "period_end": period_end,
        "fiscal_year": fiscal_year, "calendar_year": None, "accession": accession,
        "filed": e["filed"], "form": e["form"], "key_source": "sec_companyfacts",
        "selection": "latest_filed", "policies": ["S1", "S2", "S6"], "status": "verified",
        "verified_by": [],
    }


def cf_value(ticker: str, concept: str, accession: str, period_end: str,
             period_start: str | None, unit: str = "USD") -> float:
    """The companyfacts value of ``concept`` in ``accession`` (an ``annual_value``)."""
    return key(ticker, concept, accession, period_end, period_start, 0, unit)["value"]


_ENV: list[SimpleNamespace] = []


def env() -> SimpleNamespace:
    """The loaded tables, rules, note list and locked resolutions.

    Called inside a test body (never in fixture setup), because the loaders are under test.
    """
    if not _ENV:
        from src.eval.keys.statement_witness import (
            load_note_concepts,
            load_resolutions,
            load_statement_rules,
        )
        from src.eval.sec_reference.fsds import load_tables

        _ENV.append(SimpleNamespace(
            tables=load_tables(FSDS_DIRS),
            rules=load_statement_rules(FIXTURES / "statement_lines.yaml"),
            notes=load_note_concepts(FIXTURES / "note_concepts.yaml"),
            resolutions=load_resolutions(FIXTURES / "key_resolutions.yaml"),
        ))
    return _ENV[0]


@pytest.fixture(scope="session")
def kit():
    """Fixture paths, accessions, helpers; verifies every fixture file's SHA-256 first."""
    prov = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    for rel, digest in prov["files"].items():
        assert _sha(FIXTURES / rel) == digest, f"fixture {rel} differs from its provenance"
    return SimpleNamespace(env=env,
        fixtures=FIXTURES, dirs=FSDS_DIRS, live=LIVE, repo=REPO, provenance=prov,
        rules_path=FIXTURES / "statement_lines.yaml", notes_path=FIXTURES / "note_concepts.yaml",
        resolutions_path=FIXTURES / "key_resolutions.yaml",
        taxonomy=json.loads((FIXTURES / "taxonomy_excerpt.json").read_text(encoding="utf-8")),
        answers=json.loads((FIXTURES / "recorded_answers.json").read_text(encoding="utf-8")),
        raw_rows=raw_rows, raw_value=raw_value, raw_pre=raw_pre, key=key, cf_value=cf_value,
        sha=_sha, copy=copy.deepcopy, CIK=CIK, ANNUAL_FORMS=ANNUAL_FORMS,
        AAPL=AAPL, KO=KO, META=META, ORCL=ORCL, MCD=MCD, VST=VST, PG=PG, AMZN=AMZN, AMD=AMD,
        ABT=ABT, LMT=LMT, TSLA=TSLA, INTC=INTC, ABSENT=ABSENT,
    )
