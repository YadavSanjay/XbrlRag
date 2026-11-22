"""What v2 slices every eval run and case by: split, intent, phrasing, key, judge.

The values come from ``EvalCase.tags`` (``split:<s>``, ``intent:<id>``, ``family:<f>``,
``template:<id>``, ``key_source:<src>``, ``key_status:<st>``) and from the judge's
``CaseGrade.judge_scores``, so the suite generator, the key builders and the judge can
produce them without importing this module. Spec: docs/specs/eval-run-columns.md.

A run has exactly one split: a mixed run is rejected before any case is answered, so a
held-out case can never leak, unlogged, into a dev run.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.eval.core.case import EvalCase
from src.eval.core.report import CaseResult, Metrics, RunReport

# The splits a run can belong to (docs/design/XbrlRag_v2_Plan.md §3.1).
SPLITS: tuple[str, ...] = (
    "dev",
    "validation",
    "heldout_filers",
    "external",
    "temporal",
    "legacy",
)
# The split of a case without a ``split:`` tag, and of an empty run.
LEGACY_SPLIT = "legacy"
# How an answer key was confirmed (§3.8).
KEY_STATUSES: tuple[str, ...] = ("verified", "single-source")

# Tag prefix -> slice field, in the order the fields are reported.
TAG_PREFIXES: dict[str, str] = {
    "split": "split",
    "intent": "intent_id",
    "family": "phrasing_family",
    "template": "template_id",
    "key_source": "key_source",
    "key_status": "key_status",
}
SLICE_FIELDS: tuple[str, ...] = tuple(TAG_PREFIXES.values())

# Judge score key -> case column. The claim count is an int; the rest are in [0, 1].
JUDGE_COLUMNS: dict[str, str] = {
    "correctness": "judge_correctness",
    "completeness": "judge_completeness",
    "citation_support": "judge_citation_support",
    "unsupported_claims": "judge_unsupported_claims",
}
_CLAIMS_KEY = "unsupported_claims"

# The bucket key of results whose slice field is untagged.
NONE_BUCKET = "(none)"


class MixedSplitError(ValueError):
    """A run's cases span more than one split (dev, validation, held-out, ...)."""


def slice_fields_from_tags(tags: Iterable[str]) -> dict[str, str | None]:
    """Reads the six slice fields from a case's tags.

    Args:
        tags: The case's tags; ``split:``, ``intent:``, ``family:``, ``template:``,
            ``key_source:`` and ``key_status:`` are read, others ignored.

    Returns:
        ``split``, ``intent_id``, ``phrasing_family``, ``template_id``, ``key_source``
        and ``key_status``, each ``None`` when untagged.

    Raises:
        ValueError: On an empty value, two different values for one prefix, an unknown
            split or an unknown key status.
    """
    out: dict[str, str | None] = dict.fromkeys(SLICE_FIELDS)
    for tag in tags:
        prefix, sep, value = tag.partition(":")
        if not sep or prefix not in TAG_PREFIXES:
            continue
        if not value:
            raise ValueError(f"tag {tag!r} has an empty value")
        name = TAG_PREFIXES[prefix]
        current = out[name]
        if current is not None and current != value:
            raise ValueError(
                f"conflicting {prefix}: tags: {current!r} and {value!r}"
            )
        out[name] = value
    if out["split"] is not None and out["split"] not in SPLITS:
        raise ValueError(f"unknown split {out['split']!r}; expected one of {SPLITS}")
    if out["key_status"] is not None and out["key_status"] not in KEY_STATUSES:
        raise ValueError(
            f"unknown key_status {out['key_status']!r}; expected one of {KEY_STATUSES}"
        )
    return out


def _effective_split(case: EvalCase) -> str:
    """The case's ``split:`` tag, or ``legacy`` when it has none."""
    return slice_fields_from_tags(case.tags)["split"] or LEGACY_SPLIT


def run_split(cases: Iterable[EvalCase]) -> str:
    """The single split a run's cases belong to (``legacy`` when untagged or empty).

    Args:
        cases: The run's cases; every case's tags are parsed.

    Returns:
        The one effective split all cases share.

    Raises:
        MixedSplitError: When the cases' effective splits differ.
        ValueError: When a case's tags are malformed.
    """
    splits = {_effective_split(case) for case in cases}
    if len(splits) > 1:
        raise MixedSplitError(
            f"a run must have one split, got {sorted(splits)}; narrow it with "
            "--tag split:<s>"
        )
    return splits.pop() if splits else LEGACY_SPLIT


def _fraction(key: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"judge score {key!r} must be a number, got {value!r}")
    as_float = float(value)
    if math.isnan(as_float) or not 0.0 <= as_float <= 1.0:
        raise ValueError(f"judge score {key!r} must be in [0, 1], got {value!r}")
    return as_float


def _claim_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"unsupported_claims must be a count, got {value!r}")
    as_float = float(value)
    if math.isnan(as_float) or as_float < 0 or not as_float.is_integer():
        raise ValueError(f"unsupported_claims must be an integer >= 0, got {value!r}")
    return int(as_float)


def judge_columns(scores: Mapping[str, Any] | None) -> dict[str, float | int | None]:
    """Maps the judge's sub-scores to the four ``judge_*`` case columns.

    Args:
        scores: The judge's scores keyed ``correctness``, ``completeness``,
            ``citation_support`` and ``unsupported_claims``, or ``None`` when no judge
            ran. A missing key (or a ``None`` value) gives a ``None`` column.

    Returns:
        The four columns; the claim count as an ``int``.

    Raises:
        ValueError: On an out-of-range or NaN score, a non-integral or negative claim
            count, or an unknown key.
    """
    out: dict[str, float | int | None] = dict.fromkeys(JUDGE_COLUMNS.values())
    if scores is None:
        return out
    unknown = sorted(set(scores) - set(JUDGE_COLUMNS))
    if unknown:
        raise ValueError(f"unknown judge score key(s) {unknown}")
    for key, column in JUDGE_COLUMNS.items():
        value = scores.get(key)
        if value is None:
            continue
        out[column] = _claim_count(value) if key == _CLAIMS_KEY else _fraction(key, value)
    return out


def case_columns(result: CaseResult) -> dict[str, Any]:
    """The ten new ``eval_case_result`` columns of one graded case.

    Args:
        result: The graded case; its grade's ``judge_scores`` may be absent.

    Returns:
        The six tag fields followed by the four judge columns.
    """
    scores = getattr(result.grade, "judge_scores", None)
    return {**slice_fields_from_tags(result.case.tags), **judge_columns(scores)}


def run_columns(report: RunReport) -> dict[str, Any]:
    """The new ``eval_run`` columns: ``split`` and ``judge_version``.

    Args:
        report: The run; ``config["split"]`` and ``config["judge_version"]`` are read
            when present.

    Returns:
        ``{"split": ..., "judge_version": ...}``.

    Raises:
        ValueError: When ``config["split"]`` is not a known split.
        MixedSplitError: When it disagrees with the results' split.
    """
    config = report.config or {}
    if "split" in config:
        split = config["split"]
        if split not in SPLITS:
            raise ValueError(f"unknown run split {split!r}; expected one of {SPLITS}")
        if report.results:
            from_results = run_split(r.case for r in report.results)
            if from_results != split:
                raise MixedSplitError(
                    f"run split {split!r} disagrees with its cases' split "
                    f"{from_results!r}"
                )
    else:
        split = run_split(r.case for r in report.results)
    return {"split": split, "judge_version": config.get("judge_version") or None}


def slice_metrics(results: Sequence[CaseResult], field: str) -> dict[str, Metrics]:
    """Metrics per value of one slice field; untagged results under ``"(none)"``.

    Args:
        results: The graded cases.
        field: One of the six slice fields (``split``, ``intent_id``, ...).

    Returns:
        ``{value: Metrics}``, keys sorted.

    Raises:
        ValueError: When *field* is not one of the six slice fields.
    """
    if field not in SLICE_FIELDS:
        raise ValueError(f"unknown slice field {field!r}; expected one of {SLICE_FIELDS}")
    buckets: dict[str, list[CaseResult]] = {}
    for r in results:
        key = slice_fields_from_tags(r.case.tags)[field] or NONE_BUCKET
        buckets.setdefault(key, []).append(r)
    return {k: Metrics.compute(v) for k, v in sorted(buckets.items())}


def intent_stability(results: Sequence[CaseResult]) -> float | None:
    """Fraction of multi-result intents whose results all have the same ``passed``.

    Args:
        results: The graded cases; those without an ``intent:`` tag are ignored.

    Returns:
        The stable share of intents with two or more results; ``None`` when there are
        none (never a vacuous 1.0).
    """
    groups: dict[str, list[bool]] = {}
    for r in results:
        intent = slice_fields_from_tags(r.case.tags)["intent_id"]
        if intent is not None:
            groups.setdefault(intent, []).append(r.passed)
    kept = [outcomes for outcomes in groups.values() if len(outcomes) >= 2]
    if not kept:
        return None
    return sum(1 for outcomes in kept if len(set(outcomes)) == 1) / len(kept)


def slice_report(results: Sequence[CaseResult]) -> dict[str, Any]:
    """Accuracy by split, phrasing family and key source, plus intent stability.

    Args:
        results: The graded cases.

    Returns:
        ``by_split``, ``by_phrasing_family`` and ``by_key_source`` (bucket key ->
        ``Metrics.to_dict()``) and ``intent_stability``.
    """

    def _by(field: str) -> dict[str, dict]:
        return {k: m.to_dict() for k, m in slice_metrics(results, field).items()}

    return {
        "by_split": _by("split"),
        "by_phrasing_family": _by("phrasing_family"),
        "by_key_source": _by("key_source"),
        "intent_stability": intent_stability(results),
    }
