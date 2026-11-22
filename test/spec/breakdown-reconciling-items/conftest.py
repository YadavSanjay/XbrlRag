"""Fixtures for breakdown-reconciling-items (docs/specs/breakdown-reconciling-items.md).

Each `fixtures/<filer>_fy<year>.json` is a real excerpt read-only from xbrl_db on 2026-11-07:
the total the breakdown op uses, the members it reads per axis, the filing's facts in the
total's period and unit (undimensioned, or single-dimension on ConsolidationItemsAxis) whose
concepts appear in the excerpted arcs, and the filing's own presentation/definition arcs of
the roles that present the axis or the total's concept, plus its calculation arcs under the
total's and the members' concepts. Provenance: accession, EDGAR index URL, SHA-256 of each
filer document, and the FSDS quarter (URL, zip SHA-256, retrieval time) with its `num` rows
for the same filing and period.

`load()` checks every stored value against the filer's own `ix:nonFraction` element (text,
`scale`, `sign`) and the total against FSDS, so a test's expected numbers rest on the filing,
not on our store. The DB is the only I/O boundary; nothing in `src` is faked.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def ix_value(ix: dict) -> Decimal:
    """The value an `ix:nonFraction` element states: its text times 10^scale, signed."""
    fmt = ix.get("format") or ""
    text = ix["text"].replace(",", "").replace("$", "").strip()
    value = Decimal(0) if "zero" in fmt or text in ("-", "—", "–") else Decimal(text)
    value *= Decimal(10) ** int(ix.get("scale") or 0)
    return -value if ix.get("sign") == "-" else value


def _check(entry: dict, where: str) -> None:
    ix = entry.get("ixbrl")
    assert ix is not None, f"{where}: fact {entry['fact_id']} has no ix:nonFraction element"
    assert Decimal(entry["value"]) == ix_value(ix), (
        f"{where}: fact {entry['fact_id']} differs from its iXBRL")


def load(name: str) -> dict:
    """One fixture, every value verified against the filer's iXBRL (and FSDS for the total)."""
    case = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    _check(case["total"], name)
    fsds = {(r["tag"], r["segments"]): Decimal(r["value"]) for r in case["fsds"]["rows"]}
    assert fsds[(case["total"]["concept"], "")] == Decimal(case["total"]["value"]), name
    for axis, members in case["members"].items():
        for m in members:
            _check(m, f"{name}/{axis}")
    for f in case["facts"]:
        if f["ixbrl"] is not None and "fixed-zero" not in (f["ixbrl"].get("format") or ""):
            _check(f, f"{name}/facts")
    return case


def fsds_value(case: dict, tag: str, segments: str = "") -> float:
    """The FSDS `num` value for one tag and segment string of the fixture's filing."""
    for r in case["fsds"]["rows"]:
        if r["tag"] == tag and r["segments"] == segments:
            return float(r["value"])
    raise KeyError((tag, segments))


def total_value(case: dict) -> float:
    return float(case["total"]["value"])


def members(case: dict, axis: str, parents: dict[str, list[str]] | None = None) -> list[dict]:
    """The axis members as `choose_breakdown` takes them; `parents` read by hand from the arcs."""
    parents = parents or {}
    return [
        {"label": m["label"], "value": float(m["value"]), "member": m["member"],
         "parents": list(parents.get(m["member"], [])), "fact_id": m["fact_id"], "unit": m["unit"],
         "concept": m["concept"]}
        for m in case["members"][axis]
    ]


def lines_by_fact(
    case: dict, fact_ids: list[int], weights: dict[int, float] | None = None
) -> list[dict]:
    """Candidate lines built by hand from the fixture's facts (in the order given)."""
    weights = weights or {}
    by_id = {f["fact_id"]: f for f in case["facts"]}
    return [
        {"label": by_id[i]["label"], "value": weights.get(i, 1.0) * float(by_id[i]["value"]),
         "fact_id": i, "unit": by_id[i]["unit"], "concept": by_id[i]["concept"]}
        for i in fact_ids
    ]


@pytest.fixture(scope="session")
def fx() -> SimpleNamespace:
    """The loaders above, cached per fixture name."""
    cache: dict[str, dict] = {}

    def cached(name: str) -> dict:
        if name not in cache:
            cache[name] = load(name)
        return cache[name]

    return SimpleNamespace(load=cached, members=members, lines_by_fact=lines_by_fact,
                           total=total_value, fsds=fsds_value)
