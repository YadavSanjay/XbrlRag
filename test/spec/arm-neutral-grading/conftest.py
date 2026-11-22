"""Builders for the arm-neutral-grading spec tests.

Session-scoped so hypothesis-driven tests can use them without tripping the
function-scoped-fixture health check. Everything is synthetic: hand-built cases and
answers, no suite files, no DB.
"""

from __future__ import annotations

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.report import CaseResult
from src.eval.core.system import SystemAnswer

SCREEN_ROWS = [
    {"ticker": "AAA", "value": 300.0},
    {"ticker": "BBB", "value": 200.0},
    {"ticker": "CCC", "value": 100.0},
]


@pytest.fixture(scope="session")
def scalar_case():
    """Factory: a `lookup` case expecting `value`."""

    def make(
        value: float = 100.0,
        *,
        rel_tol: float = 1e-4,
        expected_fact_ids: list[int] | None = None,
        case_id: str = "s1",
    ) -> EvalCase:
        return EvalCase(
            id=case_id,
            question="What was X in fiscal 2024?",
            kind="lookup",
            expected_value=value,
            expected_fact_ids=list(expected_fact_ids or []),
            rel_tol=rel_tol,
        )

    return make


@pytest.fixture(scope="session")
def screen_case():
    """Factory: a top-3 cross-filer `screen` case ranked AAA > BBB > CCC."""

    def make(case_id: str = "sc1") -> EvalCase:
        return EvalCase(
            id=case_id,
            question="List the top 3 companies by X in fiscal 2024, ranked.",
            kind="screen",
            expected_rows=[dict(r) for r in SCREEN_ROWS],
            top_n=3,
            rel_tol=1e-4,
        )

    return make


@pytest.fixture(scope="session")
def breakdown_case():
    """Factory: a `breakdown` case whose ground truth is `components`."""

    def make(components: list[float], case_id: str = "b1") -> EvalCase:
        return EvalCase(
            id=case_id,
            question="Break down X's fiscal 2024 revenue by segment.",
            kind="breakdown",
            expected_rows=[{"label": f"m{i}", "value": v} for i, v in enumerate(components)],
            rel_tol=1e-4,
        )

    return make


@pytest.fixture(scope="session")
def answer():
    """Factory: a system answer with the given numbers, rows and decline signals.

    `cited=True` attaches one fact id per value (or per row); `fact_ids` overrides that.
    """

    def make(
        values: list[float] | None = None,
        *,
        rows: list[dict] | None = None,
        hedged: bool = False,
        refused: bool = False,
        cited: bool = True,
        fact_ids: list[int] | None = None,
    ) -> SystemAnswer:
        values = list(values or [])
        rows = [dict(r) for r in (rows or [])]
        n_items = max(len(values), len(rows))
        if fact_ids is None:
            fact_ids = list(range(1, n_items + 1)) if (cited and n_items) else []
        return SystemAnswer(
            text="synthetic answer",
            numeric_values=values,
            rows=rows,
            fact_ids=list(fact_ids),
            hedged=hedged,
            refused=refused,
        )

    return make


@pytest.fixture(scope="session")
def uncited():
    """Returns a copy of an answer with every fact id removed."""

    def make(ans: SystemAnswer) -> SystemAnswer:
        return SystemAnswer(
            text=ans.text,
            numeric_values=list(ans.numeric_values),
            rows=[dict(r) for r in ans.rows],
            fact_ids=[],
            hedged=ans.hedged,
            refused=ans.refused,
        )

    return make


@pytest.fixture(scope="session")
def graded():
    """Factory: grade one (case, answer) pair into a CaseResult, as the runner does."""

    def make(case: EvalCase, ans: SystemAnswer) -> CaseResult:
        return CaseResult.build(case, ans, grade_case(case, ans))

    return make
