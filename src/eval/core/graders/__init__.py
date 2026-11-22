"""WS-9 / E1 — deterministic graders.

Everything here is a pure function of ``(EvalCase, SystemAnswer)``: numeric
tolerance, fact-id set comparison, rank correlation. No LLM, no DB, no network —
which keeps runs cheap and reproducible and avoids the doer≠judge problem entirely.
The LLM judge is scoped to narrative answers in Phase 3 (design doc §7.4).
"""

from src.eval.core.graders.base import CaseGrade, Grade, percentile
from src.eval.core.graders.breakdown import (
    BreakdownMetrics,
    breakdown_verdict,
    compare_breakdown,
)
from src.eval.core.graders.citation import GroundingResult, grade_grounding
from src.eval.core.graders.dispatch import (
    grade_all,
    grade_breakdown,
    grade_case,
    grade_narrative,
    grade_scalar,
    grade_screen,
)
from src.eval.core.graders.screen import ScreenMetrics, compare_screen, screen_verdict

__all__ = [
    "BreakdownMetrics",
    "CaseGrade",
    "Grade",
    "GroundingResult",
    "ScreenMetrics",
    "compare_screen",
    "breakdown_verdict",
    "compare_breakdown",
    "grade_all",
    "grade_breakdown",
    "grade_case",
    "grade_grounding",
    "grade_narrative",
    "grade_scalar",
    "grade_screen",
    "percentile",
    "screen_verdict",
]
