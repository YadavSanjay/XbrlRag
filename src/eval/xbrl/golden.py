"""WS-2 / A3 — static golden set + numeric-tolerance grader for XBRL answers.

A :class:`GoldenCase` pairs a natural-language query with a *labelled* expected
numeric answer (snapshotted from the fact store, so the set is static and
committed — see ``scripts/eval/build_golden_set.py``). :class:`NumericGrader`
runs each case's query through a caller-supplied ``run_fn`` (the live pipeline),
pulls the audited number out of the structured ``execution_result`` (never the
prose), and scores it with :func:`~src.eval.xbrl.numeric.close_enough`.

The grader answers the question A4 / B10 / H3 all depend on: *what fraction of a
labelled set does the pipeline get right, within tolerance, with a citation?*

:func:`numeric_tolerance_scorer` adapts the grader to the
``src.eval.regression.prompt_matrix`` scorer signature
(``(query, result) -> Dict[str, float]``) so the same golden set drives the
prompt×model regression matrix (B10).
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.eval.xbrl.numeric import all_numeric_values, close_enough, has_fact_citation

# The committed static golden set (repo-relative: <root>/test/eval/golden/xbrl_golden_v1.json).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_GOLDEN_PATH = os.path.join(_REPO_ROOT, "test", "eval", "golden", "xbrl_golden_v1.json")


@dataclass
class GoldenCase:
    """One labelled query → expected-number case."""

    id: str
    query: str
    expected_value: float
    kind: str = "lookup"                       # "lookup" | "compute" | "dimensional"
    unit: str | None = None
    rel_tol: float = 1e-6
    alt_values: list[float] = field(default_factory=list)  # accepted alternate forms
    require_citation: bool = True
    # Informational provenance / labels.
    ticker: str | None = None
    fy: int | None = None
    concept: str | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> GoldenCase:
        known = {f for f in cls.__dataclass_fields__}  # noqa: F841 - clarity
        return cls(
            id=d["id"],
            query=d["query"],
            expected_value=float(d["expected_value"]),
            kind=d.get("kind", "lookup"),
            unit=d.get("unit"),
            rel_tol=float(d.get("rel_tol", 1e-6)),
            alt_values=[float(v) for v in d.get("alt_values", [])],
            require_citation=bool(d.get("require_citation", True)),
            ticker=d.get("ticker"),
            fy=d.get("fy"),
            concept=d.get("concept"),
            tags=list(d.get("tags", [])),
        )

    def targets(self) -> list[float]:
        return [self.expected_value, *self.alt_values]


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    expected: float
    matched_value: float | None
    produced_values: list[float]
    has_citation: bool
    reason: str
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "expected": self.expected,
            "matched_value": self.matched_value,
            "produced_values": self.produced_values,
            "has_citation": self.has_citation,
            "reason": self.reason,
            "error": self.error,
        }


@dataclass
class GradeReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "accuracy": round(self.accuracy, 4),
            "failures": [r.to_dict() for r in self.results if not r.passed],
            "results": [r.to_dict() for r in self.results],
        }


def load_golden_set(path: str) -> list[GoldenCase]:
    """Load a committed golden-set JSON file into :class:`GoldenCase` objects."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"] if isinstance(data, dict) else data
    return [GoldenCase.from_dict(c) for c in cases]


def grade_response(case: GoldenCase, resp: Any) -> CaseResult:
    """Grade one already-produced response against a case (pure — no pipeline).

    Passes iff some audited number in the response is within `case.rel_tol` of the
    expected value (or an accepted alternate form) AND — when required — the answer
    carries a fact-store citation."""
    produced = all_numeric_values(resp)
    cited = has_fact_citation(resp)
    matched: float | None = None
    for v in produced:
        if any(close_enough(v, t, case.rel_tol) for t in case.targets()):
            matched = v
            break

    if matched is None:
        reason = (f"no produced value within rel_tol={case.rel_tol:g} of "
                  f"{case.expected_value:g}; produced={produced}")
        return CaseResult(case.id, False, case.expected_value, None, produced, cited, reason)
    if case.require_citation and not cited:
        return CaseResult(case.id, False, case.expected_value, matched, produced, cited,
                          "value matched but the answer carries no fact_id citation")
    return CaseResult(case.id, True, case.expected_value, matched, produced, cited, "ok")


class NumericGrader:
    """Runs a golden set through a pipeline ``run_fn`` and scores each case."""

    def __init__(self, cases: list[GoldenCase]):
        self.cases = cases

    def grade(self, run_fn: Callable[[str], Any]) -> GradeReport:
        """`run_fn(query) -> XbrlRagResponse`. A run that raises is a failed case,
        not a crashed harness."""
        report = GradeReport()
        for case in self.cases:
            try:
                resp = run_fn(case.query)
            except Exception as exc:  # a broken pipeline fails the case, not the run
                report.results.append(CaseResult(
                    case.id, False, case.expected_value, None, [], False,
                    "run_fn raised", error=repr(exc)))
                continue
            report.results.append(grade_response(case, resp))
        return report


def numeric_tolerance_scorer(cases: list[GoldenCase]) -> Callable[[str, Any], dict[str, float]]:
    """Adapt the golden set to the ``prompt_matrix`` scorer signature (B10):
    ``(query, result) -> {"correct": 0/1, "cited": 0/1}``. A query with no matching
    golden case yields an empty dict (it simply doesn't contribute to the score)."""
    by_query = {c.query: c for c in cases}

    def _scorer(query: str, result: Any) -> dict[str, float]:
        case = by_query.get(query)
        if case is None:
            return {}
        r = grade_response(case, result)
        return {"correct": 1.0 if r.passed else 0.0,
                "cited": 1.0 if r.has_citation else 0.0}

    return _scorer
