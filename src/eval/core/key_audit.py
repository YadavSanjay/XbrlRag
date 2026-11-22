"""Arm-consensus key audit: cases where several arms agree with each other but not the key.

When two or more independent systems (arms) answer a scalar case with the same number and
that number is not the answer key, the key is the first suspect: that is how the wrong P&G
shares-outstanding key was found. This module lists those cases for a human to triage. It
grades nothing and changes no key.

Dev only. Every run's recorded split is checked before any of its case content is read, and
a run of any other split (the held-out H-F, H-X, set B and H-T splits, validation, legacy)
is refused. No LLM, network or system call is made; the store is read with SELECT only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.eval.core.case import EvalCase
from src.eval.core.graders.numeric import close_enough, matches_any
from src.eval.core.run_columns import LEGACY_SPLIT, slice_fields_from_tags

# The only split the audit reads: keys may be inspected, and tuned, on dev alone.
AUDIT_SPLIT = "dev"
# A case's numbers are compared only when the arm asserted them: not refused, not hedged
# (a hedged miss shows the nearest facts it found, not an answer), and not an error.
_DEFAULT_MIN_ARMS = 2


class AuditRefused(ValueError):  # noqa: N818 - parallels RegradeRefused / ResumeRefused
    """The audit was asked to read something other than dev runs over a dev suite."""


@dataclass(frozen=True)
class ArmRun:
    """One arm's stored run: its identity, its recorded split and its per-case results."""

    run_id: str
    system: str
    split: str
    results: tuple[Mapping[str, Any], ...]


@dataclass
class Finding:
    """One case where at least ``min_arms`` arms agree on a value that is not the key."""

    case_id: str
    kind: str
    ticker: str | None
    fy: int | None
    concept: str | None
    unit: str | None
    key_value: float | None
    alt_values: list[float]
    rel_tol: float
    consensus_value: float
    consensus_arms: list[str]
    # Arm -> the values it asserted (None: refused, hedged, errored or gave no number).
    arm_values: dict[str, list[float] | None] = field(default_factory=dict)
    # Arms whose answer matched the key.
    key_arms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """The finding as a JSON-serialisable dict."""
        return asdict(self)


@dataclass
class AuditReport:
    """The audit's findings and what it compared or skipped."""

    runs: list[dict[str, str]]
    findings: list[Finding]
    n_compared: int
    skipped: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        """The report as a JSON-serialisable dict."""
        return {
            "runs": self.runs,
            "n_compared": self.n_compared,
            "n_findings": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "skipped": self.skipped,
        }


class RunReader(Protocol):
    """Reads stored runs: the run row first, the case rows only after the split check."""

    def run_row(self, run_id: str) -> Mapping[str, Any] | None:
        """The run's ``run_id``, ``system`` and ``split`` (or ``config``), or None."""
        ...

    def case_rows(self, run_id: str) -> Sequence[Mapping[str, Any]]:
        """The run's per-case rows (``case_id``, ``question``, ``kind``, ``produced``, ...)."""
        ...


def check_split(run_id: str, split: str | None) -> None:
    """Refuses any run whose recorded split is not dev.

    Args:
        run_id: The run, for the message.
        split: The run's recorded split; None reads as legacy.

    Raises:
        AuditRefused: When the split is not ``dev``.
    """
    effective = split or LEGACY_SPLIT
    if effective != AUDIT_SPLIT:
        raise AuditRefused(
            f"run {run_id} has split {effective!r}; the key audit reads only "
            f"{AUDIT_SPLIT!r} runs (held-out, validation and legacy runs are refused)"
        )


def _row_split(row: Mapping[str, Any]) -> str | None:
    config = row.get("config")
    if isinstance(config, str):
        config = json.loads(config)
    config_split = config.get("split") if isinstance(config, Mapping) else None
    return row.get("split") or config_split


def _check_result_splits(run_id: str, results: Iterable[Mapping[str, Any]]) -> None:
    """A second guard: no stored case may carry a split other than dev."""
    for r in results:
        tag_split = slice_fields_from_tags(r.get("tags") or [])["split"]
        for split in (r.get("split"), tag_split):
            if split is not None and split != AUDIT_SPLIT:
                raise AuditRefused(
                    f"run {run_id} holds a case of split {split!r}; the key audit reads "
                    f"only {AUDIT_SPLIT!r} cases"
                )


def arm_run_from_report(report: Mapping[str, Any]) -> ArmRun:
    """An arm run from a ``run_suite.py --out`` JSON report, its split checked first.

    Args:
        report: The report's parsed JSON.

    Returns:
        The arm run.

    Raises:
        AuditRefused: When the report's split is not dev.
    """
    run_id = str(report.get("run_id") or "?")
    check_split(run_id, _row_split(report))
    results = tuple(report.get("results") or [])
    _check_result_splits(run_id, results)
    return ArmRun(run_id=run_id, system=str(report.get("system") or run_id),
                  split=AUDIT_SPLIT, results=results)


def load_arm_run(source: str, reader: RunReader | None = None) -> ArmRun:
    """Loads an arm run from a JSON report path or a stored run id.

    A stored run's row is read and its split checked before any case row is read.

    Args:
        source: A ``.json`` report path, or a run id in the ``eval`` schema.
        reader: The store reader for a run id; the Postgres one when None.

    Returns:
        The arm run.

    Raises:
        AuditRefused: When the run is unknown or its split is not dev.
    """
    path = Path(source)
    if path.suffix == ".json" and path.exists():
        return arm_run_from_report(json.loads(path.read_text(encoding="utf-8")))
    reader = reader or PostgresRunReader()
    row = reader.run_row(source)
    if row is None:
        raise AuditRefused(f"unknown run {source!r}")
    check_split(source, _row_split(row))
    results = tuple(reader.case_rows(source))
    _check_result_splits(source, results)
    return ArmRun(run_id=source, system=str(row.get("system") or source),
                  split=AUDIT_SPLIT, results=results)


def check_suite(cases: Iterable[EvalCase]) -> None:
    """Refuses a suite holding any case whose split tag is not dev.

    Args:
        cases: The suite's cases.

    Raises:
        AuditRefused: On a case of another split, or one with no split tag.
    """
    for case in cases:
        split = slice_fields_from_tags(case.tags)["split"]
        if split != AUDIT_SPLIT:
            raise AuditRefused(
                f"suite case {case.id} has split {split or LEGACY_SPLIT!r}; the key audit "
                f"reads only a {AUDIT_SPLIT!r} suite"
            )


def asserted_values(result: Mapping[str, Any]) -> list[float] | None:
    """The numbers an arm asserted for a case, or None when it asserted none.

    Args:
        result: A stored per-case result with a ``produced`` payload.

    Returns:
        The values; None for a refusal, a hedge, an error or no number.
    """
    produced = result.get("produced") or {}
    if produced.get("error") or produced.get("refused") or produced.get("hedged"):
        return None
    values = [float(v) for v in produced.get("numeric_values") or []]
    return values or None


def agree(a: float, b: float, rel_tol: float) -> bool:
    """Whether two arms' values agree within the key's tolerance, in either direction."""
    return close_enough(a, b, rel_tol) or close_enough(b, a, rel_tol)


def _concept(case: EvalCase) -> str | None:
    if case.concept:
        return case.concept
    key = case.key if isinstance(case.key, Mapping) else {}
    concept = key.get("concept")
    return str(concept) if concept else None


def _best_cluster(
    disagreeing: Mapping[str, list[float]], rel_tol: float
) -> tuple[float, list[str]] | None:
    """The value most disagreeing arms share, and those arms; ties go to arm order."""
    best: tuple[float, list[str]] | None = None
    for arm, values in disagreeing.items():
        for v in values:
            support = [
                other for other, theirs in disagreeing.items()
                if other == arm or any(agree(v, w, rel_tol) for w in theirs)
            ]
            if best is None or len(support) > len(best[1]):
                best = (v, support)
    return best


def find_consensus(
    runs: Sequence[ArmRun],
    cases: Mapping[str, EvalCase],
    *,
    min_arms: int = _DEFAULT_MIN_ARMS,
    rel_tol: float | None = None,
) -> AuditReport:
    """Lists scalar cases where ``min_arms`` or more arms agree on a value that is not the key.

    Args:
        runs: The arms' dev runs; distinct systems.
        cases: The dev suite's cases by id: the keys, tolerances and concepts.
        min_arms: How many arms must agree.
        rel_tol: A tolerance overriding every case's own; None uses the key's.

    Returns:
        The findings, most-agreed first, and what was skipped and why.

    Raises:
        AuditRefused: On a run that is not dev, a suite case that is not dev, repeated
            systems, or ``min_arms`` below 2.
    """
    if min_arms < 2:
        raise AuditRefused("min_arms must be at least 2: one arm is not a consensus")
    for run in runs:
        check_split(run.run_id, run.split)
    systems = [r.system for r in runs]
    if len(set(systems)) != len(systems):
        raise AuditRefused(f"each arm must be a different system, got {systems}")
    check_suite(cases.values())

    by_case: dict[str, dict[str, Mapping[str, Any]]] = {}
    skipped: dict[str, list[str]] = {
        "not_in_suite": [], "question_changed": [], "not_scalar": [],
    }
    for run in runs:
        for r in run.results:
            case_id = str(r.get("case_id"))
            case = cases.get(case_id)
            if case is None:
                skipped["not_in_suite"].append(f"{run.system}:{case_id}")
                continue
            if r.get("question") is not None and r.get("question") != case.question:
                skipped["question_changed"].append(f"{run.system}:{case_id}")
                continue
            by_case.setdefault(case_id, {})[run.system] = r

    findings: list[Finding] = []
    n_compared = 0
    for case_id in sorted(by_case):
        case = cases[case_id]
        if case.expected_value is None or case.expect_not_meaningful:
            skipped["not_scalar"].append(case_id)
            continue
        arms = by_case[case_id]
        if len(arms) < min_arms:
            continue
        n_compared += 1
        tol = case.rel_tol if rel_tol is None else rel_tol
        targets = case.targets()
        arm_values = {arm: asserted_values(r) for arm, r in arms.items()}
        key_arms = [
            arm for arm, vals in arm_values.items()
            if vals and any(matches_any(v, targets, tol) for v in vals)
        ]
        disagreeing = {
            arm: vals for arm, vals in arm_values.items() if vals and arm not in key_arms
        }
        cluster = _best_cluster(disagreeing, tol)
        if cluster is None or len(cluster[1]) < min_arms:
            continue
        value, support = cluster
        findings.append(
            Finding(
                case_id=case_id,
                kind=case.kind,
                ticker=case.ticker,
                fy=case.fy,
                concept=_concept(case),
                unit=case.unit,
                key_value=case.expected_value,
                alt_values=list(case.alt_values),
                rel_tol=tol,
                consensus_value=value,
                consensus_arms=support,
                arm_values=arm_values,
                key_arms=key_arms,
            )
        )
    findings.sort(key=lambda f: (-len(f.consensus_arms), f.case_id))
    return AuditReport(
        runs=[{"run_id": r.run_id, "system": r.system, "split": r.split} for r in runs],
        findings=findings,
        n_compared=n_compared,
        skipped={k: sorted(v) for k, v in skipped.items()},
    )


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:,.6g}"


def format_report(report: AuditReport) -> str:
    """The report as a plain-text table for the terminal.

    Args:
        report: The audit report.

    Returns:
        One header line, one block per finding and the skip counts.
    """
    lines = [
        f"{len(report.findings)} case(s) where arms agree with each other and not the key "
        f"({report.n_compared} scalar case(s) compared across "
        f"{', '.join(r['system'] for r in report.runs)})"
    ]
    for f in report.findings:
        lines.append(
            f"\n{f.case_id}  [{f.kind}]  {f.ticker or '-'} FY{f.fy or '-'}  "
            f"concept={f.concept or '-'}  unit={f.unit or '-'}"
        )
        lines.append(
            f"  key {_fmt(f.key_value)} (rel_tol {f.rel_tol:g})  consensus "
            f"{_fmt(f.consensus_value)} from {', '.join(f.consensus_arms)}"
            + (f"; key matched by {', '.join(f.key_arms)}" if f.key_arms else "")
        )
        for arm, values in f.arm_values.items():
            shown = "no assertion" if values is None else ", ".join(_fmt(v) for v in values)
            lines.append(f"    {arm:<24} {shown}")
    counts = {k: len(v) for k, v in report.skipped.items() if v}
    if counts:
        lines.append(f"\nskipped: {counts}")
    return "\n".join(lines)


class PostgresRunReader:
    """Reads runs from the ``eval`` schema with SELECT only, in a read-only session."""

    _RUN = "SELECT run_id, system, split, config FROM eval.eval_run WHERE run_id = %s"
    _CASES = (
        "SELECT case_id, kind, question, ticker, fy, tags, expected, produced, grade "
        "FROM eval.eval_case_result WHERE run_id = %s ORDER BY case_id"
    )

    def _query(self, sql: str, run_id: str) -> list[dict[str, Any]]:
        from src.db.conn import connect  # lazy: the module imports without a DB driver

        conn = connect()
        try:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute(sql, (run_id,))
                columns = [d[0] for d in cur.description or ()]
                return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        finally:
            conn.close()

    def run_row(self, run_id: str) -> Mapping[str, Any] | None:
        """The run row, or None when the run is unknown."""
        rows = self._query(self._RUN, run_id)
        return rows[0] if rows else None

    def case_rows(self, run_id: str) -> Sequence[Mapping[str, Any]]:
        """The run's case rows, JSON columns decoded."""
        rows = self._query(self._CASES, run_id)
        for row in rows:
            for key in ("expected", "produced"):
                if isinstance(row.get(key), str):
                    row[key] = json.loads(row[key])
        return rows
