"""WS-9 / E1 — the per-case verdict types shared by every grader.

``REFUSED`` is deliberately kept apart from ``INCORRECT``: XbrlRag's synthesizer is
*designed* to decline rather than hallucinate, so collapsing the two would punish
the safe behaviour. The counterweight is that refusal rate is reported alongside
accuracy — a system cannot game silent-wrong-rate to zero by refusing everything
without that showing up (design doc §7.1–§7.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Grade(str, Enum):
    CORRECT = "CORRECT"
    PARTIAL = "PARTIAL"  # right number, unaudited — or partially-right rows
    INCORRECT = "INCORRECT"
    REFUSED = "REFUSED"  # declined to answer; not a wrong answer
    # Hedged ("can't determine X; the nearest fact is Y") with numbers, none of them the
    # answer. Asserts nothing false, so not INCORRECT; returned numbers, so not REFUSED.
    HEDGED_MISS = "HEDGED_MISS"
    # Stated less precisely than the key's rule, and the key rounded at the answer's
    # own precision (docs/specs/grading-precision.md requirement 8). Neither correct
    # nor wrong: it counts towards neither value accuracy nor confident-wrong.
    IMPRECISE = "IMPRECISE"
    ERROR = "ERROR"  # the system raised


@dataclass
class CaseGrade:
    """A pure verdict on one (case, answer) pair — no execution metadata.

    Graders are pure functions of ``(EvalCase, SystemAnswer)``; the runner attaches
    latency / cost / query_id afterwards. That split keeps every grader unit-testable
    with no backends.
    """

    case_id: str
    grade: Grade
    passed: bool
    reason: str
    expected: dict[str, Any] = field(default_factory=dict)
    produced: dict[str, Any] = field(default_factory=dict)

    matched_value: float | None = None
    has_citation: bool = False
    grounding_precision: float | None = None
    grounding_recall: float | None = None
    screen_metrics: dict[str, Any] | None = None
    error: str | None = None
    # Set when the answer's numbers are real and correctly cited but do not cover — or
    # decompose — what was asked. See `silent_wrong` for why that is excluded.
    incomplete: bool = False
    # The judge's sub-scores (correctness, completeness, citation_support,
    # unsupported_claims), set by the judge; None when no judge ran
    # (docs/specs/eval-run-columns.md).
    judge_scores: dict[str, Any] | None = None
    # The v2 grader's named sub-checks (docs/specs/v2-grading.md requirement 4), e.g.
    # "universe", "exclusions", "FY2023", "assumption[0]". None for a legacy grade.
    checks: dict[str, bool] | None = None
    # Which accepted reading each matched number is ("key" or a reading name such as
    # "cover_page"; docs/specs/shares-outstanding-reading.md). A scalar uses the name
    # "value", a trend its row names. None when the case has no alternate readings, or a
    # scalar matched nothing.
    matched_readings: dict[str, str] | None = None
    # A compound's value-correctness: every graded part value-correct, cited or not
    # (docs/specs/compound-value-correct.md). None for every other grade.
    parts_value_correct: bool | None = None

    @property
    def silent_wrong(self) -> bool:
        """A confident, cited, **wrong number** — the worst failure mode for a
        financial product, and the metric VISION.md's un-cited-generation non-goal
        exists to gate. Hedged answers do not count: flagging uncertainty is not
        silent.

        Deliberately excludes ``incomplete`` answers. A breakdown that returns real,
        correctly-cited facts at a *different decomposition level* is answering a
        different question, not asserting a false number: NVIDIA FY2025 returned Data
        Center 115,186,000,000 where the ground truth wanted its children Compute
        102,196,000,000 + Networking 12,990,000,000 — which sum to exactly that.
        Counting such an answer as silent-wrong conflates a **relevance** failure with
        a **faithfulness** one and makes the release gate cry wolf.
        """
        if self.incomplete:
            return False
        return (
            self.grade is Grade.INCORRECT
            and self.has_citation
            and not self.produced.get("hedged", False)
        )

    @property
    def needs_review(self) -> bool:
        """An unhedged, failing breakdown that asserted an amount no expected component
        or subset of components accounts for.

        The ``incomplete`` exemption above keeps a different-decomposition answer off
        the release gate, but would also hide a genuinely false component. This flag
        routes exactly those answers to a human instead of silently passing them.
        """
        if not self.incomplete or self.produced.get("hedged", False):
            return False
        return bool((self.screen_metrics or {}).get("unexplained_values"))

    @property
    def confident_wrong(self) -> bool:
        """A wrong answer the system neither refused nor hedged, cited or not.

        The arm-neutral counterpart of `silent_wrong`: a baseline never cites a
        ``fact_id``, so only this metric can catch it asserting a false number. The
        ``incomplete`` exemption keeps its meaning (a relevance miss, not a false
        number) unless the breakdown also `needs_review`.
        """
        if self.grade is not Grade.INCORRECT:
            return False
        if self.produced.get("refused", False) or self.produced.get("hedged", False):
            return False
        return not self.incomplete or self.needs_review

    @property
    def value_correct(self) -> bool:
        """Whether the answer's numbers were right, ignoring citations and grounding.

        Every ``PARTIAL`` that a missing or wrong citation produced still carries the
        evidence of the match: a scalar's ``matched_value``, or the ``screen_metrics``
        of a screen or breakdown whose ranking or components passed. The other
        ``PARTIAL`` (a case with nothing to compare against) carries neither. A compound
        carries its parts' verdict in ``parts_value_correct`` instead, because its own
        grade is a roll-up with no single matched value.
        """
        if self.parts_value_correct is not None:
            return self.parts_value_correct
        if self.grade is Grade.CORRECT:
            return True
        if self.grade is not Grade.PARTIAL:
            return False
        return self.matched_value is not None or self.screen_metrics is not None

    def to_dict(self) -> dict:
        out = {
            "case_id": self.case_id,
            "grade": self.grade.value,
            "passed": self.passed,
            "reason": self.reason,
            "expected": self.expected,
            "produced": self.produced,
            "matched_value": self.matched_value,
            "has_citation": self.has_citation,
            "grounding_precision": self.grounding_precision,
            "grounding_recall": self.grounding_recall,
            "screen_metrics": self.screen_metrics,
            "silent_wrong": self.silent_wrong,
            "incomplete": self.incomplete,
            "needs_review": self.needs_review,
            "confident_wrong": self.confident_wrong,
            "value_correct": self.value_correct,
            "error": self.error,
        }
        if self.checks is not None:
            out["checks"] = self.checks
        if self.matched_readings is not None:
            out["matched_readings"] = self.matched_readings
        if self.parts_value_correct is not None:
            out["parts_value_correct"] = self.parts_value_correct
        return out


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (*q* in 0..1). Dependency-free on purpose —
    the eval core must import without numpy/scipy so it stays usable in the
    torch-free serving image."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac
