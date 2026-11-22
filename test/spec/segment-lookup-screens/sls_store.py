"""Synthetic fact records in the fixture shape, for the property tests of segment-lookup-screens.

A record carries the columns of ``mv_financial_facts`` (first non-default dimension as
``segment_axis`` / ``segment_member`` / ``segment_label``), ``dimension_labels`` /
``dimensions`` for a cell, and the ``mv_geographic_facts`` columns for a geographic row.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

_PREFIXES = (("http://fasb.org/us-gaap/", "us-gaap"), ("http://fasb.org/srt/", "srt"),
             ("http://xbrl.sec.gov/country/", "country"))


def prefixed(qname: str) -> str:
    """The view's prefixed form of a URI-qualified QName (standard namespaces only)."""
    ns, _, local = qname.rpartition(":")
    for uri, prefix in _PREFIXES:
        if ns.startswith(uri):
            return f"{prefix}:{local}"
    return qname


def synthetic_record(*, fact_id: int, ticker: str, cik: str, fiscal_year: int, value: float,
                     dims: Sequence[tuple[str, str, str]], unit: str = "USD",
                     concept: str = "http://fasb.org/us-gaap/2024:Revenues",
                     geographic: bool = False, geo_code: str | None = None,
                     geo_name: str | None = None) -> dict:
    """One record. `dims` is ordered ``(axis_uri, member_qname, label)``; the first is the
    dimension the flat view lists."""
    first = dims[0] if dims else None
    rec: dict[str, Any] = {
        "fact_id": fact_id, "cik": cik, "ticker": ticker,
        "accession_no": f"{cik}-{fiscal_year % 100:02d}-000001",
        "fiscal_year": fiscal_year, "fiscal_period": "FY", "concept": concept,
        "concept_family": "Revenue", "concept_pref_rank": 1, "value": value, "unit": unit,
        "period_type": "duration", "period_start": f"{fiscal_year}-01-01",
        "period_end": f"{fiscal_year + 1}-01-01", "duration_kind": "annual",
        "is_current_period": True,
        "segment_axis": prefixed(first[0]) if first else None,
        "segment_member": prefixed(first[1]) if first else None,
        "segment_label": first[2] if first else None,
        "has_multiple_dimensions": len(dims) > 1, "section_path": None,
        "is_geographic_row": geographic,
    }
    if len(dims) > 1:
        rec["dimension_labels"] = {a: lbl for a, _, lbl in dims}
        rec["dimensions"] = {a: m for a, m, _ in dims}
    if geographic:
        rec.update({"geo_member": prefixed(first[1]) if first else None, "geo_code": geo_code,
                    "geo_name": geo_name, "geo_type": None})
    return rec


def catalog_of(records: Sequence[dict], dims_of: Sequence[Sequence[tuple[str, str, str]]]) -> dict:
    """The ``dimension_catalog`` rows (per cik) for records built from `dims_of`."""
    out: dict[str, list[dict]] = {}
    for rec, dims in zip(records, dims_of, strict=True):
        for axis, _, label in dims:
            out.setdefault(rec["cik"], []).append({"axis_qname": axis, "member_label": label})
    return out
