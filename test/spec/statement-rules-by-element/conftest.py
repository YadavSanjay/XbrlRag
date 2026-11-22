"""Fixtures for the statement-rules-by-element spec tests.

Spec: docs/specs/statement-rules-by-element.md. The committed excerpts under ``fixtures/fsds``
are SEC Financial Statement Data Sets rows, byte for byte, of two corpus filings (TSM's FY2024
20-F balance sheet and TSLA's FY2025 10-K cash-flow statement), each file's SHA-256 in
``fixtures/provenance.json`` with the quarter zips' and primary documents' SHA-256s; the
session fixture verifies them before any test runs. No network, no database; outside this
directory only the live ``configs/eval/v2/statement_lines.yaml`` is read.

Split check (spec Notes): no pinned figure is a key of any v2 split (TSM has no Debt key;
TSLA's FY2023-FY2025 capex are no key; its dev capex key is FY2022, not pinned).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FSDS_DIRS = sorted(p for p in (FIXTURES / "fsds").iterdir() if p.is_dir())
REPO = Path(__file__).resolve().parents[3]
LIVE_RULES = REPO / "configs" / "eval" / "v2" / "statement_lines.yaml"

TSM = "0001193125-25-083423"    # TSM 20-F FY2024 (2024-12-31), quarter 2025q2
TSLA = "0001628280-26-003952"   # TSLA 10-K FY2025 (2025-12-31), quarter 2026q1


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_ENV: list[SimpleNamespace] = []


def env() -> SimpleNamespace:
    """The loaded fixture tables and live rules; called inside a test body."""
    if not _ENV:
        from src.eval.keys.statement_witness import load_statement_rules
        from src.eval.sec_reference.fsds import load_tables

        _ENV.append(SimpleNamespace(tables=load_tables(FSDS_DIRS),
                                    rules=load_statement_rules(LIVE_RULES)))
    return _ENV[0]


def tables_copy() -> dict:
    """A deep copy of the fixture tables, for metamorphic edits."""
    return {k: [dict(r) for r in v] for k, v in env().tables.items()}


def set_pre(tables: dict, adsh: str, line: int, **fields: str) -> None:
    """Edits fields of one ``pre`` row of ``adsh`` (exactly one row must match)."""
    rows = [r for r in tables["pre"] if r["adsh"] == adsh and r["line"] == str(line)]
    assert len(rows) == 1, (adsh, line)
    rows[0].update(fields)


def write_rules(tmp: Path, body: str) -> Path:
    """Writes a rules file (``version: 1``, then ``families:`` with ``body``)."""
    p = tmp / "rules.yaml"
    p.write_text("version: 1\nfamilies:\n" + body, encoding="utf-8")
    return p


@pytest.fixture(scope="session")
def kit():
    """Fixture paths and helpers; verifies every fixture file's SHA-256 first."""
    prov = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    for rel, digest in prov["files"].items():
        assert _sha(FIXTURES / rel) == digest, f"fixture {rel} differs from its provenance"
    assert sorted(prov["files"]) == sorted(
        p.relative_to(FIXTURES).as_posix() for p in (FIXTURES / "fsds").rglob("*.tsv"))
    return SimpleNamespace(env=env, tables_copy=tables_copy, set_pre=set_pre,
                           write_rules=write_rules, live_rules=LIVE_RULES, provenance=prov,
                           TSM=TSM, TSLA=TSLA, sha=_sha)
