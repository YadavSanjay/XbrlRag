"""WS-2 / A4 — continuous eval / drift detection for the XBRL flow.

Filing-season index swaps and model upgrades can silently regress answer quality.
This re-scores the committed golden set against a stored **baseline** and raises a
``drift_alert`` when accuracy (or the citation rate — a faithfulness proxy) falls
more than a tolerance below baseline. Designed to run on a schedule
(``scripts/eval/run_drift_check.py``); the baseline is snapshotted by
``scripts/eval/build_baseline.py``.

Built directly on A3's :class:`~src.eval.xbrl.golden.NumericGrader` — same golden
set, same tolerance grader.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.eval.xbrl.golden import GoldenCase, NumericGrader
from src.observability.trace_context import log_event


@dataclass
class Baseline:
    """A recorded snapshot of golden-set quality to compare future runs against."""

    accuracy: float
    cited_rate: float
    per_tag: dict[str, float] = field(default_factory=dict)
    generated_at: str | None = None
    n_cases: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Baseline:
        return cls(
            accuracy=float(d["accuracy"]),
            cited_rate=float(d.get("cited_rate", 0.0)),
            per_tag={k: float(v) for k, v in (d.get("per_tag") or {}).items()},
            generated_at=d.get("generated_at"),
            n_cases=d.get("n_cases"),
        )

    @classmethod
    def load(cls, path: str) -> Baseline:
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return {
            "accuracy": round(self.accuracy, 4),
            "cited_rate": round(self.cited_rate, 4),
            "per_tag": {k: round(v, 4) for k, v in self.per_tag.items()},
            "generated_at": self.generated_at,
            "n_cases": self.n_cases,
        }


@dataclass
class DriftReport:
    current_accuracy: float
    baseline_accuracy: float
    accuracy_drop: float
    current_cited_rate: float
    baseline_cited_rate: float
    cited_drop: float
    drifted: bool
    reasons: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "drifted": self.drifted,
            "current_accuracy": round(self.current_accuracy, 4),
            "baseline_accuracy": round(self.baseline_accuracy, 4),
            "accuracy_drop": round(self.accuracy_drop, 4),
            "current_cited_rate": round(self.current_cited_rate, 4),
            "baseline_cited_rate": round(self.baseline_cited_rate, 4),
            "cited_drop": round(self.cited_drop, 4),
            "reasons": self.reasons,
            "failures": self.failures,
        }


def _cited_rate(results) -> float:
    return sum(1 for r in results if r.has_citation) / len(results) if results else 0.0


def per_tag_accuracy(cases: list[GoldenCase], results) -> dict[str, float]:
    """Accuracy grouped by case tag (results are aligned 1:1 with cases)."""
    by_id = {r.case_id: r for r in results}
    tag_pass: dict[str, int] = {}
    tag_total: dict[str, int] = {}
    for c in cases:
        r = by_id.get(c.id)
        for tag in c.tags or ["untagged"]:
            tag_total[tag] = tag_total.get(tag, 0) + 1
            if r is not None and r.passed:
                tag_pass[tag] = tag_pass.get(tag, 0) + 1
    return {t: tag_pass.get(t, 0) / n for t, n in tag_total.items()}


class DriftDetector:
    """Re-scores the golden set and flags drift vs a baseline."""

    def __init__(
        self,
        cases: list[GoldenCase],
        baseline: Baseline,
        *,
        accuracy_tolerance: float = 0.1,
        cited_tolerance: float = 0.1,
    ):
        self.cases = cases
        self.baseline = baseline
        self.accuracy_tolerance = accuracy_tolerance
        self.cited_tolerance = cited_tolerance

    def check(self, run_fn: Callable[[str], Any]) -> DriftReport:
        report = NumericGrader(self.cases).grade(run_fn)
        current_acc = report.accuracy
        current_cited = _cited_rate(report.results)

        acc_drop = self.baseline.accuracy - current_acc
        cited_drop = self.baseline.cited_rate - current_cited
        reasons: list[str] = []
        if acc_drop > self.accuracy_tolerance:
            reasons.append(
                f"accuracy {current_acc:.1%} is {acc_drop:.1%} below baseline "
                f"{self.baseline.accuracy:.1%} (tol {self.accuracy_tolerance:.0%})")
        if cited_drop > self.cited_tolerance:
            reasons.append(
                f"citation rate {current_cited:.1%} is {cited_drop:.1%} below baseline "
                f"{self.baseline.cited_rate:.1%} (tol {self.cited_tolerance:.0%})")

        drifted = bool(reasons)
        drift = DriftReport(
            current_accuracy=current_acc,
            baseline_accuracy=self.baseline.accuracy,
            accuracy_drop=acc_drop,
            current_cited_rate=current_cited,
            baseline_cited_rate=self.baseline.cited_rate,
            cited_drop=cited_drop,
            drifted=drifted,
            reasons=reasons,
            failures=[r.to_dict() for r in report.results if not r.passed],
        )
        if drifted:
            try:
                log_event("drift_alert", source="eval", **drift.to_dict())
            except Exception:  # pragma: no cover - telemetry must not break the check
                pass
        return drift
