"""Prune a stored run's results to the cases ``regrade_run.py`` would accept against a suite.

``regrade_run.py`` refuses a whole run when any of its cases is missing from the rebuilt suite
or its question text changed (docs/specs/eval-regrade.md, requirement 3). After a suite rebuild
drops or re-words cases, this keeps only the results whose case id is in the suite with the
very same question, and writes the source back otherwise unchanged (every run-level key, and
every kept result byte-for-byte), so the pruned file is a valid ``--source`` for the re-grade:

    conda run -n py312 python scripts/eval/prune_regrade_source.py \\
        --source data/eval/baseline_runs/2026-11-04/dev.json \\
        --suite data/eval/v2_frozen/xbrl_v2_dev.json \\
        --out data/eval/regrade/dev_source_pruned.json --report dropped.json

The source is a ``run_suite.py --out`` JSON report, or a stored run id read (read-only) with
``load_source``. It refuses (exit 2) a ``RUNNING`` or empty run, and a source the re-grade would
still refuse after pruning (a repeated case id, a result without a ``produced`` payload). No
LLM is called and nothing is written to the database.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.case import EvalCase, EvalSuite  # noqa: E402
from src.eval.core.checkpoint.base import RunStore  # noqa: E402
from src.eval.core.regrade import RegradeRefused, _match, load_source  # noqa: E402

MISSING_FROM_SUITE = "missing_from_suite"
QUESTION_CHANGED = "question_changed"
REASONS = (MISSING_FROM_SUITE, QUESTION_CHANGED)


def _default_store() -> RunStore:
    """The production run store, built only when a stored run id is read."""
    from src.eval.core.store import PostgresEvalStore

    return PostgresEvalStore.from_env()


def read_source(source: str, store: Callable[[], RunStore]) -> dict[str, Any]:
    """Loads the source: a ``.json`` report that exists, else a stored run id (read-only).

    Args:
        source: A report path or a run id.
        store: Builds the run store; called only for a run id.

    Returns:
        The source run.

    Raises:
        RegradeRefused: When the file is not a run report or the stored run cannot be read.
    """
    path = Path(source)
    if path.suffix == ".json" and path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RegradeRefused(f"{source} is not a run report")
        return data
    return load_source(store(), source)


def prune_source(
    source: Mapping[str, Any], cases: Sequence[EvalCase]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Keeps the source results the re-grade would accept against ``cases``.

    A result is kept when its ``case_id`` is a suite case and its ``question`` equals that
    case's question exactly, the comparison ``regrade_report`` makes.

    Args:
        source: The stored run, as ``RunReport.to_dict()`` or ``load_source`` gives it.
        cases: The rebuilt suite's cases.

    Returns:
        The pruned source (a shallow copy with only ``results`` filtered; kept results are
        the source's own objects, in source order) and the dropped cases as
        ``{"case_id", "reason"}`` in source order.

    Raises:
        RegradeRefused: When the run is ``RUNNING`` or has no results, or when the kept
            results would still be refused (a repeated id or a missing payload).
    """
    run_id = source.get("run_id")
    if source.get("status") == "RUNNING":
        raise RegradeRefused(
            f"run {run_id!r} is RUNNING (in progress or crashed): resume it, do not prune it"
        )
    results = list(source.get("results") or [])
    if not results:
        raise RegradeRefused(f"run {run_id!r} has no results")
    by_id = {case.id: case for case in cases}
    kept: list[Any] = []
    dropped: list[dict[str, str]] = []
    for result in results:
        case_id = str(result.get("case_id"))
        case = by_id.get(case_id)
        if case is None:
            dropped.append({"case_id": case_id, "reason": MISSING_FROM_SUITE})
        elif result.get("question") != case.question:
            dropped.append({"case_id": case_id, "reason": QUESTION_CHANGED})
        else:
            kept.append(result)
    if not kept:
        raise RegradeRefused(f"run {run_id!r}: no result matches the suite; nothing to keep")
    _match(kept, cases)  # the re-grade's own acceptance check; raises on what pruning keeps
    pruned = dict(source)
    pruned["results"] = kept
    return pruned, dropped


def reason_counts(dropped: Sequence[Mapping[str, str]]) -> dict[str, int]:
    """The number of dropped cases per reason, every reason listed."""
    return {reason: sum(1 for d in dropped if d["reason"] == reason) for reason in REASONS}


def main(argv: list[str] | None = None, *, store: Callable[[], RunStore] | None = None) -> int:
    """Runs the CLI.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when None.
        store: Builds the run store for a run-id source; Postgres when None.

    Returns:
        0 on success, 2 when the source is refused.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--source", required=True, help="run_suite --out JSON or stored run id")
    parser.add_argument("--suite", required=True, help="the rebuilt suite JSON")
    parser.add_argument("--out", required=True, help="where to write the pruned source JSON")
    parser.add_argument("--report", default=None, help="write the dropped cases here")
    args = parser.parse_args(argv)

    suite = EvalSuite.load(args.suite)
    try:
        source = read_source(args.source, store or _default_store)
        pruned, dropped = prune_source(source, suite.cases)
    except RegradeRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    counts = reason_counts(dropped)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pruned, indent=2, default=str), encoding="utf-8")
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {"source_run_id": source.get("run_id"), "suite": suite.qualified_name,
                 "n_source": len(source["results"]), "n_kept": len(pruned["results"]),
                 "counts": counts, "dropped": dropped},
                indent=2,
            ),
            encoding="utf-8",
        )
    print(f"pruned {source.get('run_id')} against {suite.qualified_name}: "
          f"kept {len(pruned['results'])} of {len(source['results'])}, dropped {len(dropped)} "
          f"({', '.join(f'{r} {n}' for r, n in counts.items())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
