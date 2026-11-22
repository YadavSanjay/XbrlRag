# Re-grade a stored eval run against a rebuilt suite, from its stored answers, and record
# where the new run came from. No system, judge or network is called.
# Spec: docs/specs/eval-regrade.md.

from __future__ import annotations

import copy
import dataclasses
import datetime
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from src.eval.core.answer_parsing import extract_stated_numbers, parse_structured_answer
from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.base import RunStore
from src.eval.core.checkpoint.run import suite_hash
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.honesty import grader_version
from src.eval.core.judge_wiring import is_text_graded
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.run_columns import JUDGE_COLUMNS, LEGACY_SPLIT, MixedSplitError, run_split
from src.eval.core.system import SystemAnswer
from src.observability.service_tiers import merge_counts

# The splits whose per-case grade changes are never listed (requirement 10).
HELDOUT_SPLITS = frozenset({"heldout_filers", "external", "temporal"})
# What a missing stored grade reads as in a transition.
NO_GRADE = "(none)"
# The baseline arms whose values are parse_structured_answer's (regrade-raw-values req. 2).
STRUCTURED_ARMS = frozenset({
    "closed_book",
    "naive_rag_filtered",
    "naive_rag_plain",
    "long_context_oracle",
    "long_context_resolved",
    "long_context_multi",
    "frontier_sec_tools",
})


class RegradeRefused(ValueError):  # noqa: N818 - the name the spec's interface fixes
    """A re-grade that cannot be shown to grade the stored answers to the same questions."""


def answer_from_payload(produced: Mapping[str, Any]) -> SystemAnswer:
    """Rebuilds the graded view of a stored answer from its ``produced`` payload.

    Args:
        produced: The ``produced`` dict a run stored for the case.

    Returns:
        The answer, as the graders see it.
    """
    return SystemAnswer(
        text=produced.get("text") or "",
        numeric_values=list(produced.get("numeric_values") or []),
        rows=[dict(row) for row in produced.get("rows") or []],
        fact_ids=list(produced.get("fact_ids") or []),
        chunk_ids=list(produced.get("chunk_ids") or []),
        refused=bool(produced.get("refused", False)),
        hedged=bool(produced.get("hedged", False)),
        error=produced.get("error"),
        selection=dict(produced.get("selection") or {}),
        agent=dict(produced.get("agent") or {}),
    )


def regrade_values(produced: Mapping[str, Any], system: str | None) -> list[float]:
    """The values a re-grade reads for a stored answer, before any grader filter.

    Spec: docs/specs/regrade-raw-values.md (requirement 2). The stored
    ``raw_numeric_values`` when present; else the values rebuilt from the stored text with
    the system's own parser; else the stored ``numeric_values``. Never mutates
    ``produced``.

    Args:
        produced: The ``produced`` dict a run stored for the case.
        system: The run's system name, or None.

    Returns:
        A new list of floats.
    """
    raw = produced.get("raw_numeric_values")
    if isinstance(raw, list):
        return [float(v) for v in raw]
    text = produced.get("text") or ""
    if text and system is not None:
        if system == "xbrlrag_full" or system.startswith("xbrlrag_"):
            return [float(n.value) for n in extract_stated_numbers(text)]
        if system in STRUCTURED_ARMS:
            return [float(v) for v in parse_structured_answer(text).numeric_values]
    return [float(v) for v in produced.get("numeric_values") or []]


def regrade_values_source(produced: Mapping[str, Any], system: str | None) -> str:
    """Which branch of ``regrade_values`` reads a stored answer: raw, rebuilt or stored."""
    if isinstance(produced.get("raw_numeric_values"), list):
        return "raw"
    if produced.get("text") and system is not None and (
        system.startswith("xbrlrag_") or system in STRUCTURED_ARMS
    ):
        return "rebuilt"
    return "stored"


def values_provenance(sources: Sequence[str]) -> str:
    """The re-grade's ``values`` provenance from each result's source (requirement 4)."""
    kinds = set(sources)
    if len(kinds) == 1:
        return next(iter(kinds))
    return "mixed"


def answer_for_regrade(produced: Mapping[str, Any], system: str | None) -> SystemAnswer:
    """The answer a re-grade grades: ``answer_from_payload`` with the re-grade's values.

    Spec: docs/specs/regrade-raw-values.md (requirement 3).

    Args:
        produced: The ``produced`` dict a run stored for the case.
        system: The run's system name, or None.

    Returns:
        ``answer_from_payload(produced)`` with ``numeric_values`` replaced by
        ``regrade_values(produced, system)``.
    """
    return dataclasses.replace(
        answer_from_payload(produced), numeric_values=regrade_values(produced, system)
    )


def _source_split(source: Mapping[str, Any]) -> str:
    """The source's split: its ``split``, else its config's, else ``legacy``."""
    split = source.get("split")
    if split:
        return str(split)
    config = source.get("config") or {}
    return str(config.get("split") or LEGACY_SPLIT)


def _judge_scores(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """A stored result's judge scores: ``judge_scores``, else the four judge columns."""
    if "judge_scores" in result:
        scores = result["judge_scores"]
        return dict(scores) if scores is not None else None
    columns = {key: result.get(column) for key, column in JUDGE_COLUMNS.items()}
    if all(value is None for value in columns.values()):
        return None
    return columns


def _stored_grade(result: Mapping[str, Any]) -> str | None:
    """A stored result's grade value, or None when it has none."""
    grade = result.get("grade")
    return None if grade is None else str(grade)


def _new_run_id(avoid: Any) -> str:
    """A fresh ``eval-`` plus 12 hex digits run id, never *avoid*."""
    while True:
        run_id = "eval-" + uuid.uuid4().hex[:12]
        if run_id != avoid:
            return run_id


def _match(results: Sequence[Mapping[str, Any]], cases: Sequence[EvalCase]) -> list[EvalCase]:
    """The suite's case for each result, in result order.

    Args:
        results: The source's results.
        cases: The rebuilt suite's cases.

    Returns:
        One case per result.

    Raises:
        RegradeRefused: On a repeated, unknown, payload-less or re-worded case, naming
            every offending case id.
    """
    by_id = {case.id: case for case in cases}
    seen: set[str] = set()
    repeated: list[str] = []
    unknown: list[str] = []
    no_payload: list[str] = []
    reworded: list[str] = []
    matched: list[EvalCase] = []
    for result in results:
        case_id = str(result.get("case_id"))
        if case_id in seen:
            repeated.append(case_id)
            continue
        seen.add(case_id)
        if not isinstance(result.get("produced"), Mapping):
            no_payload.append(case_id)
        case = by_id.get(case_id)
        if case is None:
            unknown.append(case_id)
            continue
        if result.get("question") != case.question:
            reworded.append(case_id)
        matched.append(case)
    problems = []
    if repeated:
        problems.append(f"case ids stored more than once: {sorted(set(repeated))}")
    if unknown:
        problems.append(f"cases not in the suite: {unknown}")
    if no_payload:
        problems.append(f"results without a produced payload: {no_payload}")
    if reworded:
        problems.append(f"questions that differ from the suite's: {reworded}")
    if problems:
        raise RegradeRefused("re-grade refused: " + "; ".join(problems))
    return matched


def regrade_report(
    source: Mapping[str, Any],
    cases: Sequence[EvalCase],
    *,
    suite: str,
    suite_version: str,
    corpus_fingerprint: str | None,
    run_id: str | None = None,
    regraded_at: str | None = None,
) -> RunReport:
    """Grades a stored run's answers against ``cases`` into a new run.

    Args:
        source: The stored run, as ``RunReport.to_dict()`` or ``load_source`` gives it.
        cases: The rebuilt suite's cases; matched to the results by id.
        suite: The rebuilt suite's name.
        suite_version: The rebuilt suite's version.
        corpus_fingerprint: The rebuilt suite's corpus fingerprint.
        run_id: The new run's id; minted when None.
        regraded_at: The time recorded in ``config["regrade"]``; now when None.

    Returns:
        The re-graded run, with its provenance in ``config["regrade"]``.

    Raises:
        RegradeRefused: When a result cannot be matched to the same question, the split
            differs, or the run id is the source's.
    """
    source_run_id = source.get("run_id")
    if run_id is not None and run_id == source_run_id:
        raise RegradeRefused(
            f"re-grade refused: run id {run_id!r} is the source run's; a re-grade is a new run"
        )
    results = list(source.get("results") or [])
    if not results:
        raise RegradeRefused(f"re-grade refused: run {source_run_id!r} has no results")
    matched = _match(results, cases)
    split = _source_split(source)
    try:
        case_split = run_split(matched)
    except MixedSplitError as exc:
        raise RegradeRefused(f"re-grade refused: the matched cases mix split: {exc}") from exc
    if case_split != split:
        raise RegradeRefused(
            f"re-grade refused: the run's split is {split!r} but the suite's cases are "
            f"split {case_split!r}"
        )

    regraded: list[CaseResult] = []
    stale: list[str] = []
    n_changed = 0
    system = source.get("system")
    system = str(system) if system is not None else None
    value_sources: list[str] = []
    for result, case in zip(results, matched, strict=True):
        value_sources.append(regrade_values_source(result["produced"], system))
        grade = grade_case(case, answer_for_regrade(result["produced"], system))
        scores = _judge_scores(result)
        if scores is not None:
            expected = result.get("expected") or {}
            if not is_text_graded(case) or case.expected_text != expected.get("text"):
                stale.append(case.id)
                scores = None
        grade.judge_scores = scores
        if grade.grade.value != _stored_grade(result):
            n_changed += 1
        served = result.get("served_models") or {}
        regraded.append(
            CaseResult(
                case=case,
                grade=grade,
                query_id=result.get("query_id"),
                latency_ms=float(result.get("latency_ms") or 0.0),
                cost_usd=float(result.get("cost_usd") or 0.0),
                served_models={model: list(ids) for model, ids in served.items()},
                service_tiers=merge_counts([result.get("service_tiers") or {}]),
                judge_error=result.get("judge_error"),
                judge_cost_usd=float(result.get("judge_cost_usd") or 0.0),
                judge_skipped=result.get("judge_skipped"),
            )
        )

    source_config = dict(source.get("config") or {})
    prior = source_config.get("regrade")
    prior = prior if isinstance(prior, Mapping) else {}
    identity = source_config.get("resume_identity")
    identity = identity if isinstance(identity, Mapping) else {}
    config = copy.deepcopy(source_config)
    config.pop("resume_identity", None)  # a re-graded run is never resumed
    config["split"] = split
    config["regrade"] = {
        "source_run_id": source_run_id,
        "root_run_id": prior.get("root_run_id") or source_run_id,
        "source_suite": source.get("suite"),
        "source_suite_version": source.get("suite_version"),
        "source_suite_hash": identity.get("suite_hash") or prior.get("suite_hash"),
        "source_grader_version": source.get("grader_version"),
        "suite_hash": suite_hash(matched),
        "grader_version": grader_version(),
        "regraded_at": regraded_at or datetime.datetime.now(datetime.UTC).isoformat(),
        "n_regraded": len(regraded),
        "n_grade_changed": n_changed,
        "judge_stale": sorted(stale),
        "judge_rerun": False,
        "values": values_provenance(value_sources),
    }
    n_planned = source.get("n_planned")
    return RunReport(
        run_id=run_id or _new_run_id(source_run_id),
        suite=suite,
        suite_version=suite_version,
        system=str(source.get("system") or ""),
        tier=source.get("tier"),
        corpus_fingerprint=corpus_fingerprint,
        git_sha=source.get("git_sha"),
        started_at=str(source.get("started_at") or ""),
        finished_at=source.get("finished_at"),
        status=str(source.get("status") or ""),
        results=regraded,
        n_planned=int(n_planned) if n_planned is not None else len(regraded),
        config=config,
        notes=f"regrade of {source_run_id}",
    )


def load_source(store: RunStore, run_id: str) -> dict[str, Any]:
    """Builds a re-grade source from a stored run.

    Args:
        store: The run store.
        run_id: The stored run.

    Returns:
        The source, in the form ``regrade_report`` reads.

    Raises:
        RegradeRefused: An unknown run, a ``RUNNING`` run, or one without payloads.
    """
    row = store.load_run_row(run_id)
    if row is None:
        raise RegradeRefused(f"no stored run {run_id!r}")
    if row.get("status") == "RUNNING":
        raise RegradeRefused(
            f"run {run_id!r} is RUNNING (in progress or crashed): resume it, do not re-grade it"
        )
    payloads = store.load_case_payloads(run_id)
    if not payloads:
        raise RegradeRefused(
            f"run {run_id!r} has no stored result_payload rows (stored before checkpointing); "
            "re-grade its --out JSON report instead"
        )
    raw_config = row.get("config") or {}
    config = dict(json.loads(raw_config) if isinstance(raw_config, str) else raw_config)
    n_planned = row.get("n_planned")
    return {
        "run_id": row.get("run_id", run_id),
        "suite": row.get("suite"),
        "suite_version": row.get("suite_version"),
        "system": row.get("system"),
        "tier": row.get("tier"),
        "corpus_fingerprint": row.get("corpus_fingerprint"),
        "git_sha": row.get("git_sha"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "status": row.get("status"),
        "n_planned": n_planned if n_planned is not None else len(payloads),
        "split": row.get("split") or config.get("split") or LEGACY_SPLIT,
        "grader_version": row.get("grader_version"),
        "config": config,
        "results": payloads,
    }


def regrade_summary(source: Mapping[str, Any], regraded: RunReport) -> dict[str, Any]:
    """How the grades moved between a stored run and its re-grade.

    Args:
        source: The stored run.
        regraded: Its re-grade.

    Returns:
        ``split``, ``n``, ``transitions`` and ``changed`` (``None`` for a held-out split).
    """
    split = str(
        (regraded.config or {}).get("split") or run_split(r.case for r in regraded.results)
    )
    before_by_id = {
        str(r.get("case_id")): _stored_grade(r) for r in source.get("results") or []
    }
    transitions: dict[str, int] = {}
    changed: list[dict[str, Any]] = []
    for result in regraded.results:
        stored = before_by_id.get(result.case.id)
        after = result.grade.grade.value
        if stored == after:
            continue
        before = stored if stored is not None else NO_GRADE
        key = f"{before} -> {after}"
        transitions[key] = transitions.get(key, 0) + 1
        changed.append(
            {"case_id": result.case.id, "kind": result.case.kind, "before": before,
             "after": after}
        )
    return {
        "split": split,
        "n": len(regraded.results),
        "transitions": transitions,
        "changed": None if split in HELDOUT_SPLITS else changed,
    }
