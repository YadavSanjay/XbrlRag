"""Work out a re-frozen split's paraphrase carry-over: the ids to paraphrase and the kept set.

The re-freeze rule (docs/eval/heldout_v2/preregistration.md, freezes of 2026-11-09 and
Amendment 1.2): records of removed cases are dropped; added cases, and cases reworded in
place (same id, a different question), are paraphrased afresh by a fourth-family model
through the export / import. For one split this reads the old frozen suite, the new frozen
suite and the old paraphrase set, and writes

- ``--ids-out``: the new-suite case ids to paraphrase, one per line (the input of
  ``export_paraphrase_batches.py --case-ids``);
- ``--set-out``: the carried-over set, the old records of the cases still in the new suite
  with the same question, unchanged, under a fresh content hash (the input of
  ``import_paraphrases.py --merge-with``).

It prints counts only, never question or paraphrase text. No LLM call; costs $0.

    conda run -n py312 python scripts/eval/paraphrase_carryover.py `
        --old-suite <old>/xbrl_v2_dev.json --new-suite <new>/xbrl_v2_dev.json `
        --old-set <old>/paraphrases_dev_frozen.json `
        --ids-out <dir>/ids_dev.txt --set-out <dir>/paraphrases_dev_carried.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.suites.paraphrases import (  # noqa: E402
    carry_over_set,
    load_paraphrase_set,
    write_paraphrase_set,
)
from src.eval.suites.v2_suite import content_hash  # noqa: E402


def load_suite(path: Path) -> dict[str, Any]:
    """Reads a frozen v2 suite and checks its content hash.

    Args:
        path: The suite JSON (``{"split", "cases", "content_hash", ...}``).

    Returns:
        The suite.

    Raises:
        ValueError: When the file is not a suite or its hash does not match its cases.
    """
    try:
        suite = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise ValueError(f"{path.name}: not valid JSON") from None
    if not isinstance(suite, dict) or not isinstance(suite.get("cases"), list):
        raise ValueError(f"{path.name}: not a suite (no cases list)")
    if suite.get("content_hash") != content_hash(suite["cases"]):
        raise ValueError(f"{path.name}: content_hash does not match its cases")
    return suite


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("--old-suite", type=Path, required=True, help="the old frozen suite")
    p.add_argument("--new-suite", type=Path, required=True, help="the new frozen suite")
    p.add_argument("--old-set", type=Path, required=True, help="the old paraphrase set")
    p.add_argument("--ids-out", type=Path, required=True, help="the ids to paraphrase")
    p.add_argument("--set-out", type=Path, required=True, help="the carried-over set")
    p.add_argument("--force", action="store_true", help="overwrite existing outputs")
    return p


def main(argv: list[str] | None = None) -> int:
    """Writes the id list and the carried-over set, and prints their counts.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.

    Returns:
        The exit code: 0 when written, 1 on an input error or an existing output.
    """
    args = _parser().parse_args(argv)
    for out in (args.ids_out, args.set_out):
        if out.exists() and not args.force:
            print(f"refusing to overwrite {out.name} (pass --force)")
            return 1
    try:
        old = load_suite(args.old_suite)
        new = load_suite(args.new_suite)
        if old.get("split") != new.get("split"):
            print("error: the two suites are of different splits")
            return 1
        pset = load_paraphrase_set(args.old_set)
        ids, carried = carry_over_set(old["cases"], new["cases"], pset)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    old_ids = {c["id"] for c in old["cases"]}
    new_q = {c["id"]: c["question"] for c in new["cases"]}
    old_q = {c["id"]: c["question"] for c in old["cases"]}
    reworded = sum(1 for cid in ids if cid in old_q and old_q[cid] != new_q[cid])
    carried_cases = {r["case_id"] for r in carried["paraphrases"]}
    print(f"split: {new.get('split')}")
    print(f"cases: old={len(old_ids)} new={len(new_q)} removed={len(old_ids - set(new_q))}")
    print(f"to paraphrase: {len(ids)} (reworded={reworded}, "
          f"added or without a kept paraphrase={len(ids) - reworded})")
    print(f"carried cases: {len(carried_cases)}")
    print(f"carried records: paraphrases={len(carried['paraphrases'])} of "
          f"{len(pset['paraphrases'])}, review={len(carried['review'])} of "
          f"{len(pset['review'])}, duplicates={len(carried['duplicates'])} of "
          f"{len(pset['duplicates'])}, reply files={len(carried['sources'])} of "
          f"{len(pset['sources'])}")
    args.ids_out.parent.mkdir(parents=True, exist_ok=True)
    args.ids_out.write_text("".join(f"{cid}\n" for cid in ids), encoding="utf-8", newline="\n")
    args.set_out.parent.mkdir(parents=True, exist_ok=True)
    digest = write_paraphrase_set(carried, args.set_out)
    print(f"content_hash: {digest}")
    print(f"wrote {args.ids_out.name}, {args.set_out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
