r"""Build a derived v2 suite: a split's template cases plus its kept paraphrases.

The output is one hashed suite file for ``run_suite.py --suite-path``; it records the hashes of
every suite and paraphrase set it was built from (``src/eval/suites/paraphrase_suite.py``;
docs/specs/paraphrase-suite-wiring.md). Pass every frozen suite and every paraphrase set: a
paraphrase's source case may sit in the other split's suite. Prints counts and hashes only,
never a question or a paraphrase. No database, network or LLM call; costs $0.

    conda run -n py312 python scripts/eval/build_paraphrase_suite.py --split validation `
        --suite data/eval/v2_frozen/xbrl_v2_dev.json `
        --suite data/eval/v2_frozen/xbrl_v2_validation.json `
        --paraphrases data/eval/v2_frozen/paraphrases_dev_frozen.json `
        --paraphrases data/eval/v2_frozen/paraphrases_validation_frozen.json `
        --out data/eval/v2_frozen/xbrl_v2_validation_paraphrased.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.suites.paraphrase_suite import (  # noqa: E402
    build_paraphrase_suite,
    write_paraphrase_suite,
)
from src.eval.suites.paraphrases import load_paraphrase_set  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("--split", required=True, choices=("dev", "validation"),
                   help="the split to build")
    p.add_argument("--suite", type=Path, action="append", required=True,
                   help="a frozen v2 suite file (repeat; pass dev and validation)")
    p.add_argument("--paraphrases", type=Path, action="append", required=True,
                   help="a frozen paraphrase set file (repeat; pass every set)")
    p.add_argument("--out", type=Path, required=True, help="the derived suite file to write")
    p.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    return p


def _load_suite(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise ValueError(f"{path.name}: not a JSON suite") from None


def main(argv: list[str] | None = None) -> int:
    """Builds the derived suite and writes it.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.

    Returns:
        0 when written; 1 on an input error, a missing input file or an existing output file.
    """
    args = _parser().parse_args(argv)
    if args.out.exists() and not args.force:
        print(f"refusing to overwrite {args.out}; pass --force")
        return 1
    try:
        suites = [_load_suite(p) for p in args.suite]
        sets = [load_paraphrase_set(p) for p in args.paraphrases]
        doc = build_paraphrase_suite(args.split, suites, sets)
    except FileNotFoundError as exc:
        print(f"error: no such file {exc.filename}")
        return 1
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_paraphrase_suite(doc, args.out)
    for row in doc["inputs"]["suites"]:
        print(f"suite: {row['name']} ({row['split']}) {row['content_hash']}")
    for row in doc["inputs"]["paraphrase_sets"]:
        print(f"paraphrase set: {row['content_hash']} prompt {row['prompt_version']}, "
              f"{row['n_selected']} selected")
    print(f"{doc['name']}: {doc['n_cases']} cases, {doc['counts']['template']} template + "
          f"{doc['counts']['paraphrase']} paraphrase")
    print(f"content_hash: {doc['content_hash']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
