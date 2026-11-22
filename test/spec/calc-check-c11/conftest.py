"""Fixtures for calc-check-c11 (docs/specs/calc-check-c11.md).

Each `fixtures/<case>.json` is a real excerpt from xbrl_db (read-only, 2026-11-07): one
calculation tree's arcs and every fact of its concepts in that filing, all contexts. Each
fact was checked against the filing's rendered page (its `xml_id` and `context` locate it);
`arelle_c11r` records what Arelle 2.41.0 `--calc c11r` reported for the same tree.
`seed_exceptions.csv` holds the 131 strict failures of the investigation
(data/eval/calc_check/report.md).

Nothing is faked: the check is pure.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]
SUM_2003 = "http://www.xbrl.org/2003/arcrole/summation-item"


def _fact(row: dict) -> dict:
    return {
        "fact_id": row["fact_id"],
        "concept": row["concept"],
        "entity": row["entity"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "dimensions": [tuple(pair) for pair in row["dimensions"]],
        "unit": row["unit"],
        "value": None if row["value"] is None else Decimal(row["value"]),
        "decimals": row["decimals"],
    }


def _arc(row: dict, arcrole: str | None = None) -> dict:
    return {
        "arcrole": arcrole or row["arcrole"],
        "link_role": row["link_role"],
        "parent": row["parent"],
        "child": row["child"],
        "weight": Decimal(row["weight"]),
    }


class Case:
    """One fixture filing: its facts, arcs and recorded Arelle outcome."""

    def __init__(self, name: str) -> None:
        self.raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        self.accession_no = self.raw["accession_no"]
        self.facts = [_fact(r) for r in self.raw["facts"]]
        self.arcs = [_arc(r) for r in self.raw["arcs"]]
        # The MV's fanned-out copy of the same arcs (only NVIDIA's fixture has it).
        self.mv_arcs = [_arc(r, SUM_2003) for r in self.raw.get("mv_calculation_network_rows", [])]
        self.arelle = self.raw["arelle_c11r"]["inconsistent_in_this_tree"]

    def by_id(self, fact_id: int) -> dict:
        return next(f for f in self.facts if f["fact_id"] == fact_id)


@pytest.fixture(scope="session")
def case():
    """Load a fixture filing by name."""
    cache: dict[str, Case] = {}

    def load(name: str) -> Case:
        if name not in cache:
            cache[name] = Case(name)
        return cache[name]

    return load


@pytest.fixture(scope="session")
def seed_path() -> Path:
    return FIXTURES / "seed_exceptions.csv"


@pytest.fixture(scope="session")
def committed_exceptions_path() -> Path:
    return REPO_ROOT / "configs" / "ingest" / "calc_c11_exceptions.csv"
