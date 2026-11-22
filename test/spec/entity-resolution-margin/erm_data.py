"""Committed data for entity-resolution-margin (docs/specs/entity-resolution-margin.md).

Two committed files, nothing else (no database, no network):

- ``fixtures/sec_company_tickers_excerpt.json``: rows of SEC's company ticker list
  (https://www.sec.gov/files/company_tickers.json, retrieved 2026-11-07, full-file SHA-256
  recorded in the file). Each row's CIK is the oracle for "which company is this name".
- ``fixtures/corpus_filers.json``: the 33 corpus filers as ``entity_master`` stores them
  (cik, trading_symbol, company_name), read 2026-11-07: the universe the resolver sees at
  run time.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


SEC = _load("sec_company_tickers_excerpt.json")
SEC_ROWS: list[dict] = SEC["rows"]
# SEC ticker -> zero-padded CIK: the oracle for every expected CIK.
SEC_CIK: dict[str, str] = {r["ticker"]: f"{r['cik_str']:010d}" for r in SEC_ROWS}

CORPUS_ROWS: list[dict] = _load("corpus_filers.json")["rows"]
# The run-time universe: (cik, ticker, company_name) per entity_master row.
CORPUS: list[tuple[str, str | None, str]] = [
    (r["cik"], r["trading_symbol"], r["company_name"]) for r in CORPUS_ROWS
]
CORPUS_CIKS: frozenset[str] = frozenset(r["cik"] for r in CORPUS_ROWS)

# Rows of the excerpt by role (see the fixture's "selection").
CORPUS_SEC_ROWS = [r for r in SEC_ROWS if r["why"] == "corpus"]
OTHER_SEC_ROWS = [r for r in SEC_ROWS if r["why"] in ("named", "near")]

# Every excerpt row as a universe row.
SEC_UNIVERSE: list[tuple[str, str | None, str]] = [
    (f"{r['cik_str']:010d}", r["ticker"], r["title"]) for r in SEC_ROWS
]


def cik_of(r: dict) -> str:
    """The zero-padded CIK of an excerpt row."""
    return f"{r['cik_str']:010d}"


def sec_row(ticker: str) -> tuple[str, str | None, str]:
    """The excerpt's row for a ticker, as a universe row."""
    r = next(r for r in SEC_ROWS if r["ticker"] == ticker)
    return (cik_of(r), r["ticker"], r["title"])
