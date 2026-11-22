"""WS-2 / A4 — drift check: re-score the golden set and alert if quality dropped
below the recorded baseline. Schedule this (cron); exits non-zero on drift.

    conda run -n py312 python scripts/eval/run_drift_check.py
    conda run -n py312 python scripts/eval/run_drift_check.py --accuracy-tolerance 0.15

Requires OPENAI_API_KEY + XBRL_DB_*.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.eval.xbrl.drift import Baseline, DriftDetector  # noqa: E402
from src.eval.xbrl.golden import DEFAULT_GOLDEN_PATH, load_golden_set  # noqa: E402

BASELINE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "test", "eval", "golden", "baseline_v1.json",
)


def main() -> int:
    ap = argparse.ArgumentParser(description="XBRL golden-set drift check (A4).")
    ap.add_argument("--golden", default=DEFAULT_GOLDEN_PATH)
    ap.add_argument("--baseline", default=BASELINE_PATH)
    ap.add_argument("--accuracy-tolerance", type=float, default=0.1)
    ap.add_argument("--cited-tolerance", type=float, default=0.1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.baseline):
        print(f"no baseline at {args.baseline}; run scripts/eval/build_baseline.py first",
              file=sys.stderr)
        return 2

    cases = load_golden_set(args.golden)
    baseline = Baseline.load(args.baseline)
    from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
    pipeline = XbrlRetrievalPipeline(persist_audit=False)

    detector = DriftDetector(cases, baseline,
                             accuracy_tolerance=args.accuracy_tolerance,
                             cited_tolerance=args.cited_tolerance)
    report = detector.check(lambda q: pipeline.generate(q))

    status = "DRIFT" if report.drifted else "OK"
    print(f"[{status}] accuracy {report.current_accuracy:.1%} "
          f"(baseline {report.baseline_accuracy:.1%}), "
          f"cited {report.current_cited_rate:.1%} (baseline {report.baseline_cited_rate:.1%})")
    for r in report.reasons:
        print("  -", r)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    return 1 if report.drifted else 0


if __name__ == "__main__":
    raise SystemExit(main())
