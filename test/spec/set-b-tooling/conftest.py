"""Fakes and builders for the set-b-tooling spec tests.

Spec: docs/specs/set-b-tooling.md. The only fake sits at the I/O seam the spec names:
``FakeStore``, a ``KeyFactStore`` returning synthetic candidate dicts per (ticker, fiscal
year) and recording every call. The builders make candidate dicts and authoring cases by
hand. Everything is exposed through one session-scoped ``kit`` fixture so hypothesis tests
can use it without the function-scoped-fixture health check. All data is synthetic, with
values chosen by hand; no network, no data directory, no current date.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.set_b.base import KeyFactStore

US_GAAP = "http://fasb.org/us-gaap/2024"
IFRS = "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"

# The 12 suite filers (spec decisions); they are just strings here.
FILERS = ["AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "META", "MSFT", "MU", "NVDA", "ORCL",
          "TSLA", "TSM"]
KINDS = ["lookup", "dimensional", "compute", "breakdown"]

# Synthetic fiscal-year duration periods, (start, end).
PERIODS = {
    2024: ("2023-07-01", "2024-06-30"),
    2025: ("2024-07-01", "2025-06-30"),
    2026: ("2025-07-01", "2026-06-30"),
}


def cand(
    fact_id: int,
    local: str,
    value: float,
    *,
    unit: str = "USD",
    member: str | None = None,
    family: str | None = None,
    fy: int = 2025,
    instant: bool = False,
    ns: str = US_GAAP,
) -> dict[str, Any]:
    """One candidate fact; it is dimensional exactly when it has a member label."""
    start, end = PERIODS[fy]
    return {
        "fact_id": fact_id,
        "concept": f"{ns}:{local}",
        "concept_family": family,
        "unit": unit,
        "value": value,
        "period_start": None if instant else start,
        "period_end": end,
        "dimensional": member is not None,
        "member_label": member,
    }


def lookup(cid: str = "L1", *, ticker: str = "MSFT", fy: int = 2025, expected: Any = "1,234",
           scale: str | None = "millions", unit: str = "USD", **extra: Any) -> dict[str, Any]:
    """A minimal valid lookup case."""
    case = {"id": cid, "question": f"Synthetic lookup question {cid}?", "kind": "lookup",
            "ticker": ticker, "fy": fy, "expected": expected, "unit": unit}
    if scale is not None:
        case["scale"] = scale
    case.update(extra)
    return case


def dimensional(cid: str = "D1", *, ticker: str = "MSFT", fy: int = 2025, expected: Any = "567",
                scale: str | None = "millions", unit: str = "USD", member: str = "Cloud",
                **extra: Any) -> dict[str, Any]:
    """A minimal valid dimensional case."""
    extra.setdefault("question", f"Synthetic dimensional question {cid}?")
    case = lookup(cid, ticker=ticker, fy=fy, expected=expected, scale=scale, unit=unit,
                  member=member, **extra)
    case["kind"] = "dimensional"
    return case


def inp(name: str, expected: Any, *, scale: str | None = "millions", unit: str = "USD",
        **extra: Any) -> dict[str, Any]:
    """One compute input."""
    out = {"name": name, "expected": expected, "unit": unit}
    if scale is not None:
        out["scale"] = scale
    out.update(extra)
    return out


def compute(cid: str = "C1", *, ticker: str = "MSFT", fy: int = 2025, formula: str = "yoy",
            expected: Any = "12.5%", scale: str | None = None,
            inputs: list[dict] | None = None, **extra: Any) -> dict[str, Any]:
    """A minimal valid compute case (a yoy by default)."""
    if inputs is None:
        inputs = [inp("current", "1,125"), inp("prior", "1,000", fy=fy - 1)]
    case = {"id": cid, "question": f"Synthetic compute question {cid}?", "kind": "compute",
            "ticker": ticker, "fy": fy, "formula": formula, "expected": expected,
            "inputs": inputs}
    if scale is not None:
        case["scale"] = scale
    case.update(extra)
    return case


def breakdown(cid: str = "B1", *, ticker: str = "MSFT", fy: int = 2025, unit: str = "USD",
              rows: list[dict] | None = None, **extra: Any) -> dict[str, Any]:
    """A minimal valid breakdown case."""
    if rows is None:
        rows = [{"member": "Alpha", "expected": "10", "scale": "millions"},
                {"member": "Beta", "expected": "20", "scale": "millions"}]
    case = {"id": cid, "question": f"Synthetic breakdown question {cid}?", "kind": "breakdown",
            "ticker": ticker, "fy": fy, "unit": unit, "rows": rows}
    case.update(extra)
    return case


class FakeStore(KeyFactStore):
    """Returns a deep copy of the configured candidates per (ticker, fy); records every call."""

    def __init__(self, facts: dict[tuple[str, int], list[dict]] | None = None) -> None:
        self.facts = {k: list(v) for k, v in (facts or {}).items()}
        self.calls: list[tuple[str, int]] = []

    def facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        self.calls.append((ticker, int(fiscal_year)))
        return copy.deepcopy(self.facts.get((ticker, int(fiscal_year)), []))


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The builders and the fake, as one namespace."""
    return SimpleNamespace(
        US_GAAP=US_GAAP,
        IFRS=IFRS,
        FILERS=FILERS,
        KINDS=KINDS,
        PERIODS=PERIODS,
        cand=cand,
        lookup=lookup,
        dimensional=dimensional,
        inp=inp,
        compute=compute,
        breakdown=breakdown,
        FakeStore=FakeStore,
    )
