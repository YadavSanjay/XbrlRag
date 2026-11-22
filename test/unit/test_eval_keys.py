"""Unit tests for src/eval/keys/core.py branches the sec-keys spec tests do not reach.

Payloads are small synthetic companyfacts documents built inline; expected values are read
off them by hand.
"""

from __future__ import annotations

import pytest

from src.eval.keys.core import (
    KeyAmbiguousError,
    KeyNotFoundError,
    as_reported_key,
    calendar_key_from_source,
    cross_check,
    fiscal_year_key,
)


def _doc(entries: list[dict]) -> dict:
    return {"cik": 42, "entityName": "Synthetic",
            "facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}


def _entry(start, end, val, accn, filed, fy=2024, fp="FY", form="10-K"):
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": fy, "fp": fp,
            "form": form, "filed": filed}


def test_as_reported_same_day_equal_values_pick_smallest_accession():
    doc = _doc([_entry("2024-01-01", "2024-12-31", 10, "A-2", "2025-02-01"),
                _entry("2024-01-01", "2024-12-31", 10, "A-1", "2025-02-01"),
                _entry("2024-01-01", "2024-12-31", 11, "A-3", "2026-02-01")])
    key = as_reported_key(doc, "us-gaap:Revenues", "USD", "2024-01-01", "2024-12-31")
    assert key["accession"] == "A-1"
    assert key["value"] == 10
    assert key["cik"] == "0000000042"


def test_as_reported_accession_with_two_values_is_ambiguous():
    doc = _doc([_entry("2024-01-01", "2024-12-31", 10, "A-1", "2025-02-01"),
                _entry("2024-01-01", "2024-12-31", 12, "A-1", "2025-02-01")])
    with pytest.raises(KeyAmbiguousError):
        as_reported_key(doc, "us-gaap:Revenues", "USD", "2024-01-01", "2024-12-31",
                        accession="A-1")


def test_fiscal_year_two_starts_for_the_year_end_is_ambiguous():
    # 2024-01-01..2024-12-31 (365 days) and 2023-12-30..2024-12-31 (367 days): both annual.
    doc = _doc([_entry("2024-01-01", "2024-12-31", 10, "A-1", "2025-02-01"),
                _entry("2023-12-30", "2024-12-31", 10, "A-1", "2025-02-01")])
    with pytest.raises(KeyAmbiguousError):
        fiscal_year_key(doc, "us-gaap:Revenues", "USD", 2024)


def test_fiscal_year_ignores_non_annual_forms():
    doc = _doc([_entry("2024-01-01", "2024-12-31", 10, "A-1", "2025-02-01", form="8-K")])
    with pytest.raises(KeyNotFoundError):
        fiscal_year_key(doc, "us-gaap:Revenues", "USD", 2024)


def test_cross_check_does_not_duplicate_verified_by():
    base = {"value": 5.0, "unit": "USD", "decimals": -3, "concept": "us-gaap:Revenues",
            "cik": "0000000042", "period_start": None, "period_end": "2024-12-31",
            "fiscal_year": None, "calendar_year": 2024, "accession": "A-1", "filed": None,
            "form": None, "key_source": "sec_frames", "selection": "sec_frame",
            "policies": ["S1", "S2", "S6"], "status": "verified",
            "verified_by": ["fact_store"]}
    other = dict(base, key_source="fact_store", decimals=-6, verified_by=[])
    res = cross_check(base, other)
    assert res["key"]["verified_by"] == ["fact_store"]
    assert res["key"]["decimals"] == -3  # known decimals are kept
    assert base["verified_by"] == ["fact_store"]


def test_calendar_key_from_source_rejects_malformed_concept_before_fetching():
    class _NoCalls:
        def frames(self, *args):
            raise AssertionError("must not fetch")

    with pytest.raises(ValueError):
        calendar_key_from_source(_NoCalls(), "42", "Revenues", "USD", 2024)  # type: ignore[arg-type]  # duck-typed fake
