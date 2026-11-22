"""Compare two eval runs case by case, re-grading both with today's graders and suite.

A stored scorecard reflects the grader and ground truth *of the day it ran*. When the
graders change (grader_version) or a case's ground truth is corrected, comparing two
stored scorecards mixes those changes in with the system's. This script re-grades the
saved answers of both runs — no LLM calls, no pipeline — so the only difference left is
what the system answered.

Each source is either a run id stored in the ``eval`` schema or the JSON report written
by ``run_suite.py --out``:

    # Phase 1 vs the HEAD baseline, both re-graded under the current grader + suite
    conda run -n py312 python scripts/eval/compare_runs.py \
        --baseline eval-de7de6e12182 --candidate eval-ab63dd172611

    # also list how a slice moved (substring match on case id; repeatable)
    conda run -n py312 python scripts/eval/compare_runs.py \
        --baseline eval-de7de6e12182 --candidate eval-ab63dd172611 --show ticker=TSM

Cases missing from the current suite are skipped and counted. Read the result with the
usual caution: ~11% of cases flip grade between identical runs, so judge a change by the
cases it targeted and by what it lost, not by a small headline delta.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.case import EvalCase, EvalSuite  # noqa: E402
from src.eval.core.graders.dispatch import grade_case  # noqa: E402
from src.eval.core.regrade import answer_for_regrade  # noqa: E402
from src.eval.core.report import CaseResult, Metrics  # noqa: E402

DEFAULT_SUITE = REPO_ROOT / "test" / "eval" / "suites" / "sql_derived_v1.json"


def regrade(
    rows: Iterable[dict], cases: dict[str, EvalCase], system: str | None = None
) -> tuple[dict[str, CaseResult], list[str]]:
    """Grade each stored answer against the current case definition.

    The answer graded is ``answer_for_regrade``'s: the values the system produced (stored
    raw, else rebuilt from the stored text by the system's own parser), not the values an
    earlier grader already filtered (docs/specs/regrade-raw-values.md).

    Args:
        rows: Per-case records with ``case_id`` and ``produced``.
        cases: The current suite, by case id.
        system: The run's system name; None reads the stored values.

    Returns:
        Results by case id, and the case ids skipped because the suite no longer has
        them.
    """
    results: dict[str, CaseResult] = {}
    skipped: list[str] = []
    for row in rows:
        case = cases.get(row["case_id"])
        if case is None:
            skipped.append(row["case_id"])
            continue
        answer = answer_for_regrade(row.get("produced") or {}, system)
        results[case.id] = CaseResult.build(case, answer, grade_case(case, answer))
    return results, skipped


@dataclass
class Comparison:
    """How every case common to both runs moved between them."""

    transitions: Counter = field(default_factory=Counter)  # (before, after) -> count
    gained: list[str] = field(default_factory=list)  # not CORRECT -> CORRECT
    lost: list[str] = field(default_factory=list)  # CORRECT -> not CORRECT
    only_baseline: list[str] = field(default_factory=list)
    only_candidate: list[str] = field(default_factory=list)


def compare(
    baseline: dict[str, CaseResult], candidate: dict[str, CaseResult]
) -> Comparison:
    """Diff two re-graded runs case by case."""
    out = Comparison(
        only_baseline=sorted(set(baseline) - set(candidate)),
        only_candidate=sorted(set(candidate) - set(baseline)),
    )
    for case_id in sorted(set(baseline) & set(candidate)):
        before = baseline[case_id].grade.grade.value
        after = candidate[case_id].grade.grade.value
        if before == after:
            continue
        out.transitions[(before, after)] += 1
        if after == "CORRECT":
            out.gained.append(case_id)
        elif before == "CORRECT":
            out.lost.append(case_id)
    return out


def load_system(source: str) -> str | None:
    """The system name of a JSON report or a stored run id, or None when unknown."""
    path = Path(source)
    if path.suffix == ".json" and path.exists():
        system = json.loads(path.read_text(encoding="utf-8")).get("system")
    else:
        from src.eval.core.store import load_run

        system = (load_run(source) or {}).get("system")
    return str(system) if system else None


def load_rows(source: str) -> list[dict]:
    """Per-case ``{case_id, produced}`` records from a JSON report or a stored run id."""
    path = Path(source)
    if path.suffix == ".json" and path.exists():
        report = json.loads(path.read_text(encoding="utf-8"))
        rows = report.get("results") or []
    else:
        from src.eval.core.store import load_case_results

        rows = load_case_results(source, limit=100_000)
    if not rows:
        # src.api.db is fail-soft (an unreachable DB returns no rows), so say both.
        raise SystemExit(f"no case results for {source!r} (unknown run id, or DB unreachable)")
    return rows


def _scorecard(label: str, results: dict[str, CaseResult]) -> None:
    m = Metrics.compute(list(results.values()))
    print(
        f"  {label:10} accuracy {m.accuracy:6.1%}  ({m.n_correct}/{m.n})  "
        f"silent-wrong {m.silent_wrong_rate:5.1%}  refused {m.refusal_rate:5.1%}  "
        f"hedged-miss {m.hedged_miss_rate:5.1%}  partial {m.n_partial}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--baseline", required=True, help="run id or JSON report")
    parser.add_argument("--candidate", required=True, help="run id or JSON report")
    parser.add_argument("--suite", default=str(DEFAULT_SUITE), help="suite JSON to grade against")
    parser.add_argument(
        "--show", action="append", default=[],
        help="also list every case whose id contains this text (repeatable)",
    )
    args = parser.parse_args(argv)

    cases = {c.id: c for c in EvalSuite.load(args.suite).cases}
    base, base_skipped = regrade(load_rows(args.baseline), cases, load_system(args.baseline))
    cand, cand_skipped = regrade(
        load_rows(args.candidate), cases, load_system(args.candidate)
    )
    diff = compare(base, cand)
    kind = {cid: c.kind for cid, c in cases.items()}

    print(f"Re-graded against {Path(args.suite).name} with the current graders\n")
    _scorecard("baseline", base)
    _scorecard("candidate", cand)
    for label, skipped in (("baseline", base_skipped), ("candidate", cand_skipped)):
        if skipped:
            print(f"  ({label}: {len(skipped)} case(s) not in the suite, skipped)")
    if diff.only_baseline or diff.only_candidate:
        print(f"  (only in baseline: {len(diff.only_baseline)}, "
              f"only in candidate: {len(diff.only_candidate)})")

    print(f"\ngained {len(diff.gained)}  lost {len(diff.lost)}  "
          f"net {len(diff.gained) - len(diff.lost):+d}")
    print("  gained by kind:", dict(Counter(kind[c] for c in diff.gained)))
    print("  lost by kind:  ", dict(Counter(kind[c] for c in diff.lost)))
    print("\ntransitions:")
    for (before, after), n in diff.transitions.most_common():
        print(f"  {n:4d}  {before} -> {after}")
    if diff.lost:
        print("\nlost (CORRECT -> not):")
        for cid in diff.lost:
            print(f"  {cand[cid].grade.grade.value:<11} {cid}")

    for needle in args.show:
        ids = sorted(c for c in set(base) & set(cand) if needle in c)
        now = sum(cand[c].grade.grade.value == "CORRECT" for c in ids)
        was = sum(base[c].grade.grade.value == "CORRECT" for c in ids)
        print(f"\n{needle!r}: {now}/{len(ids)} correct (was {was})")
        for c in ids:
            print(f"  {base[c].grade.grade.value:>11} -> {cand[c].grade.grade.value:<11} {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
