"""WS-9 / E2 — generate the SQL-derived ground-truth suite.

Runs every template in ``configs/eval/sql_derived_templates.yaml`` across the
corpus, labels each case with the value **and the fact_ids it came from**, and
writes a committed suite file.

    conda run -n py312 python scripts/eval/build_sql_derived_suite.py --tier standard
    conda run -n py312 python scripts/eval/build_sql_derived_suite.py --tier smoke --dry-run

Requires a populated Postgres ``xbrl_db``. Regenerate whenever the corpus changes —
the suite records the corpus fingerprint it was built against, and results are only
comparable within one fingerprint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from src.eval.suites.sql_derived import DEFAULT_SPEC_PATH, build  # noqa: E402

DEFAULT_OUT = os.path.join("test", "eval", "suites", "sql_derived_v1.json")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--tier", default="standard", choices=["smoke", "standard", "full"]
    )
    parser.add_argument("--spec", default=DEFAULT_SPEC_PATH)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and report, but do not write the suite file",
    )
    args = parser.parse_args(argv)

    suite, stats = build(spec_path=args.spec, tier=args.tier)

    print(f"\nSuite      : {suite.qualified_name}  (tier={args.tier})")
    print(f"Cases      : {len(suite)}")
    print(f"Fingerprint: {suite.corpus_fingerprint}")

    by_kind = {k: len(v) for k, v in sorted(suite.by_kind().items())}
    print(f"By kind    : {by_kind}")

    groups = {c.paraphrase_group for c in suite.cases if c.paraphrase_group}
    print(f"Paraphrase groups: {len(groups)}")

    print("\nPer template:")
    for tid, n in stats.per_template.items():
        flag = "  <-- EMPTY" if n == 0 else ""
        print(f"  {n:5d}  {tid}{flag}")

    print("\nSkips (why cases were not generated):")
    for key, value in stats.to_dict().items():
        if key.startswith("skipped") or key == "sql_errors":
            print(f"  {key:24s} {value}")

    missing_facts = [c.id for c in suite.cases if not c.expected_fact_ids]
    if missing_facts:
        print(f"\nERROR: {len(missing_facts)} case(s) carry no expected_fact_ids")
        return 1

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    suite.save(args.out)

    # Record what was generated, so a stored run can be traced back to the exact suite
    # version and corpus state it was scored against.
    from src.eval.core.store import PostgresEvalStore

    if PostgresEvalStore.from_env().persist_suite(suite, stats.to_dict()):
        print(f"recorded suite {suite.qualified_name} in eval.eval_suite")

    size_kb = os.path.getsize(args.out) / 1024
    print(f"\nWrote {args.out}  ({size_kb:.0f} KB)")

    meta_path = args.out.replace(".json", "_buildstats.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "tier": args.tier,
                "corpus_fingerprint": suite.corpus_fingerprint,
                "n_cases": len(suite),
                **stats.to_dict(),
            },
            f,
            indent=2,
        )
        f.write("\n")
    print(f"Wrote {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
