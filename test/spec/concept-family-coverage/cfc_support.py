"""Shared tables and helpers for the concept-family-coverage spec tests.

The cases in ``filings.json`` are real fact-store rows (provenance in that file and in
docs/specs/concept-family-coverage.md, Notes). ``store_rows`` turns a case into the rows the
lookup's SQL returns after the change: members of the family only, with the rank, role and
substitution that ``family_member`` gives for the filer. ``lookup`` runs the real
``XbrlFactstoreLookup._build_result`` over them; nothing in ``src`` is faked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.retriever.xbrl.family_resolution import family_member
from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup

_DOC = json.loads((Path(__file__).parent / "filings.json").read_text(encoding="utf-8"))
CASES: dict[str, dict[str, Any]] = _DOC["cases"]

# SEC EDGAR CIKs (10-digit) of the filers in the decision table.
CIK = {
    "ABT": "0000001800", "AMD": "0000002488", "AAPL": "0000320193", "AMZN": "0001018724",
    "AVGO": "0001730168", "CEG": "0001868275", "COST": "0000909832", "CRM": "0001108524",
    "CSCO": "0000858877", "GOOGL": "0001652044", "HD": "0000354950", "INTC": "0000050863",
    "KO": "0000021344", "LLY": "0000059478", "LMT": "0000936468", "MCD": "0000063908",
    "META": "0001326801", "MRK": "0000310158", "MSFT": "0000789019", "MU": "0000723125",
    "NFLX": "0001065280", "NVDA": "0001045810", "ORCL": "0001341439", "PG": "0000080424",
    "QCOM": "0000804328", "TSLA": "0001318605", "TSM": "0001046179", "TXN": "0000097476",
    "UNP": "0000100885", "UPS": "0001090727", "VST": "0001692819", "WMT": "0000104169",
}
NO_ENTRIES_CIK = "0000000001"  # a CIK with no cik-scoped or reviewed entry

G24 = "http://fasb.org/us-gaap/2024:"
G25 = "http://fasb.org/us-gaap/2025:"
IFRS25 = "https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full:"


def row_of(case_id: str, fact_id: int) -> dict[str, Any]:
    """The fixture row with this fact id."""
    return next(r for r in CASES[case_id]["rows"] if r["fact_id"] == fact_id)


def store_row(case: dict[str, Any], row: dict[str, Any], family: str) -> dict[str, Any] | None:
    """One fixture row as the lookup SQL returns it after the change, or None (no member)."""
    m = family_member(family, case["cik"], row["concept"])
    if m is None:
        return None
    sub = m["substitution"] or {}
    return {
        "fact_id": row["fact_id"], "value": row["value"], "unit": row["unit"],
        "period_type": case["period_type"], "period_start": row["period_start"],
        "period_end": row["period_end"], "concept": row["concept"],
        "concept_family": family, "concept_pref_rank": m["rank"],
        "concept_role": m["role"], "substitution_code": sub.get("code"),
        "substitution_note": sub.get("note"), "presented": row["presented"],
        "decimals": row["decimals"], "segment_axis": None, "segment_member": None,
        "segment_label": None, "html_anchor": None, "page_num": None,
        "section_path": None, "accession_no": case["accession_no"],
        "linked_chunk_ids": [],
    }


def store_rows(case_id: str, family: str) -> list[dict[str, Any]]:
    """The case's rows that are members of ``family`` for the filer."""
    case = CASES[case_id]
    out = [store_row(case, r, family) for r in case["rows"]]
    return [r for r in out if r is not None]


def lookup(case_id: str, family: str, rows: list[dict[str, Any]] | None = None):
    """``_build_result`` for an undimensioned FY lookup of ``family`` on the case's filing."""
    case = CASES[case_id]
    rows = store_rows(case_id, family) if rows is None else rows
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions("NONE", notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "cik": case["cik"], "fy": case["fiscal_year"],
            "concept_family": family, "dimensions": "NONE", "section_hint": None}
    return lk._build_result("s1", step, case["cik"], case["fiscal_year"], family,
                            case["period_type"], specs, rows, notes)


def substitution_of(result: Any) -> Any:
    """The result's substitution, or the marker "missing" when the field does not exist."""
    return getattr(result, "substitution", "missing")
