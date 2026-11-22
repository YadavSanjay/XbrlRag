r"""Build the frozen dev sample for the baseline arms that don't run the whole dev suite.

One phrasing per intent, stratified by shape, seeded (``src/eval/suites/dev_sample.py``;
docs/eval/heldout_v2/measurement_plan.md §3). The output is a suite file for
``run_suite.py --suite-path``; its ``content_hash`` goes into the pre-registration
amendment. No database, network or LLM call; costs $0.

    conda run -n py312 python scripts/eval/build_dev_sample.py `
        --suite data/eval/v2_frozen/xbrl_v2_dev.json --size 100 `
        --seed xbrlrag-v2-dev-sample-1 --out data/eval/v2_frozen/xbrl_v2_dev_sample.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.suites.dev_sample import sample_suite  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("--suite", type=Path, required=True, help="the v2 dev suite file")
    # Not "--n": `conda run` takes an abbreviated "--n" as its own --name.
    p.add_argument("--size", type=int, default=100, help="sample size (default 100)")
    p.add_argument("--seed", required=True, help="the draw's seed string")
    p.add_argument("--out", type=Path, required=True, help="the sample suite file to write")
    p.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    return p


def main(argv: list[str] | None = None) -> int:
    """Draws the sample and writes it.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.

    Returns:
        0 when written, 1 on an input error or an existing output file.
    """
    args = _parser().parse_args(argv)
    if args.out.exists() and not args.force:
        print(f"refusing to overwrite {args.out}; pass --force")
        return 1
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    try:
        sample = sample_suite(suite, args.size, args.seed)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sample, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
    meta = sample["sample"]
    print(f"source: {meta['source_suite']} {meta['source_content_hash']}")
    print(f"sample: {sample['n_cases']} cases, seed {meta['seed']!r}, "
          f"{meta['single_filer']} single-filer / {meta['cross_filer']} cross-filer")
    for shape, k in meta["by_shape"].items():
        print(f"  {shape:<20} {k}")
    print(f"content_hash: {sample['content_hash']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
