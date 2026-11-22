"""Summarise repeated eval runs: mean ± spread, floor/ceiling and the flaky cases.

Each source is a run id stored in the ``eval`` schema or a JSON report written by
``run_suite.py --out``. Every run is re-graded against the current suite first (as in
``compare_runs.py``), and only cases present in every run are counted
(docs/specs/repeat-spread.md):

    # three repeats of a slice, plus the stored full run on the same code
    conda run -n py312 python scripts/eval/summarize_repeats.py --runs `
        eval-d2884b8dd7c1 $env:TEMP\rep1.json $env:TEMP\rep2.json $env:TEMP\rep3.json

When the runs cover only a slice, ``--stable N`` adds a full-suite equivalent: it counts
N cases outside the slice as passing in every run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_runs import DEFAULT_SUITE, load_rows, regrade

from src.eval.core.case import EvalSuite
from src.eval.core.repeats import summarize_repeats


def _print_summary(label: str, s: dict, indent: str = "") -> None:
    print(
        f"{indent}{label:12s} n={s['n_cases']:4d}  mean {s['mean']:6.1%} ± {s['std']:5.2%}"
        f"  floor {s['floor']:6.1%}  ceiling {s['ceiling']:6.1%}"
        f"  flaky {len(s['flaky_case_ids'])}"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Command-line arguments; ``None`` reads ``sys.argv``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", nargs="+", required=True, help="run ids or JSON reports")
    parser.add_argument("--suite", default=str(DEFAULT_SUITE), help="suite JSON to grade against")
    parser.add_argument(
        "--stable", type=int, default=0,
        help="cases outside the runs counted as always passing (full-suite equivalent)",
    )
    parser.add_argument("--out", default=None, help="write the summary JSON here")
    args = parser.parse_args(argv)

    suite = EvalSuite.load(args.suite)
    cases = {c.id: c for c in suite.cases}
    runs: list[dict[str, bool]] = []
    grades: dict[str, list[str]] = {}
    for source in args.runs:
        results, skipped = regrade(load_rows(source), cases)
        if skipped:
            print(f"{source}: {len(skipped)} case(s) not in the suite, skipped")
        runs.append({cid: r.passed for cid, r in results.items()})
        for cid, r in results.items():
            grades.setdefault(cid, []).append(r.grade.grade.value)

    try:
        summary = summarize_repeats(runs, {cid: c.kind for cid, c in cases.items()})
    except ValueError as exc:
        raise SystemExit(f"cannot summarise: {exc}") from exc

    print(f"\n{summary['n_runs']} runs, {summary['n_cases']} common cases")
    print("accuracy per run:", ", ".join(f"{a:.1%}" for a in summary["accuracies"]))
    _print_summary("overall", summary)
    for kind, sub in summary["by_kind"].items():
        _print_summary(kind, sub, indent="  ")
    if args.stable:
        total = args.stable + summary["n_cases"]
        full = [(args.stable + a * summary["n_cases"]) / total for a in summary["accuracies"]]
        mean = sum(full) / len(full)
        sd = (sum((x - mean) ** 2 for x in full) / (len(full) - 1)) ** 0.5
        print(
            f"full-suite equivalent ({total} cases): mean {mean:.1%} ± {sd:.2%}, floor "
            f"{(args.stable + summary['floor'] * summary['n_cases']) / total:.1%}, ceiling "
            f"{(args.stable + summary['ceiling'] * summary['n_cases']) / total:.1%}"
        )
    print("\nflaky cases (grade per run):")
    for cid in summary["flaky_case_ids"]:
        print(f"  {'/'.join(g[:4] for g in grades[cid])}  {cid}")

    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
