"""Builders for the grading-stated-aggregate spec tests
(docs/specs/grading-stated-aggregate.md).

- ``agg_case``: a ``screen.sum`` / ``screen.median`` v2 record in the shape v2-suite-build
  emits (pattern copied from the grading-dont-care-and-ties spec conftest), with
  ``expected_value`` given by the test.
- ``answer``: a cited SystemAnswer whose produced values are what ``extract_stated_numbers``
  reads from its text (the system stores them that way), and whose rows are given.
- ``real_case`` / ``real_answer``: ``int-bd7ee7bd1bce-0231b50a`` (dev split, public SEC XBRL
  values), from the committed fixture ``fixtures/int_bd7ee7bd1bce_0231b50a.json`` (the stored
  answer's text, rows and hedged flag, copied verbatim from slice run ``eval-d789be154ba8``).

Reached through the session-scoped ``kit`` fixture, so hypothesis tests can use it. Grading is
pure, so nothing is faked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.answer_parsing import extract_stated_numbers
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

M = 1e6
FIXTURE = Path(__file__).parent / "fixtures" / "int_bd7ee7bd1bce_0231b50a.json"
REAL_RANKED = ["INTC", "NVDA", "QCOM", "MU", "AVGO", "AMD", "TXN"]


def _tags(shape: str) -> list[str]:
    return ["split:dev", "intent:int-gsa", "family:formal", "key_source:sec_companyfacts",
            "key_status:verified", f"shape:{shape}"]


def _key() -> dict[str, Any]:
    return {"definition": None, "disagreements": [], "inputs": [],
            "policies": ["S1", "S2", "S3", "S5", "S6"], "same_filing": None,
            "source": "sec_companyfacts", "status": "verified"}


def agg_case(shape: str, ranked: list[str], expected_value: float, *,
             universe: str = "retailers", excluded: list[dict[str, Any]] | None = None,
             fy: int = 2025, case_id: str | None = None) -> EvalCase:
    """A ``screen.sum`` or ``screen.median`` record."""
    excluded = excluded or []
    return EvalCase.from_dict({
        "id": case_id or f"v2-gsa-{shape}", "suite": "xbrl_v2_dev", "kind": "compute",
        "origin": f"v2:{shape}", "fy": fy, "unit": "USD", "rel_tol": 1e-4,
        "question": f"What was the {shape.split('.')[1]} of the metric across the {universe}?",
        "tags": _tags(shape), "expected_value": expected_value,
        "disclosures": {"universe": universe, "members": len(ranked) + len(excluded),
                        "ranked": list(ranked), "excluded": [dict(e) for e in excluded],
                        "period_ends": {}, "disclose_period_ends": False},
        "key": _key(),
    })


def answer(text: str, rows: list[tuple[str, float]], *, hedged: bool = False,
           refused: bool = False) -> SystemAnswer:
    """A cited answer; its produced values are the amounts its text states."""
    values = [n.value for n in extract_stated_numbers(text)]
    return SystemAnswer(text=text, numeric_values=values,
                        rows=[{"ticker": t, "value": float(v)} for t, v in rows],
                        fact_ids=[1], refused=refused, hedged=hedged)


def real_record() -> dict[str, Any]:
    """The stored case and answer, as committed."""
    with FIXTURE.open(encoding="utf-8") as f:
        return json.load(f)


def real_case() -> EvalCase:
    """``int-bd7ee7bd1bce-0231b50a``: the median of the seven ranked semiconductors."""
    rec = real_record()
    return agg_case("screen.median", REAL_RANKED, rec["expected"]["value"],
                    universe="semiconductors", excluded=[{"ticker": "TSM", "reason": "currency"}],
                    fy=2023, case_id=rec["case_id"])


def real_answer(text: str | None = None, *, hedged: bool | None = None) -> SystemAnswer:
    """The stored answer; ``text`` / ``hedged`` replace the stored ones when given."""
    rec = real_record()
    rows = [(r["ticker"], r["value"]) for r in rec["rows"]]
    return answer(rec["text"] if text is None else text, rows,
                  hedged=rec["hedged"] if hedged is None else hedged,
                  refused=rec["refused"])


class Kit:
    M = M
    agg_case = staticmethod(agg_case)
    answer = staticmethod(answer)
    real_record = staticmethod(real_record)
    real_case = staticmethod(real_case)
    real_answer = staticmethod(real_answer)


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()
