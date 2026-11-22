"""List dev cases where two or more arms agree with each other and not with the answer key.

A triage aid for a human: when independent arms land on the same number and the key says
otherwise, check the key first (that is how the wrong P&G shares-outstanding key was found).
It grades nothing and changes no key. Each source is an arm's run id in the ``eval`` schema
or its ``run_suite.py --out`` JSON report; keys, tolerances and concepts come from the dev
suite. Dev only: a run whose recorded split is anything else (H-F, H-X, set B, H-T,
validation, legacy) is refused before its cases are read. No LLM or network call.

    conda run -n py312 python scripts/eval/key_consensus_audit.py `
        --suite data/eval/v2_frozen/xbrl_v2_dev.json `
        --runs eval-9a99afc97d4b data/eval/anthropic_smoke/2026-11-05/closed_book.json `
        --out $env:TEMP\\key_audit.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.case import EvalSuite  # noqa: E402
from src.eval.core.key_audit import (  # noqa: E402
    AuditRefused,
    find_consensus,
    format_report,
    load_arm_run,
)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Command-line arguments; ``None`` reads ``sys.argv``.

    Returns:
        The process exit code: 0, or 2 when the audit is refused.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", nargs="+", required=True,
                        help="arm run ids or JSON reports, one per system")
    parser.add_argument("--suite", required=True, help="the dev suite JSON (keys, concepts)")
    parser.add_argument("--min-arms", type=int, default=2,
                        help="how many arms must agree (default 2)")
    parser.add_argument("--rel-tol", type=float, default=None,
                        help="override every key's tolerance (default: the key's own)")
    parser.add_argument("--out", default=None, help="write the findings JSON here")
    args = parser.parse_args(argv)

    try:
        # Every run's split is checked before its cases, and before the suite is read.
        runs = [load_arm_run(source) for source in args.runs]
        cases = {c.id: c for c in EvalSuite.load(args.suite).cases}
        report = find_consensus(runs, cases, min_arms=args.min_arms, rel_tol=args.rel_tol)
    except AuditRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    print(format_report(report))
    if args.out:
        Path(args.out).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
