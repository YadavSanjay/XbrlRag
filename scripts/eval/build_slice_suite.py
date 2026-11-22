r"""Build a slice of the SQL-derived suite for a cheap validation run.

A full run costs ~$2.50 and moves ~1 pt between identical runs, so fixes are validated on
slices (docs/design/XbrlRag_Evaluation.md §8):

- ``--working-set``: the cases that did not pass in every one of ``--runs`` (the cases that
  carry all the run-to-run movement).
- ``--target ID`` / ``--prefix PREFIX``: named cases, or every case whose id starts with a
  prefix (e.g. ``aq-3d``).
- ``--stable N``: plus a seeded sample of N cases that passed in every one of ``--runs``, as a
  regression guard.

The slice is written as a suite file for ``run_suite.py --suite-path``:

    conda run -n py312 python scripts/eval/build_slice_suite.py --out $env:TEMP\slice.json `
        --prefix aq-3d --stable 60
    conda run -n py312 python scripts/eval/run_suite.py --suite-path $env:TEMP\slice.json `
        --no-store --persist-audit --max-cost-usd 1 --out $env:TEMP\slice_out.json
    conda run -n py312 python scripts/eval/compare_runs.py --baseline eval-d2884b8dd7c1 `
        --candidate $env:TEMP\slice_out.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.db.conn import connect  # noqa: E402

DEFAULT_SUITE = REPO_ROOT / "test" / "eval" / "suites" / "sql_derived_v1.json"
DEFAULT_RUNS = ("eval-f2ae98c8358b", "eval-9a74fa9183cf", "eval-d2884b8dd7c1")


def _pass_sets(runs: list[str]) -> tuple[set[str], set[str]]:
    """Case ids passed in every run, and case ids present in every run but not always passed."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT case_id, bool_and(passed) FROM eval.eval_case_result"
            " WHERE run_id = ANY(%s) GROUP BY case_id HAVING count(*) = %s",
            (runs, len(runs)),
        )
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"no case is stored for all of {runs} (unknown run id?)")
    return {c for c, ok in rows if ok}, {c for c, ok in rows if not ok}


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Command-line arguments; ``None`` reads ``sys.argv``.

    Returns:
        The process exit code.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True, help="slice suite JSON to write")
    p.add_argument("--suite", default=str(DEFAULT_SUITE))
    p.add_argument("--runs", nargs="+", default=list(DEFAULT_RUNS),
                   help="stored runs that define stable / working-set cases")
    p.add_argument("--working-set", action="store_true",
                   help="include every case not passed in all --runs")
    p.add_argument("--target", action="append", default=[], help="a case id (repeatable)")
    p.add_argument("--prefix", action="append", default=[],
                   help="every case whose id starts with this (repeatable)")
    p.add_argument("--stable", type=int, default=0,
                   help="plus N cases passed in all --runs, sampled with --seed")
    p.add_argument("--seed", type=int, default=1729)
    args = p.parse_args(argv)

    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    ids = [c["id"] for c in suite["cases"]]
    missing = [t for t in args.target if t not in set(ids)]
    if missing:
        raise SystemExit(f"targets not in the suite: {missing}")

    keep = set(args.target)
    keep |= {i for i in ids if any(i.startswith(px) for px in args.prefix)}
    if args.working_set or args.stable:
        stable, unstable = _pass_sets(args.runs)
        if args.working_set:
            keep |= unstable & set(ids)
        if args.stable:
            pool = sorted((stable & set(ids)) - keep)
            keep |= set(random.Random(args.seed).sample(pool, min(args.stable, len(pool))))
    if not keep:
        raise SystemExit("empty slice: give --target, --prefix, --working-set or --stable")

    cases = [c for c in suite["cases"] if c["id"] in keep]
    suite.update(name=f"{suite['name']}_slice", n_cases=len(cases), cases=cases)
    Path(args.out).write_text(json.dumps(suite, indent=2), encoding="utf-8")
    kinds: dict[str, int] = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"wrote {len(cases)} cases to {args.out}: {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
