"""WS-2 / A4 — snapshot the current golden-set quality as the drift baseline.

Runs the golden set through the live pipeline, records overall accuracy, citation
rate, and per-tag accuracy, and writes them to test/eval/golden/baseline_v1.json.
Re-run to re-baseline after an intentional quality change.

    conda run -n py312 python scripts/eval/build_baseline.py

Requires OPENAI_API_KEY + XBRL_DB_*.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.eval.xbrl.drift import Baseline, _cited_rate, per_tag_accuracy  # noqa: E402
from src.eval.xbrl.golden import DEFAULT_GOLDEN_PATH, NumericGrader, load_golden_set  # noqa: E402

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "test", "eval", "golden", "baseline_v1.json",
)


def main(stamp: str = "") -> int:
    cases = load_golden_set(DEFAULT_GOLDEN_PATH)
    from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
    pipeline = XbrlRetrievalPipeline(persist_audit=False)

    report = NumericGrader(cases).grade(lambda q: pipeline.generate(q))
    baseline = Baseline(
        accuracy=report.accuracy,
        cited_rate=_cited_rate(report.results),
        per_tag=per_tag_accuracy(cases, report.results),
        generated_at=stamp or None,
        n_cases=report.total,
    )
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(baseline.to_dict(), f, indent=2)
    print(f"baseline: accuracy={baseline.accuracy:.1%} cited={baseline.cited_rate:.1%} "
          f"({report.passed}/{report.total}) -> {OUT_PATH}")
    print("per-tag:", json.dumps(baseline.to_dict()["per_tag"]))
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", default="", help="ISO timestamp to record (optional)")
    raise SystemExit(main(ap.parse_args().stamp))
