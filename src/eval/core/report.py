"""WS-9 / E1 — per-case results and run-level aggregation.

Defines the metric set the ``eval`` schema persists and the UI renders (design doc
§7.2). Two of these do not exist in WS-2 and are the point of the workstream:

* **grounding precision/recall** — *the right fact*, not merely *a fact*.
* **silent-wrong rate** — a confident, cited, wrong number. The finance-critical
  failure, and a release gate rather than a dashboard number. Its honest
  counterweight, **refusal rate**, is reported beside it: silent-wrong is trivially
  driven to zero by refusing everything.

**paraphrase stability** measures how often every phrasing of the same underlying
question lands on the same verdict — a routing/planning determinism signal the
13-case golden set structurally cannot see.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import CaseGrade, Grade, percentile
from src.eval.core.graders.honesty import grader_version
from src.eval.core.system import SystemAnswer
from src.observability.service_tiers import TierCounts, merge_counts


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


@dataclass
class CaseResult:
    """A graded case plus the execution metadata the runner observed."""

    case: EvalCase
    grade: CaseGrade
    query_id: str | None = None
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    # Requested model id -> served ids; rolled up into the run's config, not persisted
    # per case (docs/specs/served-model-id.md).
    served_models: dict[str, list[str]] = field(default_factory=dict)
    # The answer's LLM calls by requested and served service tier, per provider and stage;
    # summed into the run's config["service_tier_calls"] (src/observability/service_tiers.py).
    service_tiers: TierCounts = field(default_factory=dict)
    # The claim judge's error text for this case and what judging it cost, set by the
    # graded runner in the case's own task; kept in the checkpoint payload so a resumed
    # run's judge totals are exact (docs/specs/eval-run-checkpoint.md, requirement 1).
    judge_error: str | None = None
    judge_cost_usd: float = 0.0
    # Why the graded runner did not judge this text case ("answer_error",
    # "synthesis_error", "synthesis_refusal"), kept in the checkpoint payload like
    # judge_error (docs/specs/grader-v9-number-reading.md, requirement 5).
    judge_skipped: str | None = None

    @classmethod
    def build(
        cls, case: EvalCase, answer: SystemAnswer, grade: CaseGrade
    ) -> CaseResult:
        return cls(
            case=case,
            grade=grade,
            query_id=answer.query_id,
            latency_ms=answer.latency_ms,
            cost_usd=answer.cost_usd,
            served_models=dict(getattr(answer, "served_models", None) or {}),
            service_tiers=merge_counts([getattr(answer, "service_tiers", None) or {}]),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], case: EvalCase) -> CaseResult:
        """Rebuilds a result from its checkpoint payload (docs/specs/eval-run-checkpoint.md).

        Args:
            data: A ``checkpoint_payload`` dict.
            case: The suite's case for it.

        Returns:
            The result, equal to the one the payload was taken from.

        Raises:
            ValueError: If ``data["case_id"]`` is not ``case.id``.
        """
        if data.get("case_id") != case.id:
            raise ValueError(
                f"checkpoint payload is for case {data.get('case_id')!r}, not {case.id!r}"
            )
        checks = data.get("checks")
        grade = CaseGrade(
            case_id=case.id,
            grade=Grade(data["grade"]),
            passed=bool(data["passed"]),
            reason=data.get("reason", ""),
            expected=dict(data.get("expected") or {}),
            produced=dict(data.get("produced") or {}),
            matched_value=data.get("matched_value"),
            has_citation=bool(data.get("has_citation", False)),
            grounding_precision=data.get("grounding_precision"),
            grounding_recall=data.get("grounding_recall"),
            screen_metrics=data.get("screen_metrics"),
            error=data.get("error"),
            incomplete=bool(data.get("incomplete", False)),
            judge_scores=data.get("judge_scores"),
            checks=dict(checks) if checks is not None else None,
            parts_value_correct=data.get("parts_value_correct"),
        )
        served = data.get("served_models") or {}
        return cls(
            case=case,
            grade=grade,
            query_id=data.get("query_id"),
            latency_ms=float(data.get("latency_ms") or 0.0),
            cost_usd=float(data.get("cost_usd") or 0.0),
            served_models={model: list(ids) for model, ids in served.items()},
            service_tiers=merge_counts([data.get("service_tiers") or {}]),
            judge_error=data.get("judge_error"),
            judge_cost_usd=float(data.get("judge_cost_usd") or 0.0),
            judge_skipped=data.get("judge_skipped"),
        )

    @property
    def passed(self) -> bool:
        return self.grade.passed

    def to_dict(self) -> dict:
        d = self.grade.to_dict()
        d.update(
            {
                "question": self.case.question,
                "kind": self.case.kind,
                "tags": self.case.tags,
                "ticker": self.case.ticker,
                "fy": self.case.fy,
                "paraphrase_group": self.case.paraphrase_group,
                "origin": self.case.origin,
                "query_id": self.query_id,
                "latency_ms": round(self.latency_ms, 1),
                "cost_usd": round(self.cost_usd, 6),
            }
        )
        # Lazy: run_columns imports this module (docs/specs/eval-run-columns.md).
        from src.eval.core.run_columns import case_columns

        d.update(case_columns(self))
        return d


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass
class Metrics:
    """The aggregate scorecard for a set of case results."""

    n: int = 0
    n_correct: int = 0
    n_partial: int = 0
    n_incorrect: int = 0
    n_refused: int = 0
    n_hedged_miss: int = 0
    n_error: int = 0
    # Stated less precisely than the key's rule but right at its own precision: neither
    # correct nor wrong (docs/specs/grading-precision.md requirement 8).
    n_imprecise: int = 0
    n_needs_review: int = 0
    # Wrong and undeclined whether cited or not: the failure metric every system arm
    # is held to alike (silent_wrong also needs a fact_id, which baselines never cite).
    n_confident_wrong: int = 0
    accuracy: float = 0.0
    # Right numbers regardless of citations and grounding: accuracy a baseline can earn.
    value_accuracy: float = 0.0
    citation_rate: float = 0.0
    grounding_precision: float | None = None
    grounding_recall: float | None = None
    silent_wrong_rate: float = 0.0
    confident_wrong_rate: float = 0.0
    refusal_rate: float = 0.0
    hedged_miss_rate: float = 0.0
    imprecise_rate: float = 0.0
    # Every way the system declined: an outright refusal or a hedged miss. The honest
    # counterweight to accuracy — refusal_rate alone hides the hedged declines.
    decline_rate: float = 0.0
    error_rate: float = 0.0
    total_cost_usd: float = 0.0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    grader_version: str = field(default_factory=grader_version)

    @classmethod
    def compute(cls, results: list[CaseResult]) -> Metrics:
        n = len(results)
        if n == 0:
            return cls()
        by_grade = {g: 0 for g in Grade}
        for r in results:
            by_grade[r.grade.grade] += 1

        # Grounding is averaged only over cases that actually assert it; cases with
        # no expected fact_ids report None and must not drag the mean toward zero.
        precisions = [
            r.grade.grounding_precision
            for r in results
            if r.grade.grounding_precision is not None
        ]
        recalls = [
            r.grade.grounding_recall
            for r in results
            if r.grade.grounding_recall is not None
        ]
        latencies = [r.latency_ms for r in results if r.latency_ms]
        n_confident_wrong = sum(1 for r in results if r.grade.confident_wrong)

        return cls(
            n=n,
            n_correct=by_grade[Grade.CORRECT],
            n_partial=by_grade[Grade.PARTIAL],
            n_incorrect=by_grade[Grade.INCORRECT],
            n_refused=by_grade[Grade.REFUSED],
            n_hedged_miss=by_grade[Grade.HEDGED_MISS],
            n_error=by_grade[Grade.ERROR],
            n_imprecise=by_grade[Grade.IMPRECISE],
            n_needs_review=sum(1 for r in results if r.grade.needs_review),
            n_confident_wrong=n_confident_wrong,
            accuracy=_rate(by_grade[Grade.CORRECT], n),
            value_accuracy=_rate(sum(1 for r in results if r.grade.value_correct), n),
            citation_rate=_rate(sum(1 for r in results if r.grade.has_citation), n),
            grounding_precision=_mean(precisions),
            grounding_recall=_mean(recalls),
            silent_wrong_rate=_rate(sum(1 for r in results if r.grade.silent_wrong), n),
            confident_wrong_rate=_rate(n_confident_wrong, n),
            refusal_rate=_rate(by_grade[Grade.REFUSED], n),
            hedged_miss_rate=_rate(by_grade[Grade.HEDGED_MISS], n),
            imprecise_rate=_rate(by_grade[Grade.IMPRECISE], n),
            decline_rate=_rate(by_grade[Grade.REFUSED] + by_grade[Grade.HEDGED_MISS], n),
            error_rate=_rate(by_grade[Grade.ERROR], n),
            total_cost_usd=sum(r.cost_usd for r in results),
            latency_p50_ms=percentile(latencies, 0.50),
            latency_p95_ms=percentile(latencies, 0.95),
        )

    def to_dict(self) -> dict:
        def _r(v: float | None, places: int = 4) -> float | None:
            return None if v is None else round(v, places)

        return {
            "n": self.n,
            "n_correct": self.n_correct,
            "n_partial": self.n_partial,
            "n_incorrect": self.n_incorrect,
            "n_refused": self.n_refused,
            "n_hedged_miss": self.n_hedged_miss,
            "n_error": self.n_error,
            "n_imprecise": self.n_imprecise,
            "n_needs_review": self.n_needs_review,
            "n_confident_wrong": self.n_confident_wrong,
            "accuracy": _r(self.accuracy),
            "value_accuracy": _r(self.value_accuracy),
            "citation_rate": _r(self.citation_rate),
            "grounding_precision": _r(self.grounding_precision),
            "grounding_recall": _r(self.grounding_recall),
            "silent_wrong_rate": _r(self.silent_wrong_rate),
            "confident_wrong_rate": _r(self.confident_wrong_rate),
            "refusal_rate": _r(self.refusal_rate),
            "hedged_miss_rate": _r(self.hedged_miss_rate),
            "imprecise_rate": _r(self.imprecise_rate),
            "decline_rate": _r(self.decline_rate),
            "error_rate": _r(self.error_rate),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "latency_p50_ms": _r(self.latency_p50_ms, 1),
            "latency_p95_ms": _r(self.latency_p95_ms, 1),
            "grader_version": self.grader_version,
        }


def paraphrase_stability(results: list[CaseResult]) -> float | None:
    """Fraction of paraphrase groups whose members all reached the same verdict.

    ``None`` when no case carries a ``paraphrase_group`` (the WS-2 golden set has
    none), so the metric is absent rather than falsely 1.0. Single-member groups are
    excluded — they cannot disagree and would inflate the score.
    """
    groups: dict[str, list[bool]] = {}
    for r in results:
        if r.case.paraphrase_group:
            groups.setdefault(r.case.paraphrase_group, []).append(r.passed)
    multi = [v for v in groups.values() if len(v) > 1]
    if not multi:
        return None
    return _rate(sum(1 for v in multi if len(set(v)) == 1), len(multi))


def run_stability(results: list[CaseResult]) -> float | None:
    """The run's ``paraphrase_stability`` value: intent stability, else paraphrase groups.

    When intents are tagged the value is the per-intent stability (v2 plan §3.2); a
    legacy suite keeps the paraphrase-group value, so runs stay comparable within their
    own split (docs/specs/eval-run-columns.md, requirement 8).

    Args:
        results: The run's graded cases.

    Returns:
        The stability, or ``None`` when neither kind of group has two or more members.
    """
    # Lazy: run_columns imports this module.
    from src.eval.core.run_columns import intent_stability

    by_intent = intent_stability(results)
    return by_intent if by_intent is not None else paraphrase_stability(results)


@dataclass
class RunReport:
    """Everything one suite×system run produced — the payload persisted in Phase 2."""

    run_id: str
    suite: str
    suite_version: str = ""
    system: str = ""
    tier: str | None = None
    corpus_fingerprint: str | None = None
    git_sha: str | None = None
    started_at: str = field(default_factory=_utc_now)
    finished_at: str | None = None
    status: str = "RUNNING"  # RUNNING | OK | ABORTED | ERROR
    results: list[CaseResult] = field(default_factory=list)
    n_planned: int = 0  # cases selected, incl. any not attempted
    config: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    # ------------------------------------------------------------------
    @property
    def metrics(self) -> Metrics:
        return Metrics.compute(self.results)

    @property
    def n_skipped(self) -> int:
        """Cases selected but never attempted — a budget abort, not a failure. Always
        reported so a truncated run can never read as full coverage."""
        return max(0, self.n_planned - len(self.results))

    def by_kind(self) -> dict[str, Metrics]:
        buckets: dict[str, list[CaseResult]] = {}
        for r in self.results:
            buckets.setdefault(r.case.kind, []).append(r)
        return {k: Metrics.compute(v) for k, v in sorted(buckets.items())}

    def by_tag(self) -> dict[str, Metrics]:
        buckets: dict[str, list[CaseResult]] = {}
        for r in self.results:
            for tag in r.case.tags:
                buckets.setdefault(tag, []).append(r)
        return {k: Metrics.compute(v) for k, v in sorted(buckets.items())}

    def by_ticker(self) -> dict[str, Metrics]:
        buckets: dict[str, list[CaseResult]] = {}
        for r in self.results:
            if r.case.ticker:
                buckets.setdefault(r.case.ticker, []).append(r)
        return {k: Metrics.compute(v) for k, v in sorted(buckets.items())}

    def failures(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]

    def silent_wrongs(self) -> list[CaseResult]:
        """The release-gating set: confident, cited, wrong."""
        return [r for r in self.results if r.grade.silent_wrong]

    def unstable_groups(self) -> dict[str, list[CaseResult]]:
        """Paraphrase groups whose members disagree — routing non-determinism."""
        groups: dict[str, list[CaseResult]] = {}
        for r in self.results:
            if r.case.paraphrase_group:
                groups.setdefault(r.case.paraphrase_group, []).append(r)
        return {
            g: rs
            for g, rs in groups.items()
            if len(rs) > 1 and len({r.passed for r in rs}) > 1
        }

    def to_dict(self, *, include_results: bool = True) -> dict:
        # Lazy: run_columns imports this module (docs/specs/eval-run-columns.md).
        from src.eval.core.run_columns import run_columns, slice_report

        slices = slice_report(self.results)
        d: dict[str, Any] = {
            "run_id": self.run_id,
            "suite": self.suite,
            "suite_version": self.suite_version,
            "system": self.system,
            "tier": self.tier,
            "corpus_fingerprint": self.corpus_fingerprint,
            "git_sha": self.git_sha,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "grader_version": grader_version(),
            "n_planned": self.n_planned,
            "n_skipped": self.n_skipped,
            "metrics": self.metrics.to_dict(),
            "paraphrase_stability": run_stability(self.results),
            "by_kind": {k: m.to_dict() for k, m in self.by_kind().items()},
            "by_tag": {k: m.to_dict() for k, m in self.by_tag().items()},
            **run_columns(self),
            "slices": slices,
            "config": self.config,
            "notes": self.notes,
        }
        if include_results:
            d["results"] = [r.to_dict() for r in self.results]
        return d


@dataclass
class RunComparison:
    """A vs B, for the regression-diff view. Regressions are listed first."""

    run_a: RunReport
    run_b: RunReport
    regressions: list[str] = field(default_factory=list)  # passed in A, failed in B
    improvements: list[str] = field(default_factory=list)  # failed in A, passed in B
    only_in_a: list[str] = field(default_factory=list)
    only_in_b: list[str] = field(default_factory=list)
    fingerprint_mismatch: bool = False

    @property
    def accuracy_delta(self) -> float:
        return self.run_b.metrics.accuracy - self.run_a.metrics.accuracy

    def to_dict(self) -> dict:
        return {
            "run_a": self.run_a.to_dict(include_results=False),
            "run_b": self.run_b.to_dict(include_results=False),
            "accuracy_delta": round(self.accuracy_delta, 4),
            "regressions": self.regressions,
            "improvements": self.improvements,
            "only_in_a": self.only_in_a,
            "only_in_b": self.only_in_b,
            "fingerprint_mismatch": self.fingerprint_mismatch,
        }


def compare_runs(run_a: RunReport, run_b: RunReport) -> RunComparison:
    """Diff two runs case-by-case.

    ``fingerprint_mismatch`` flags a comparison across different corpus states — the
    numbers are not commensurable and the UI must say so rather than render a
    misleading delta.
    """
    a = {r.case.id: r.passed for r in run_a.results}
    b = {r.case.id: r.passed for r in run_b.results}
    shared = set(a) & set(b)
    return RunComparison(
        run_a=run_a,
        run_b=run_b,
        regressions=sorted(cid for cid in shared if a[cid] and not b[cid]),
        improvements=sorted(cid for cid in shared if not a[cid] and b[cid]),
        only_in_a=sorted(set(a) - set(b)),
        only_in_b=sorted(set(b) - set(a)),
        fingerprint_mismatch=(run_a.corpus_fingerprint != run_b.corpus_fingerprint),
    )
