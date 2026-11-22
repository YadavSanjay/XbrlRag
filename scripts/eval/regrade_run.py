"""Re-grade a stored eval run against a rebuilt suite and store the result as a new run.

The stored answers are graded again with today's graders against the suite given; no
system, judge, LLM or network is called. The new run records where it came from in
``config["regrade"]`` (source run, suite hashes, grader versions, judge scores dropped
because their reference text changed). Spec: docs/specs/eval-regrade.md.

The source is a run id stored in the ``eval`` schema (it must carry ``result_payload``) or
the JSON report written by ``run_suite.py --out``:

    conda run -n py312 python scripts/eval/regrade_run.py \\
        --source eval-9a99afc97d4b --suite data/eval/v2_frozen/xbrl_v2_dev.json

v2 grounding (docs/specs/v2-grounding.md) is attached to the re-graded results when the
source run's ``corpus_fingerprint`` equals the fact store's current one (fact ids are store
row ids); otherwise the grounding fields stay None and the reason is printed and recorded in
``config["grounding_v2"]``. ``--no-grounding`` skips it.

It refuses (exit 2) a run with a case missing from the suite, a changed question, a split
other than the suite's, or a run still ``RUNNING``. Held-out runs print aggregates only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.case import EvalSuite  # noqa: E402
from src.eval.core.checkpoint.base import RunStore  # noqa: E402
from src.eval.core.graders.grounding_v2 import ground_results  # noqa: E402
from src.eval.core.regrade import (  # noqa: E402
    RegradeRefused,
    load_source,
    regrade_report,
    regrade_summary,
)
from src.eval.core.report import Metrics  # noqa: E402
from src.eval.sec_check.base import FactStore  # noqa: E402


def _default_store() -> RunStore:
    """The production run store, built only when a stored run is read or written."""
    from src.eval.core.store import PostgresEvalStore

    return PostgresEvalStore.from_env()


def _default_fact_store() -> FactStore:
    """The fact store cited fact ids resolve against (read-only ``xbrl_db``)."""
    from src.eval.sec_check.pg_store import PostgresFactStore

    return PostgresFactStore()


def _default_fingerprint() -> str:
    """The fact store's current corpus fingerprint (``"unknown"`` when unreachable)."""
    from src.eval.core.fingerprint import compute_fingerprint

    return compute_fingerprint()


def read_source(source: str, store: Callable[[], RunStore]) -> dict[str, Any]:
    """Loads the source run: a ``.json`` report that exists, else a stored run id.

    Args:
        source: A report path or a run id.
        store: Builds the run store, called only for a run id.

    Returns:
        The source, in the form ``regrade_report`` reads.

    Raises:
        RegradeRefused: When the stored run cannot be re-graded.
    """
    path = Path(source)
    if path.suffix == ".json" and path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RegradeRefused(f"{source} is not a run report")
        return data
    return load_source(store(), source)


def stored_scorecard(source: Mapping[str, Any]) -> str:
    """One line of the source's stored grades, as the run graded them."""
    counts = Counter(str(r.get("grade")) for r in source.get("results") or [])
    n = sum(counts.values())
    correct = counts.get("CORRECT", 0)
    accuracy = correct / n if n else 0.0
    return (
        f"accuracy {accuracy:6.1%}  ({correct}/{n})  refused {counts.get('REFUSED', 0)}  "
        f"hedged-miss {counts.get('HEDGED_MISS', 0)}  partial {counts.get('PARTIAL', 0)}  "
        f"incorrect {counts.get('INCORRECT', 0)}  error {counts.get('ERROR', 0)}"
    )


def _pct(value: float | None) -> str:
    """A rate as a percentage, or ``n/a`` for None."""
    return "n/a" if value is None else f"{value:.1%}"


def scorecard(metrics: Metrics) -> str:
    """One line of a re-graded run's scorecard."""
    return (
        f"accuracy {metrics.accuracy:6.1%}  ({metrics.n_correct}/{metrics.n})  "
        f"refused {metrics.n_refused}  hedged-miss {metrics.n_hedged_miss}  "
        f"partial {metrics.n_partial}  incorrect {metrics.n_incorrect}  "
        f"error {metrics.n_error}  silent-wrong {metrics.silent_wrong_rate:5.1%}"
    )


def main(
    argv: list[str] | None = None,
    *,
    store: Callable[[], RunStore] | None = None,
    fact_store: Callable[[], FactStore] | None = None,
    fingerprint: Callable[[], str] | None = None,
) -> int:
    """Runs the CLI.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when None.
        store: Builds the run store (the database boundary); Postgres when None.
        fact_store: Builds the fact store v2 grounding resolves cited ids in; Postgres
            when None.
        fingerprint: Reads the fact store's corpus fingerprint; Postgres when None.

    Returns:
        0 on success, 2 on a refused re-grade, 1 when storing was asked for and failed.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--source", required=True, help="stored run id or run_suite --out JSON")
    parser.add_argument("--suite", required=True, help="the rebuilt suite JSON to grade against")
    parser.add_argument("--run-id", default=None, help="the new run's id (minted when omitted)")
    parser.add_argument("--out", default=None, help="also write the re-graded report here")
    parser.add_argument("--no-store", action="store_true", help="do not persist the new run")
    parser.add_argument("--no-grounding", action="store_true",
                        help="do not attach v2 grounding (no fact store read)")
    parser.add_argument(
        "--answered-on-corpus", default=None, metavar="FINGERPRINT",
        help=("for a source run that recorded no corpus_fingerprint: the operator's "
              "statement of the corpus it answered on; grounding is attached only when it "
              "equals the fact store's. Ignored when the run recorded one."))
    args = parser.parse_args(argv)
    make_store = store or _default_store

    suite = EvalSuite.load(args.suite)
    try:
        source = read_source(args.source, make_store)
        report = regrade_report(
            source, suite.cases, suite=suite.name, suite_version=suite.version,
            corpus_fingerprint=suite.corpus_fingerprint, run_id=args.run_id,
        )
    except RegradeRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    summary = regrade_summary(source, report)
    provenance = report.config["regrade"]
    grounding = None
    if not args.no_grounding:
        # Fact ids are store row ids: resolve them only on the corpus the run answered on.
        recorded = source.get("corpus_fingerprint")
        asserted = args.answered_on_corpus if not recorded else None
        grounding = ground_results(
            report.results, fact_store or _default_fact_store,
            run_fingerprint=recorded or asserted,
            store_fingerprint=fingerprint or _default_fingerprint,
        )
        if grounding["status"] != "not_applicable":
            if asserted is not None:
                grounding["answered_on_corpus_asserted"] = asserted
            report.config["grounding_v2"] = grounding

    print(f"re-grade of {source.get('run_id')} ({summary['split']}) against "
          f"{suite.qualified_name} -> {report.run_id}")
    print(f"  stored     {stored_scorecard(source)}")
    print(f"  re-graded  {scorecard(report.metrics)}")
    print(f"  {provenance['n_grade_changed']} of {summary['n']} grade(s) changed; "
          f"judge scores dropped as stale: {len(provenance['judge_stale'])}")
    if grounding is not None and grounding["status"] == "attached":
        m = report.metrics
        print(f"  v2 grounding: {grounding['n_cases']} case(s), "
              f"{grounding['n_citations']} citation(s), "
              f"{grounding['n_unresolved']} unresolved fact id(s); "
              f"precision {_pct(m.grounding_precision)}  recall {_pct(m.grounding_recall)}")
    elif grounding is not None and grounding["status"] != "not_applicable":
        print(f"  v2 grounding NOT attached ({grounding['status']}): {grounding['reason']}")
    for transition, n in sorted(summary["transitions"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {transition}")
    if summary["changed"] is None:
        print("  (held-out split: per-case changes are not shown)")
    else:
        for change in summary["changed"]:
            print(f"  {change['before']:>11} -> {change['after']:<11} "
                  f"{change['kind']:<10} {change['case_id']}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
        print(f"wrote {out}")
    if args.no_store:
        return 0
    if not make_store().finish_run(report):
        print(f"failed to store re-graded run {report.run_id}", file=sys.stderr)
        return 1
    print(f"stored run {report.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
