"""Fixtures for the grading-precision spec tests (docs/specs/grading-precision.md).

- ``dev``: real cases from the frozen dev split, copied verbatim into
  ``fixtures/dev_cases.json`` (SEC companyfacts values, public domain), as ``EvalCase``;
- ``answer``: a gradable answer stating the numbers of a text the way a text arm's reply
  without a JSON object is read (``parse_structured_answer``'s free-text fallback), cited
  so a citation never decides a grade;
- ``xbrl_response`` / ``FakePipeline``: an ``XbrlRagResponse`` and an injected pipeline
  for ``XbrlRagFullSystem`` (the pipeline is the system's LLM/DB boundary).

Everything is offline: no data/ read, no network, no LLM.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

SPEC_DIR = Path(__file__).resolve().parent
_CASES = json.loads((SPEC_DIR / "fixtures" / "dev_cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def dev():
    """Factory: ``dev(name, **overrides) -> EvalCase`` for a named fixture case."""

    def make(name: str, **overrides: Any) -> EvalCase:
        record = copy.deepcopy(_CASES["cases"][name])
        record.update(overrides)
        return EvalCase.from_dict(record)

    return make


@pytest.fixture(scope="session")
def dev_record():
    """Factory: a deep copy of a named fixture case's raw record."""

    def make(name: str) -> dict[str, Any]:
        return copy.deepcopy(_CASES["cases"][name])

    return make


@pytest.fixture(scope="session")
def answer():
    """Factory: a text arm's free-text reply, cited (fact id 1) so citations never decide."""

    def make(text: str) -> SystemAnswer:
        parsed = parse_structured_answer(text)
        parsed.fact_ids = [1]
        return parsed

    return make


@pytest.fixture(scope="session")
def values_answer():
    """Factory: an answer carrying given values, rows and text, cited."""

    def make(values: list[float] | None = None, rows: list[dict] | None = None,
             text: str = "") -> SystemAnswer:
        return SystemAnswer(text=text, numeric_values=list(values or []),
                            rows=[dict(r) for r in rows or []], fact_ids=[1])

    return make


def _response(summary: str, details: list[dict] | None, steps: list[dict]) -> Any:
    from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse

    return XbrlRagResponse(
        query="q", answer_summary=summary, answer_details=list(details or []),
        unanswered_parts=[], metadata={"fully_answered": True}, plan={"steps": []},
        execution_result={"steps": steps}, narrative_chunks=[],
    )


@pytest.fixture(scope="session")
def xbrl_response():
    """Factory: ``xbrl_response(summary, details, steps) -> XbrlRagResponse``."""
    return _response


class FakePipeline:
    """An injected pipeline answering every question with one fixed response."""

    def __init__(self, response: Any) -> None:
        self.response = response

    def generate(self, question: str) -> Any:
        return self.response


@pytest.fixture(scope="session")
def xbrlrag_answer():
    """Factory: ``XbrlRagFullSystem`` over a ``FakePipeline`` answering one case."""
    from src.eval.systems.xbrlrag_full import XbrlRagFullSystem

    def make(case: EvalCase, response: Any) -> SystemAnswer:
        system = XbrlRagFullSystem(pipeline=FakePipeline(response), transient_retries=0)
        return system.answer(case)

    return make


def lookup_step(value: float, fact_id: int) -> dict:
    """An OK lookup step at full precision, as the executor writes it."""
    return {"step_id": f"s{fact_id}", "op": "lookup", "status": "OK", "value": value,
            "citation": {"fact_id": fact_id}}
