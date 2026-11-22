"""WS-2 / A3 — run the static golden set through the live XBRL pipeline and print
a scored accuracy report.

    conda run -n py312 python scripts/eval/run_golden_eval.py
    conda run -n py312 python scripts/eval/run_golden_eval.py --tag compute --json

Requires OPENAI_API_KEY (the pipeline makes LLM calls) and the XBRL_DB_* env vars.
Each case's audited number is pulled from the response's execution_result (not the
prose) and compared to the labelled expected value within the case's tolerance.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.eval.xbrl.golden import DEFAULT_GOLDEN_PATH, NumericGrader, load_golden_set  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Score the XBRL pipeline against the golden set.")
    ap.add_argument("--golden", default=DEFAULT_GOLDEN_PATH, help="golden-set JSON path")
    ap.add_argument("--tag", default=None, help="only run cases carrying this tag")
    ap.add_argument("--json", action="store_true", help="also print the full JSON report")
    args = ap.parse_args()

    cases = load_golden_set(args.golden)
    if args.tag:
        cases = [c for c in cases if args.tag in c.tags]
    if not cases:
        print("no cases to run", file=sys.stderr)
        return 2

    from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
    pipeline = XbrlRetrievalPipeline(persist_audit=False)

    grader = NumericGrader(cases)
    report = grader.grade(lambda q: pipeline.generate(q))

    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        got = f"{r.matched_value:g}" if r.matched_value is not None else "-"
        print(f"[{mark}] {r.case_id}: expected={r.expected:g} got={got}  {r.reason}"
              + (f"  ERROR={r.error}" if r.error else ""))

    print(f"\nACCURACY  {report.passed}/{report.total} = {report.accuracy:.1%}")
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
