"""WS-2 / B10 — run the prompt×model regression matrix against the golden set and
gate on a baseline arm's accuracy. CI entry point (exits non-zero on regression).

    conda run -n py312 python scripts/eval/run_regression_matrix.py
    conda run -n py312 python scripts/eval/run_regression_matrix.py --compare-synth-model gpt-5

By default runs a single "baseline" arm (the current prod config) and fails if its
numeric-accuracy mean drops below --min-accuracy. Pass --compare-* to add variant
arms and compare configs side by side. Requires OPENAI_API_KEY + XBRL_DB_*.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.eval.regression.prompt_matrix import Arm  # noqa: E402
from src.eval.xbrl.golden import DEFAULT_GOLDEN_PATH, load_golden_set  # noqa: E402
from src.eval.xbrl.regression_runner import regression_gate, run_regression  # noqa: E402

BASELINE = "baseline"


def main() -> int:
    ap = argparse.ArgumentParser(description="XBRL prompt×model regression matrix (B10).")
    ap.add_argument("--golden", default=DEFAULT_GOLDEN_PATH)
    ap.add_argument("--min-accuracy", type=float, default=0.8, help="baseline gate threshold")
    ap.add_argument("--compare-synth-model", default=None,
                    help="add a variant arm overriding the synthesizer model (e.g. gpt-5)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cases = load_golden_set(args.golden)
    arms = [Arm(label=BASELINE)]  # no overrides == current prod config
    if args.compare_synth_model:
        arms.append(Arm(label=f"synth={args.compare_synth_model}",
                        stage_models={"synthesizer": args.compare_synth_model}))

    result = run_regression(arms, cases)

    for s in result.summaries:
        acc = s.scores.get("correct", 0.0)
        cited = s.scores.get("cited", 0.0)
        print(f"[{s.arm}] accuracy={acc:.1%} cited={cited:.1%} errors={s.errors}/{s.n}")

    gate = regression_gate(result, BASELINE, min_score=args.min_accuracy)
    status = "PASS" if gate.passed else "FAIL"
    print(f"\nGATE [{status}] baseline accuracy {gate.score:.1%} "
          f"(threshold {gate.min_score:.0%})")
    if args.json:
        print(json.dumps(gate.detail, indent=2, default=str))
    return 0 if gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
