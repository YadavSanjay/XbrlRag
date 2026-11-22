"""Fixtures for segment-geo-views (docs/specs/segment-geo-views.md).

Every file under `fixtures/` is a real excerpt retrieved 2026-11-07 (provenance inside each):

- `<filer>_fy<year>_revenue(s).json`: every fact of one revenue concept in the filing, all
  contexts, from xbrl_db (read-only). Each context's explicit `context_dimension` rows are on
  the fact; its default rows are listed once per distinct set in `default_sets`. `view` is the
  fact's mv_financial_facts row.
- `<filer>_fy<year>_geo_members.json`: the filing's distinct members on a geographic axis.
- `geography_normalization.json`: the whole table as loaded.
- `fsds_excerpt.json`: FSDS sub/num rows copied from the SEC quarter zips (SHA-256 recorded).
- `tsla_fy2023_filing_excerpt.json`: ix facts, contexts and table rows copied from Tesla's
  10-K, which FSDS 2024q1 does not carry.

Nothing is faked: the functions under test are pure.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _fact(doc: dict, row: dict) -> dict:
    explicit = [{"axis": a, "member": m, "is_default": False}
                for a, m, _typed in row["explicit_dimensions"]]
    defaults = [{"axis": a, "member": m, "is_default": True}
                for a, m in doc["default_sets"][row["default_set"]]]
    return {
        "fact_id": row["fact_id"],
        "accession_no": doc["accession_no"],
        "concept": row["concept"],
        "unit": row["unit"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "value": None if row["value"] is None else Decimal(row["value"]),
        "decimals": row["decimals"],
        "dimensions": explicit + defaults,
        "view": row["view"],
    }


class Filing:
    """One fixture filing's facts of one concept."""

    def __init__(self, name: str) -> None:
        self.raw = _load(name)
        self.accession_no = self.raw["accession_no"]
        self.facts = [_fact(self.raw, r) for r in self.raw["facts"]]

    def by_id(self, fact_id: int) -> dict:
        return next(f for f in self.facts if f["fact_id"] == fact_id)

    def current(self) -> list[dict]:
        return [f for f in self.facts if f["view"]["is_current_period"]]


@pytest.fixture(scope="session")
def filing():
    """Load a fixture filing's facts by name."""
    cache: dict[str, Filing] = {}

    def load(name: str) -> Filing:
        if name not in cache:
            cache[name] = Filing(name)
        return cache[name]

    return load


@pytest.fixture(scope="session")
def geo_members():
    """Load a filing's geographic members by fixture name."""
    return lambda name: _load(name)


@pytest.fixture(scope="session")
def normalization() -> list[dict]:
    """The geography_normalization snapshot."""
    return _load("geography_normalization")["rows"]


def _fsds_rows(entry: dict) -> list[dict]:
    header = entry["num_header"].split("\t")
    rows = []
    for line in entry["num_rows"]:
        row = dict(zip(header, line.split("\t"), strict=True))
        row["value"] = Decimal(row["value"])
        rows.append(row)
    return rows


@pytest.fixture(scope="session")
def fsds():
    """FSDS num rows of a filing, keyed by their `segments` field ('' = undimensioned)."""
    doc = _load("fsds_excerpt")

    def lookup(adsh: str) -> dict[str, Decimal]:
        entry = next(e for e in doc["filings"] if e["adsh"] == adsh)
        out: dict[str, Decimal] = {}
        for row in _fsds_rows(entry):
            if row["coreg"] == "":
                out[row["segments"]] = row["value"]
        return out

    return lookup


@pytest.fixture(scope="session")
def tsla_filing() -> dict:
    """Tesla FY2023 revenue-by-geography facts copied from the filing."""
    return _load("tsla_fy2023_filing_excerpt")
