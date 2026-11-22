"""Builders for the grading-dont-care-and-ties spec tests
(docs/specs/grading-dont-care-and-ties.md).

Every case is a fictional v2 record in the shape v2-suite-build emits (tickers ``DEVA`` ...
``DEVX``, round amounts, universe "retailers"); every answer is a hand-built SystemAnswer
carrying a citation, so only the rule under test decides the grade.

- ``screen_case``: a ``screen.top`` / ``screen.bottom`` record from its ranked members (already
  in key order), its exclusions and N; ``inputs=True`` writes every ranked member's key into
  ``key["inputs"]`` in ranked order, as the builder does.
- ``peer_case``: a ``peer_relative`` record (subject rank given by hand).
- ``agg_case``: a ``screen.sum`` / ``screen.median`` record with ``expected_value`` given by
  the test and optional key inputs.
- ``text_for``: an answer text that states the universe by name and names the tickers given.
- ``MRK_TEXT``: the stored dev xbrlrag_full refusal to ``int-3df803cba7fe-01c77e81``.

Reached through the session-scoped ``kit`` fixture, so hypothesis tests can use it. Grading is
pure, so nothing is faked.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

M = 1e6

MRK_TEXT = (
    "The requested figure, MRK's general and administrative expenses for the fiscal year "
    "ended December 31, 2024, could not be determined. The structured XBRL facts lookup "
    "returned NOT_FOUND, and no narrative context chunks were retrieved. No value is stated."
)


def _tags(shape: str) -> list[str]:
    return ["split:dev", "intent:int-gdct", "family:formal", "key_source:sec_companyfacts",
            "key_status:verified", f"shape:{shape}"]


def _key(inputs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"definition": None, "disagreements": [], "inputs": inputs,
            "policies": ["S1", "S2", "S3", "S5", "S6"], "same_filing": None,
            "source": "sec_companyfacts", "status": "verified"}


def _input(value: float) -> dict[str, Any]:
    return {"accession": "0000000000-25-000001", "fiscal_year": 2025, "form": "10-K",
            "period_end": "2025-12-31", "unit": "USD", "value": value}


def _disclosures(ranked: list[str], excluded: list[dict[str, Any]]) -> dict[str, Any]:
    return {"universe": "retailers", "members": len(ranked) + len(excluded),
            "ranked": list(ranked), "excluded": [dict(e) for e in excluded],
            "period_ends": {t: "2025-12-31" for t in ranked}, "disclose_period_ends": False}


def _row(ticker: str, value: float) -> dict[str, Any]:
    return {"ticker": ticker, "value": value, "fiscal_year": 2025, "period_end": "2025-12-31"}


def screen_case(members: list[tuple[str, float]], excluded: list[dict[str, Any]] | None = None,
                top_n: int = 3, shape: str = "screen.top", inputs: bool = True,
                input_values: list[float] | None = None) -> EvalCase:
    """A screen record; ``members`` are (ticker, value) in the key's order."""
    ranked = [t for t, _ in members]
    values = input_values if input_values is not None else [v for _, v in members]
    return EvalCase.from_dict({
        "id": "v2-gdct-screen", "suite": "xbrl_v2_dev", "kind": "screen",
        "origin": f"v2:{shape}", "fy": 2025, "unit": "USD", "rel_tol": 1e-4,
        "question": f"Which retailers were the {shape.split('.')[1]} {top_n} by the metric?",
        "tags": _tags(shape), "top_n": top_n,
        "expected_rows": [_row(t, v) for t, v in members[:top_n]],
        "disclosures": _disclosures(ranked, excluded or []),
        "key": _key([_input(v) for v in values] if inputs else []),
    })


def peer_case(members: list[tuple[str, float]], subject: str, rank: int,
              excluded: list[dict[str, Any]] | None = None) -> EvalCase:
    """A peer-relative record; ``members`` are (ticker, value) in the key's order."""
    ranked = [t for t, _ in members]
    return EvalCase.from_dict({
        "id": "v2-gdct-peer", "suite": "xbrl_v2_dev", "kind": "screen",
        "origin": "v2:peer_relative", "fy": 2025, "unit": "USD", "rel_tol": 1e-4,
        "question": f"Where does {subject} rank among the retailers on the metric?",
        "tags": _tags("peer_relative"),
        "expected_rows": [_row(t, v) for t, v in members],
        "disclosures": _disclosures(ranked, excluded or []),
        "subject": {"ticker": subject, "rank": rank, "universe_median": 0.0,
                    "position": "at"},
        "key": _key([_input(v) for _, v in members]),
    })


def agg_case(shape: str, ranked: list[str], expected_value: float,
             excluded: list[dict[str, Any]] | None = None,
             input_values: list[float] | None = None) -> EvalCase:
    """A ``screen.sum`` or ``screen.median`` record."""
    return EvalCase.from_dict({
        "id": f"v2-gdct-{shape}", "suite": "xbrl_v2_dev", "kind": "compute",
        "origin": f"v2:{shape}", "fy": 2025, "unit": "USD", "rel_tol": 1e-4,
        "question": f"What was the {shape.split('.')[1]} of the metric across the retailers?",
        "tags": _tags(shape), "expected_value": expected_value,
        "disclosures": _disclosures(ranked, excluded or []),
        "key": _key([_input(v) for v in input_values or []]),
    })


def drop(ticker: str, value: Any = None, reason: str = "resolved_drop") -> dict[str, Any]:
    """An exclusion entry; ``value`` None leaves the field out."""
    entry: dict[str, Any] = {"ticker": ticker, "reason": reason}
    if value is not None:
        entry["value"] = value
    return entry


def text_for(names: list[str], extra: str = "") -> str:
    """Names the universe ("retailers") and every ticker in ``names``."""
    return f"Among the retailers: {', '.join(names)}. {extra}".strip()


def answer(text: str, values: list[float] | None = None,
           rows: list[tuple[str, float]] | None = None, *, refused: bool = False,
           hedged: bool = False) -> SystemAnswer:
    """A cited answer; ``values`` default to the rows' values."""
    row_dicts = [{"ticker": t, "value": float(v)} for t, v in rows or []]
    nums = [float(v) for v in values] if values is not None else [r["value"] for r in row_dicts]
    return SystemAnswer(text=text, numeric_values=nums, rows=row_dicts, fact_ids=[1],
                        refused=refused, hedged=hedged)


def screen_answer(rows: list[tuple[str, float]], named: list[str] | None = None) -> SystemAnswer:
    """A screen answer listing ``rows`` and naming them plus ``named``."""
    return answer(text_for([t for t, _ in rows] + list(named or [])), rows=rows)


class Kit:
    M = M
    MRK_TEXT = MRK_TEXT
    screen_case = staticmethod(screen_case)
    peer_case = staticmethod(peer_case)
    agg_case = staticmethod(agg_case)
    drop = staticmethod(drop)
    text_for = staticmethod(text_for)
    answer = staticmethod(answer)
    screen_answer = staticmethod(screen_answer)


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()
