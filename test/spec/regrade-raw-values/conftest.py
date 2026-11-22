"""Builders for the regrade-raw-values spec tests (docs/specs/regrade-raw-values.md).

Pattern copied from the grading-stated-aggregate spec conftest:

- ``agg_case``: a ``screen.sum`` / ``screen.median`` v2 record in the shape v2-suite-build
  emits, with ``expected_value`` given by the test.
- ``answer``: a cited SystemAnswer whose values are what ``extract_stated_numbers`` reads
  from its text (XbrlRag stores them that way), and whose rows are given.
- ``real_case`` / ``real_answer`` / ``real_payload``: ``int-bd7ee7bd1bce-0231b50a`` (dev split,
  public SEC XBRL values), from ``fixtures/int_bd7ee7bd1bce_0231b50a.json`` (a byte copy of the
  grading-stated-aggregate fixture: the stored answer's text, rows, hedged flag and the
  filtered ``numeric_values`` the run stored).

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

FIXTURE = Path(__file__).parent / "fixtures" / "int_bd7ee7bd1bce_0231b50a.json"
REAL_RANKED = ["INTC", "NVDA", "QCOM", "MU", "AVGO", "AMD", "TXN"]
# The systems requirement 2 rebuilds with parse_structured_answer.
BASELINE_ARMS = ("closed_book", "naive_rag_filtered", "naive_rag_plain", "long_context_oracle",
                 "long_context_resolved", "long_context_multi", "frontier_sec_tools")
# The systems requirement 2 rebuilds with extract_stated_numbers (examples).
XBRLRAG_ARMS = ("xbrlrag_full", "xbrlrag_custom_arm")


def _tags(shape: str) -> list[str]:
    return ["split:dev", "intent:int-rrv", "family:formal", "key_source:sec_companyfacts",
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
        "id": case_id or f"v2-rrv-{shape}", "suite": "xbrl_v2_dev", "kind": "compute",
        "origin": f"v2:{shape}", "fy": fy, "unit": "USD", "rel_tol": 1e-4,
        "question": f"What was the {shape.split('.')[1]} of the metric across the {universe}?",
        "tags": _tags(shape), "expected_value": expected_value,
        "disclosures": {"universe": universe, "members": len(ranked) + len(excluded),
                        "ranked": list(ranked), "excluded": [dict(e) for e in excluded],
                        "period_ends": {}, "disclose_period_ends": False},
        "key": _key(),
    })


def answer(text: str, rows: list[tuple[str, float]], *, hedged: bool = False,
           refused: bool = False, error: str | None = None) -> SystemAnswer:
    """A cited answer; its values are the amounts its text states."""
    values = [n.value for n in extract_stated_numbers(text)]
    return SystemAnswer(text=text, numeric_values=values,
                        rows=[{"ticker": t, "value": float(v)} for t, v in rows],
                        fact_ids=[1], refused=refused, hedged=hedged, error=error)


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


def real_answer() -> SystemAnswer:
    """The stored answer, with the values its text states (as the system produced them)."""
    rec = real_record()
    rows = [(r["ticker"], r["value"]) for r in rec["rows"]]
    return answer(rec["text"], rows, hedged=rec["hedged"], refused=rec["refused"])


def real_payload() -> dict[str, Any]:
    """The stored ``produced`` payload: text, rows, flags and the filtered stored values,
    without ``raw_numeric_values`` (a run graded before this change)."""
    rec = real_record()
    return {
        "text": rec["text"],
        "numeric_values": list(rec["numeric_values_stored"]),
        "rows": [dict(r) for r in rec["rows"]],
        "fact_ids": [1],
        "chunk_ids": [],
        "refused": rec["refused"],
        "hedged": rec["hedged"],
        "error": None,
    }


class Kit:
    agg_case = staticmethod(agg_case)
    answer = staticmethod(answer)
    real_record = staticmethod(real_record)
    real_case = staticmethod(real_case)
    real_answer = staticmethod(real_answer)
    real_payload = staticmethod(real_payload)
    BASELINE_ARMS = BASELINE_ARMS
    XBRLRAG_ARMS = XBRLRAG_ARMS


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()
