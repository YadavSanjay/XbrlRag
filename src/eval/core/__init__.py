"""WS-9 / E1 — benchmark-agnostic evaluation framework.

Design: [docs/design/XbrlRag_Evaluation.md](../../../docs/design/XbrlRag_Evaluation.md).

Extends the WS-2 accuracy spine (``src.eval.xbrl``) rather than replacing it: the
13-case golden set, its grader, drift detection and the prompt×model matrix all keep
working — ``src.eval.xbrl.numeric`` now re-exports from here, so there is still
exactly one implementation of the tolerance/extraction logic.

    from src.eval.core import EvalSuite, EvalRunner, RunnerConfig
    from src.eval.systems.xbrlrag_full import XbrlRagFullSystem

    suite  = EvalSuite.load("test/eval/suites/sql_derived_v1.json")
    report = EvalRunner(RunnerConfig(max_workers=8, max_cost_usd=5.0)).run_suite(
        suite, XbrlRagFullSystem()
    )
    print(report.metrics.to_dict())
"""

from src.eval.core.case import (
    KIND_BREAKDOWN,
    KIND_COMPUTE,
    KIND_DIMENSIONAL,
    KIND_LOOKUP,
    KIND_NARRATIVE,
    KIND_SCREEN,
    Coverage,
    EvalCase,
    EvalSuite,
)
from src.eval.core.graders.base import CaseGrade, Grade
from src.eval.core.graders.dispatch import grade_all, grade_case
from src.eval.core.report import (
    CaseResult,
    Metrics,
    RunComparison,
    RunReport,
    compare_runs,
    paraphrase_stability,
)
from src.eval.core.runner import EvalRunner, RunnerConfig, run_suite
from src.eval.core.system import CallableSystem, SystemAnswer, SystemUnderTest

__all__ = [
    "CallableSystem",
    "CaseGrade",
    "CaseResult",
    "Coverage",
    "EvalCase",
    "EvalRunner",
    "EvalSuite",
    "Grade",
    "KIND_BREAKDOWN",
    "KIND_COMPUTE",
    "KIND_DIMENSIONAL",
    "KIND_LOOKUP",
    "KIND_NARRATIVE",
    "KIND_SCREEN",
    "Metrics",
    "RunComparison",
    "RunReport",
    "RunnerConfig",
    "SystemAnswer",
    "SystemUnderTest",
    "compare_runs",
    "grade_all",
    "grade_case",
    "paraphrase_stability",
    "run_suite",
]
