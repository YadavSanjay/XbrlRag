"""Builders for the eval-grader-honesty spec tests.

Session-scoped so hypothesis-driven tests can use them without tripping the
function-scoped-fixture health check. Everything is synthetic: hand-built cases and
answers, no suite files, no DB.
"""

from __future__ import annotations

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_breakdown, grade_scalar
from src.eval.core.report import CaseResult
from src.eval.core.system import SystemAnswer


@pytest.fixture(scope="session")
def scalar_case():
    """Factory: a `lookup` case expecting `value` (no expected fact ids)."""

    def make(value: float = 100.0, rel_tol: float = 1e-4, case_id: str = "s1") -> EvalCase:
        return EvalCase(
            id=case_id,
            question="What was X in fiscal 2024?",
            kind="lookup",
            expected_value=value,
            rel_tol=rel_tol,
        )

    return make


@pytest.fixture(scope="session")
def breakdown_case():
    """Factory: a `breakdown` case whose ground truth is `components`."""

    def make(components: list[float], rel_tol: float = 1e-4, case_id: str = "b1") -> EvalCase:
        return EvalCase(
            id=case_id,
            question="Break down X's fiscal 2024 revenue by segment.",
            kind="breakdown",
            expected_rows=[{"label": f"m{i}", "value": v} for i, v in enumerate(components)],
            rel_tol=rel_tol,
        )

    return make


@pytest.fixture(scope="session")
def answer():
    """Factory: a system answer with the given numbers and refusal signals."""

    def make(
        values: list[float] | None = None,
        *,
        hedged: bool = False,
        refused: bool = False,
        cited: bool = True,
    ) -> SystemAnswer:
        values = list(values or [])
        return SystemAnswer(
            text="synthetic answer",
            numeric_values=values,
            fact_ids=list(range(1, len(values) + 1)) if (cited and values) else [],
            hedged=hedged,
            refused=refused,
        )

    return make


@pytest.fixture(scope="session")
def graded():
    """Factory: grade one (case, answer) pair into a CaseResult, as the runner does."""

    def make(case: EvalCase, ans: SystemAnswer) -> CaseResult:
        grader = grade_breakdown if case.kind == "breakdown" else grade_scalar
        return CaseResult.build(case, ans, grader(case, ans))

    return make
