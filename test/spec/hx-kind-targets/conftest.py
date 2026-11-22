"""Builders for the hx-kind-targets spec tests.

Spec: docs/specs/hx-kind-targets.md. One minimal valid H-X case per kind, in hx-authoring's
authoring shape (docs/specs/hx-authoring.md requirements 2-7). Every question, ticker and
amount is made up; tickers are fictional (``DEVA``..., held-out ``HOLDA``...). Nothing here
touches I/O. Exposed through one session-scoped ``kit`` fixture so hypothesis tests can use it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

DEV = frozenset({"DEVA", "DEVB", "DEVC", "DEVD", "DEVE", "DEVF"})
HELD = frozenset({"HOLDA", "HOLDB"})
KINDS = ["cross_filer", "cross_period", "compound", "honesty", "text"]

# Pre-registration Amendment 1.1 (docs/eval/heldout_v2/preregistration.md), the H-X kind mix.
TARGETS = {"cross_filer": 24, "cross_period": 15, "compound": 18, "honesty": 9, "text": 9}

M = "millions"
ACC_TEXT = "0000000002-25-000003"


def _row(ticker: str, expected: str) -> dict[str, Any]:
    return {"ticker": ticker, "expected": expected, "scale": M}


def _yrow(fy: int, expected: str) -> dict[str, Any]:
    return {"fy": fy, "expected": expected, "scale": M}


def _screen_top() -> dict[str, Any]:
    """Universe DEVA..DEVD, DEVD excluded; ranked DEVA 300, DEVB 200 (millions, FY2025)."""
    return {"subtype": "screen_top", "fy": 2025, "unit": "USD",
            "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
            "exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
            "rows": [_row("DEVA", "300"), _row("DEVB", "200")]}


def _head(cid: str, kind: str) -> dict[str, Any]:
    return {"id": cid, "question": f"Synthetic {kind} question {cid}?", "kind": kind}


def make(kind: str, cid: str) -> dict[str, Any]:
    """One valid case of ``kind``."""
    case = _head(cid, kind)
    if kind == "cross_filer":
        case.update(_screen_top())
    elif kind == "cross_period":
        case.update({"subtype": "trend", "ticker": "DEVE", "unit": "USD",
                     "rows": [_yrow(2023, "100"), _yrow(2024, "110"), _yrow(2025, "121")]})
    elif kind == "compound":
        case["ranking"] = _screen_top()
        case["dependent"] = {"kind": "cross_period", "subtype": "trend", "ticker": "DEVA",
                             "unit": "USD", "rows": [_yrow(2024, "250"), _yrow(2025, "300")]}
    elif kind == "honesty":
        case.update({"subtype": "ambiguous", "tickers": ["DEVA"],
                     "assumptions": ["the synthetic fiscal year, not the calendar year"]})
    elif kind == "text":
        case.update({"ticker": "DEVA", "fy": 2025,
                     "reference_answer": "Synthetic reference answer about a made-up filer.",
                     "key_points": ["first synthetic point"],
                     "passages": [{"accession": ACC_TEXT, "quote": "a synthetic quoted span"}]})
    else:
        raise AssertionError(kind)
    return case


def cases_for(counts: dict[str, int]) -> list[dict[str, Any]]:
    """``counts[k]`` valid cases of each kind ``k``, ids ``<kind>-<i>``."""
    return [make(k, f"{k}-{i}") for k in KINDS for i in range(counts.get(k, 0))]


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The builders and constants, as one namespace."""
    return SimpleNamespace(DEV=DEV, HELD=HELD, KINDS=KINDS, TARGETS=TARGETS, make=make,
                           cases_for=cases_for)
