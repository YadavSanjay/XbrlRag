"""Unit tests for ``KeyWitness.annual_accession``'s cache (src/eval/suites/v2_witness.py).

The build script shares one witness between the main and the compound build, and each build
fetches its own companyfacts documents. A cache keyed by the document's ``id()`` can then
hand a later document the accession of an earlier, freed one that happened to live at the
same address. The cache is keyed by the filer's CIK instead. The documents are built here,
so every expected accession is read off them.
"""

from __future__ import annotations

from src.eval.keys.core import annual_filing_labels
from src.eval.suites.v2_witness import KeyWitness

_EMPTY_TABLES: dict = {"sub": [], "num": [], "pre": [], "tag": []}


def _adsh(cik: str) -> str:
    return f"{cik}-25-000001"


def _doc(cik: str) -> dict:
    """One filer's companyfacts document: a single FY2024 10-K revenue fact.

    The outer dict is created first, so after a freed document CPython's dict free list
    usually hands its address to this one (the hazard under test).
    """
    doc: dict = {}
    doc["cik"] = int(cik)
    doc["entityName"] = f"Filer {cik}"
    doc["facts"] = {"us-gaap": {"Revenues": {"units": {"USD": [{
        "start": "2024-01-01", "end": "2024-12-31", "val": 100.0, "accn": _adsh(cik),
        "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-01"}]}}}}
    return doc


def _witness() -> KeyWitness:
    return KeyWitness(_EMPTY_TABLES, {}, {}, [])


def test_each_filer_gets_its_own_annual_report():
    # oracle: each document's only 10-K is the accession written into it by _doc
    witness = _witness()
    for cik in ("0000900201", "0000900202"):
        doc = _doc(cik)
        assert witness.annual_accession(doc, annual_filing_labels(doc), 2024) == _adsh(cik)


def test_a_reused_document_address_never_returns_another_filers_report():
    # oracle: the second filer's own 10-K accession; the first filer's document is freed
    # before the second is built, so the second usually takes the first one's address
    witness = _witness()
    reused = False
    for attempt in range(50):
        first_cik, second_cik = f"00009{attempt:03d}01", f"00009{attempt:03d}02"
        first = _doc(first_cik)
        assert witness.annual_accession(first, annual_filing_labels(first), 2024) == (
            _adsh(first_cik))
        first_id = id(first)
        del first
        second = _doc(second_cik)
        reused = reused or id(second) == first_id
        assert witness.annual_accession(second, annual_filing_labels(second), 2024) == (
            _adsh(second_cik))
    # The hazard was actually exercised at least once (CPython reuses freed dict slots).
    assert reused
