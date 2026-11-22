"""Build the dev text suite for judge calibration (docs/specs/judge-calibration.md, D3).

Reads the hand-reviewed authoring file ``docs/eval/judge_calibration/dev_text_questions.yaml``
(hx-authoring's ``text`` shape), checks it with ``validate_hx`` against the development and
held-out filer sets, and writes ``docs/eval/judge_calibration/dev_text_suite.json``: one
``kind: "narrative"`` EvalCase per entry, tagged ``split:dev``, which
``scripts/eval/judge_calibration.py export --suite`` reads.

    conda run -n py312 python scripts/eval/build_judge_calibration_dev_suite.py

Filer sets: development = the tickers of ``configs/pipelines/sec_edgar_downloads.yaml``
(the corpus); held-out = every symbol in ``docs/eval/heldout_v2/heldout_v2_draw.json``
(drawn and alternates). A case naming a held-out or non-development filer fails the build.
No database, no LLM; costs $0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.case import EvalSuite  # noqa: E402
from src.eval.core.judge_wiring import is_text_graded  # noqa: E402
from src.eval.core.run_columns import run_split  # noqa: E402
from src.eval.set_b.core import load_authoring  # noqa: E402
from src.eval.set_b.hx import validate_hx  # noqa: E402

DEFAULT_IN = REPO_ROOT / "docs" / "eval" / "judge_calibration" / "dev_text_questions.yaml"
DEFAULT_OUT = REPO_ROOT / "docs" / "eval" / "judge_calibration" / "dev_text_suite.json"
DOWNLOADS = REPO_ROOT / "configs" / "pipelines" / "sec_edgar_downloads.yaml"
HELDOUT_DRAW = REPO_ROOT / "docs" / "eval" / "heldout_v2" / "heldout_v2_draw.json"

SUITE_NAME = "judge_calibration_dev_text"
SUITE_VERSION = "v1"


def dev_filers(downloads_text: str) -> frozenset[str]:
    """The development filers: every ticker in the downloads config."""
    doc = yaml.safe_load(downloads_text)
    return frozenset(str(d["ticker"]) for d in doc["downloads"])


def heldout_filers(draw_text: str) -> frozenset[str]:
    """Every ``symbol`` (or ``ticker``) value in the held-out draw, drawn and alternates."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("symbol", "ticker") and isinstance(value, str):
                    found.add(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(draw_text))
    return frozenset(found)


def build_suite(cases: list[dict]) -> dict:
    """The suite dict: one narrative case per authoring case, sorted by id.

    Args:
        cases: Validated ``text`` authoring cases.

    Returns:
        ``{"name", "version", "description", "cases"}``.

    Raises:
        ValueError: If a case is not of kind ``text``.
    """
    built = []
    for c in cases:
        if c["kind"] != "text":
            raise ValueError(f"{c['id']}: only text cases belong in the calibration set")
        tags = ["split:dev", "text"]
        if c.get("topic"):
            tags.append(f"topic:{c['topic']}")
        built.append({
            "id": c["id"],
            "question": c["question"],
            "kind": "narrative",
            "suite": f"{SUITE_NAME}_{SUITE_VERSION}",
            "origin": "draft:judge_calibration_dev_text",
            "ticker": c["ticker"],
            "fy": c["fy"],
            "expected_text": c["reference_answer"],
            "key_points": list(c["key_points"]),
            "passages": [{"accession": p["accession"], "quote": p["quote"]}
                         for p in c["passages"]],
            "tags": tags,
        })
    built.sort(key=lambda d: d["id"])
    return {
        "name": SUITE_NAME,
        "version": SUITE_VERSION,
        "description": "DRAFT dev text questions for judge calibration (never scored); "
                       "source: docs/eval/judge_calibration/dev_text_questions.yaml",
        "cases": built,
    }


def main(argv: list[str] | None = None) -> int:
    """Validates the authoring file and writes the suite JSON.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        0 when the suite was written, 1 when validation failed.
    """
    p =argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("--in", dest="src", type=Path, default=DEFAULT_IN)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)

    cases = load_authoring(args.src.read_text(encoding="utf-8"))
    dev = dev_filers(DOWNLOADS.read_text(encoding="utf-8"))
    held = heldout_filers(HELDOUT_DRAW.read_text(encoding="utf-8"))
    if not held:
        print(f"not built: no held-out filers found in {HELDOUT_DRAW.name}")
        return 1
    result = validate_hx(cases, dev, held)
    for err in result["errors"]:
        print(f"error: {err}")
    if result["errors"]:
        print(f"not built: {len(result['errors'])} error(s)")
        return 1
    try:
        suite = build_suite(cases)
    except ValueError as exc:
        print(f"not built: {exc}")
        return 1

    loaded = EvalSuite.from_dict(suite)
    if not all(is_text_graded(c) for c in loaded.cases) or run_split(loaded.cases) != "dev":
        print("not built: every case must be text-graded and split:dev")
        return 1

    args.out.write_text(json.dumps(suite, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    n_filers = len({c["ticker"] for c in suite["cases"]})
    print(f"wrote {args.out.name}: {len(suite['cases'])} cases, {n_filers} filers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
