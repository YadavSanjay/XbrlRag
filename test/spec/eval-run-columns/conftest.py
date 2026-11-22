"""Fakes and builders for the eval-run-columns spec tests.

Spec: docs/specs/eval-run-columns.md. The fakes sit only at the I/O boundaries the spec
names:

- ``FakeSystem``: a ``SystemUnderTest`` behind the real ``EvalRunner``, answering each
  case right or wrong as configured and recording which cases it answered;
- ``fake_db``: a fake ``psycopg2`` module (installed in ``sys.modules`` for the duration
  of a ``with`` block) whose connection records every statement. Like psycopg2, its
  cursor raises ``KeyError`` for a ``%(name)s`` placeholder missing from the parameters;
- the committed DDL file, read as text.

Graders, ``Metrics``, ``RunReport``, the runner and the store are real. Fixtures are
session-scoped factories so hypothesis tests can use them. Everything is synthetic.
"""

from __future__ import annotations

import contextlib
import re
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.system import SystemAnswer

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]

_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


# ---------------------------------------------------------------------------- cases


def _case(case_id: str, tags: list[str] | None = None, *,
          paraphrase_group: str | None = None) -> EvalCase:
    """A scalar lookup expecting 1.0, grounded on fact 1."""
    return EvalCase(id=case_id, question=f"question {case_id}", kind="lookup",
                    expected_value=1.0, expected_fact_ids=[1], tags=list(tags or []),
                    paraphrase_group=paraphrase_group)


@pytest.fixture(scope="session")
def make_case():
    """Factory: ``make_case(case_id, tags=None, paraphrase_group=None) -> EvalCase``."""
    return _case


def _answer(correct: bool) -> SystemAnswer:
    return SystemAnswer(text="synthetic", numeric_values=[1.0 if correct else 5.0],
                        fact_ids=[1])


def _graded(case: EvalCase, correct: bool = True,
            judge_scores: Any = "absent") -> CaseResult:
    """Grades *case* with the real grader; a right answer is CORRECT, a wrong one
    INCORRECT. *judge_scores* is set on the grade as the judge would (by attribute)."""
    ans = _answer(correct)
    result = CaseResult.build(case, ans, grade_case(case, ans))
    if judge_scores != "absent":
        result.grade.judge_scores = judge_scores  # type: ignore[attr-defined]  # judge's attribute
    return result


@pytest.fixture(scope="session")
def graded():
    """Factory: ``graded(case, correct=True, judge_scores="absent") -> CaseResult``."""
    return _graded


def _report(results: list[CaseResult], *, config: dict[str, Any] | None = None,
            run_id: str = "run-spec") -> RunReport:
    return RunReport(run_id=run_id, suite="spec_suite", suite_version="v1",
                     system="fake_system", status="OK", results=list(results),
                     n_planned=len(results), config=dict(config or {}),
                     finished_at="2026-01-01T00:00:00+00:00",
                     started_at="2026-01-01T00:00:00+00:00")


@pytest.fixture(scope="session")
def make_report():
    """Factory: ``make_report(results, config=None, run_id=...) -> RunReport``."""
    return _report


# ---------------------------------------------------------------------------- system


class FakeSystem:
    """Answers each case right unless its id is in *wrong*; records the ids answered."""

    name = "fake_system"

    def __init__(self, wrong: set[str] | None = None) -> None:
        self.wrong = set(wrong or ())
        self.answered: list[str] = []

    def answer(self, case: EvalCase) -> SystemAnswer:
        self.answered.append(case.id)
        return _answer(case.id not in self.wrong)


@pytest.fixture(scope="session")
def fake_system():
    """Factory: ``fake_system(wrong=None) -> FakeSystem``."""
    return FakeSystem


# ---------------------------------------------------------------------------- database


class Recorder:
    """Every statement the fake driver received, as ``(sql, params)`` pairs."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.connects = 0

    def _for(self, table: str) -> list[tuple[str, dict[str, Any]]]:
        pat = re.compile(rf"insert\s+into\s+eval\.{table}\s*\(", re.IGNORECASE)
        return [(s, p) for s, p in self.statements if pat.search(s)]

    @property
    def run_rows(self) -> list[dict[str, Any]]:
        return [p for _s, p in self._for("eval_run")]

    @property
    def case_rows(self) -> list[dict[str, Any]]:
        return [p for _s, p in self._for("eval_case_result")]

    def insert_columns(self, table: str) -> list[str]:
        """The column list of the (last) INSERT into ``eval.<table>``."""
        stmts = self._for(table)
        assert stmts, f"no INSERT into eval.{table}"
        sql = stmts[-1][0]
        m = re.search(rf"insert\s+into\s+eval\.{table}\s*\(([^)]*)\)", sql, re.IGNORECASE)
        assert m is not None
        return [c.strip().lower() for c in m.group(1).split(",")]


class _Cursor:
    def __init__(self, rec: Recorder) -> None:
        self.rec = rec

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if isinstance(params, dict):
            for name in _PLACEHOLDER.findall(sql):
                if name not in params:
                    raise KeyError(name)  # what psycopg2 does for a missing placeholder
        self.rec.statements.append((sql, dict(params) if isinstance(params, dict) else {}))

    def executemany(self, sql: str, rows: Any) -> None:
        for row in rows:
            self.execute(sql, row)


class _Conn:
    def __init__(self, rec: Recorder) -> None:
        self.rec = rec

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self.rec)

    def close(self) -> None:
        return None


@contextlib.contextmanager
def _fake_db() -> Iterator[Recorder]:
    rec = Recorder()

    def connect(**_kwargs: Any) -> _Conn:
        rec.connects += 1
        return _Conn(rec)

    module = types.ModuleType("psycopg2")
    module.connect = connect  # type: ignore[attr-defined]  # the driver's entry point
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "psycopg2", module)
        yield rec


@pytest.fixture(scope="session")
def fake_db():
    """Context manager factory: ``with fake_db() as rec:`` installs a fake psycopg2."""
    return _fake_db


# ---------------------------------------------------------------------------- committed files


@pytest.fixture(scope="session")
def eval_runs_sql() -> str:
    """The committed scripts/ddl/eval/eval_runs.sql, as text."""
    return (REPO_ROOT / "scripts" / "ddl" / "eval" / "eval_runs.sql").read_text(
        encoding="utf-8")
