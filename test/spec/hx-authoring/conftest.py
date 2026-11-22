"""Fakes and builders for the hx-authoring spec tests.

Spec: docs/specs/hx-authoring.md. The only fake sits at the I/O seam the spec names:
``FakeStore``, an ``HxKeyFactStore`` returning synthetic candidate dicts per (ticker,
fiscal year) and per (ticker, accession, fiscal year), recording every call. The builders
make one minimal valid authoring case per H-X kind and sub-type. Every question, ticker
and amount is made up; tickers are fictional (``DEVA``..., held-out ``HOLDA``...). All is
exposed through one session-scoped ``kit`` fixture so hypothesis tests can use it.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.set_b.hx_base import HxKeyFactStore

US_GAAP = "http://fasb.org/us-gaap/2024"

DEV = frozenset({"DEVA", "DEVB", "DEVC", "DEVD", "DEVE", "DEVF"})
HELD = frozenset({"HOLDA", "HOLDB"})
KINDS = ["cross_filer", "cross_period", "compound", "honesty", "text"]

ACC_ORIG = "0000000001-24-000001"
ACC_LATEST = "0000000001-25-000002"
ACC_TEXT = "0000000002-25-000003"

M = "millions"

# Synthetic fiscal-year duration periods, (start, end).
PERIODS = {fy: (f"{fy - 1}-07-01", f"{fy}-06-30") for fy in (2022, 2023, 2024, 2025, 2026)}


def cand(fact_id: int, local: str, value: float, *, unit: str = "USD",
         member: str | None = None, family: str | None = None,
         fy: int = 2025) -> dict[str, Any]:
    """One candidate fact (set B's keys); dimensional exactly when it has a member."""
    start, end = PERIODS[fy]
    return {"fact_id": fact_id, "concept": f"{US_GAAP}:{local}", "concept_family": family,
            "unit": unit, "value": value, "period_start": start, "period_end": end,
            "dimensional": member is not None, "member_label": member}


def row(ticker: str, expected: Any, scale: str | None = M, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ticker": ticker, "expected": expected}
    if scale is not None:
        out["scale"] = scale
    out.update(extra)
    return out


def yrow(fy: int, expected: Any, scale: str | None = M, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"fy": fy, "expected": expected}
    if scale is not None:
        out["scale"] = scale
    out.update(extra)
    return out


def _cf_fields(subtype: str) -> dict[str, Any]:
    """The cross_filer fields of a valid case of ``subtype`` (no id, no question).

    Universe DEVA..DEVD, DEVD excluded (not tagged); ranked DEVA 300, DEVB 200, DEVC 100
    (millions, FY2025, USD).
    """
    out: dict[str, Any] = {
        "subtype": subtype, "fy": 2025, "unit": "USD",
        "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
        "exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
    }
    if subtype == "screen_top":
        out["rows"] = [row("DEVA", "300"), row("DEVB", "200")]
    elif subtype == "screen_bottom":
        out["rows"] = [row("DEVC", "100"), row("DEVB", "200")]
    elif subtype == "count":
        out["rows"] = [row("DEVA", "300"), row("DEVB", "200")]
        out["expected"] = 2
    elif subtype == "sum":
        out["rows"] = [row("DEVA", "300"), row("DEVB", "200")]
        out["expected"] = "500"
        out["scale"] = M
    elif subtype == "median":
        out["rows"] = [row("DEVA", "300"), row("DEVB", "200"), row("DEVC", "100")]
        out["expected"] = "200"
        out["scale"] = M
    elif subtype == "peer_relative":
        out["ticker"] = "DEVB"
        out["rows"] = [row("DEVA", "300"), row("DEVB", "200"), row("DEVC", "100")]
        out["expected"] = 2
    else:
        raise AssertionError(subtype)
    return out


def _cp_fields(subtype: str) -> dict[str, Any]:
    """The cross_period fields of a valid case (DEVE: 100, 110, 121 millions FY2023-25)."""
    out: dict[str, Any] = {"subtype": subtype, "ticker": "DEVE", "unit": "USD"}
    if subtype == "trend":
        out["rows"] = [yrow(2023, "100"), yrow(2024, "110"), yrow(2025, "121")]
    elif subtype == "cagr":
        out["rows"] = [yrow(2023, "100"), yrow(2025, "121")]
        out["expected"] = "10%"
    elif subtype == "point_in_time":
        out["fy"] = 2023
        out["as_originally_reported"] = {"expected": "100", "scale": M, "accession": ACC_ORIG}
        out["latest"] = {"expected": "104", "scale": M, "accession": ACC_LATEST}
    else:
        raise AssertionError(subtype)
    return out


def _text_fields() -> dict[str, Any]:
    return {"ticker": "DEVA", "fy": 2025,
            "reference_answer": "Synthetic reference answer about a made-up filer.",
            "key_points": ["first synthetic point", "second synthetic point"],
            "passages": [{"accession": ACC_TEXT, "quote": "a synthetic quoted span"}]}


def _head(cid: str, kind: str) -> dict[str, Any]:
    return {"id": cid, "question": f"Synthetic {kind} question {cid}?", "kind": kind}


def cross_filer(cid: str, subtype: str = "screen_top", **extra: Any) -> dict[str, Any]:
    case = _head(cid, "cross_filer")
    case.update(_cf_fields(subtype))
    case.update(extra)
    return case


def cross_period(cid: str, subtype: str = "trend", **extra: Any) -> dict[str, Any]:
    case = _head(cid, "cross_period")
    case.update(_cp_fields(subtype))
    case.update(extra)
    return case


def compound(cid: str, dependent: dict[str, Any] | None = None,
             **extra: Any) -> dict[str, Any]:
    """Ranking: screen_top DEVA 300, DEVB 200. Dependent default: DEVA trend 250, 300."""
    case = _head(cid, "compound")
    case["ranking"] = _cf_fields("screen_top")
    if dependent is None:
        dependent = {"kind": "cross_period", "subtype": "trend", "ticker": "DEVA",
                     "unit": "USD", "rows": [yrow(2024, "250"), yrow(2025, "300")]}
    case["dependent"] = dependent
    case.update(extra)
    return case


def honesty(cid: str, subtype: str = "ambiguous", **extra: Any) -> dict[str, Any]:
    case = _head(cid, "honesty")
    case["subtype"] = subtype
    if subtype == "ambiguous":
        case["tickers"] = ["DEVA"]
        case["assumptions"] = ["the synthetic fiscal year, not the calendar year"]
    elif subtype == "false_premise":
        case["tickers"] = ["DEVB"]
        case["fy"] = 2024
        case["correction"] = "The synthetic metric rose, it did not fall."
    elif subtype == "decline":
        case["tickers"] = ["DEVC"]
        case["reason"] = "not_tagged"
    else:
        raise AssertionError(subtype)
    case.update(extra)
    return case


def text(cid: str, **extra: Any) -> dict[str, Any]:
    case = _head(cid, "text")
    case.update(_text_fields())
    case.update(extra)
    return case


# Every (kind, subtype) and a builder for one valid case of it.
SHAPES: list[tuple[str, str | None]] = [
    ("cross_filer", "screen_top"), ("cross_filer", "screen_bottom"),
    ("cross_filer", "count"), ("cross_filer", "sum"), ("cross_filer", "median"),
    ("cross_filer", "peer_relative"),
    ("cross_period", "trend"), ("cross_period", "cagr"), ("cross_period", "point_in_time"),
    ("compound", None),
    ("honesty", "ambiguous"), ("honesty", "false_premise"), ("honesty", "decline"),
    ("text", None),
]


def make(kind: str, subtype: str | None, cid: str) -> dict[str, Any]:
    """One valid case of ``(kind, subtype)``."""
    if kind == "cross_filer":
        return cross_filer(cid, subtype or "screen_top")
    if kind == "cross_period":
        return cross_period(cid, subtype or "trend")
    if kind == "compound":
        return compound(cid)
    if kind == "honesty":
        return honesty(cid, subtype or "ambiguous")
    return text(cid)


class FakeStore(HxKeyFactStore):
    """Deep copies of the configured candidates; records every call."""

    def __init__(self, facts: dict[tuple[str, int], list[dict]] | None = None,
                 filings: dict[tuple[str, str, int], list[dict]] | None = None) -> None:
        self.facts = {k: list(v) for k, v in (facts or {}).items()}
        self.filings = {k: list(v) for k, v in (filings or {}).items()}
        self.calls: list[tuple] = []

    def facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        self.calls.append(("facts_for", ticker, int(fiscal_year)))
        return copy.deepcopy(self.facts.get((ticker, int(fiscal_year)), []))

    def facts_in_filing(self, ticker: str, accession: str, fiscal_year: int) -> list[dict]:
        self.calls.append(("facts_in_filing", ticker, accession, int(fiscal_year)))
        return copy.deepcopy(self.filings.get((ticker, accession, int(fiscal_year)), []))


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The builders and the fake, as one namespace."""
    return SimpleNamespace(
        US_GAAP=US_GAAP, DEV=DEV, HELD=HELD, KINDS=KINDS, SHAPES=SHAPES,
        ACC_ORIG=ACC_ORIG, ACC_LATEST=ACC_LATEST, ACC_TEXT=ACC_TEXT, PERIODS=PERIODS,
        cand=cand, row=row, yrow=yrow, cross_filer=cross_filer, cross_period=cross_period,
        compound=compound, honesty=honesty, text=text, make=make, FakeStore=FakeStore,
    )
