"""Fixtures for extension-classification (docs/specs/extension-classification.md).

Real excerpts, read-only, built 2026-11-07:

- `fixtures/fsds/<quarter>/{sub,pre,num,tag}.tsv`: SEC Financial Statement Data Sets rows,
  byte-for-byte from the quarter zips (`provenance.json` has each zip's URL and SHA-256);
- `fixtures/calc/<accession>.json`: the filing's own calculation arcs (its `*_cal.xml`, or
  the linkbase embedded in its `.xsd`), with the file's URL and SHA-256;
- `fixtures/members.json`: the committed mapping's standard rows for these concepts;
- `fixtures/listed.json`: the hand-listed `ext:` entries of these filers.

The loader turns them into the classifier's input as R2 defines it: an extension line's
concept is `<the schema's targetNamespace>:<tag>`; a standard line's is the FSDS version's
prefix form (`us-gaap/2024` -> `us-gaap:`, `ifrs/2024` -> `ifrs-full:`), while arcs keep the
dated namespaces of the store, so both concept forms are exercised. Nothing is faked: the
classifier is pure.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.eval.sec_reference.fsds import load_tables

FIXTURES = Path(__file__).parent / "fixtures"
_PREFIX = {"us-gaap": "us-gaap", "ifrs": "ifrs-full", "srt": "srt", "dei": "dei"}


def _calc_docs() -> dict[str, dict[str, Any]]:
    docs = {}
    for path in sorted((FIXTURES / "calc").glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        docs[doc["accession_no"]] = doc
    return docs


def _concept(tag: str, version: str, adsh: str, ext_ns: str) -> str:
    if version == adsh:
        return f"{ext_ns}:{tag}"
    return f"{_PREFIX[version.split('/', 1)[0]]}:{tag}"


def _build() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tables = load_tables(sorted(p for p in (FIXTURES / "fsds").iterdir() if p.is_dir()))
    docs = _calc_docs()
    filings: list[dict[str, Any]] = []
    for adsh, doc in sorted(docs.items()):
        ns = doc["extension_namespace"]
        lines = [
            {
                "stmt": r["stmt"],
                "report": int(r["report"]),
                "line": int(r["line"]),
                "concept": _concept(r["tag"], r["version"], adsh, ns),
                "plabel": r["plabel"],
            }
            for r in tables["pre"]
            if r["adsh"] == adsh
        ]
        values = [
            {
                "concept": _concept(r["tag"], r["version"], adsh, ns),
                "period": f"{r['ddate']}/{r['qtrs']}",
                "unit": r["uom"],
                "value": Decimal(r["value"]) if r["value"] != "" else None,
            }
            for r in tables["num"]
            if r["adsh"] == adsh
        ]
        arcs = [
            {"link_role": a["link_role"], "parent": a["parent"], "child": a["child"],
             "weight": Decimal(a["weight"])}
            for a in doc["arcs"]
        ]
        filings.append({"accession_no": adsh, "cik": doc["cik"], "lines": lines,
                        "arcs": arcs, "values": values})
    return filings, tables


_FILINGS, _TABLES = _build()
_MEMBERS = json.loads((FIXTURES / "members.json").read_text(encoding="utf-8"))["members"]
_LISTED = json.loads((FIXTURES / "listed.json").read_text(encoding="utf-8"))["listed"]
_DOCS = _calc_docs()


@pytest.fixture
def filings() -> list[dict[str, Any]]:
    """Every fixture filing as classifier input (a fresh copy per test)."""
    return copy.deepcopy(_FILINGS)


@pytest.fixture
def members() -> list[dict[str, Any]]:
    """The committed standard memberships for the fixture concepts."""
    return copy.deepcopy(_MEMBERS)


@pytest.fixture
def listed() -> list[dict[str, Any]]:
    """The hand-listed `ext:` rows of the fixture filers."""
    return copy.deepcopy(_LISTED)


@pytest.fixture
def fsds_tables() -> dict[str, list[dict[str, str]]]:
    """The raw FSDS rows of the fixtures."""
    return copy.deepcopy(_TABLES)


@pytest.fixture
def calc_docs() -> dict[str, dict[str, Any]]:
    """The calc fixture documents by accession."""
    return copy.deepcopy(_DOCS)
