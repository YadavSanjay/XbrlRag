"""Import saved paraphrase replies into a frozen, hashed paraphrase set.

Phrasing source 2 of the v2 eval (docs/specs/eval-paraphrases.md). Reads every ``*.txt``
reply file in a directory (each starts with ``# model:``, ``# date:`` and ``# prompt:``
header lines the maintainer adds), checks format and meaning, removes duplicates, assigns
the three-axis split and writes the set. It prints only the import report: counts, case
ids, reasons and the content hash, plus the review texts unless ``--sealed``. No LLM call.

    conda run -n py312 python scripts/eval/import_paraphrases.py `
        --intents <intents.json> --per-intent 3 --share 0.3 --salt <salt> `
        --split dev --replies-dir <dir> --out <paraphrases.json> [--sealed]

Use the same --intents / --per-intent / --share / --salt / --split as the export. To accept
a review entry, edit its line in the reply file and re-import. Costs $0.

Re-freeze: ``--merge-with <carried.json>`` (a set written by paraphrase_carryover.py) merges
the import with the carried-over records before writing (``merge_paraphrase_sets``: the
duplicate rule re-runs over both; a shared paraphrase id or reply file is refused). The
replies may cover any subset of the split; the sources are always the whole split's.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.suites.paraphrase_sources import (  # noqa: E402
    add_source_arguments,
    load_sources,
)
from src.eval.suites.paraphrases import (  # noqa: E402
    import_replies,
    load_paraphrase_set,
    merge_paraphrase_sets,
    render_import_report,
    write_paraphrase_set,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    add_source_arguments(p)
    p.add_argument("--replies-dir", type=Path, required=True,
                   help="directory of saved reply files (*.txt)")
    p.add_argument("--out", type=Path, required=True, help="the paraphrase set JSON to write")
    p.add_argument("--prompt-version", default="v1", help="instruction version the replies name")
    p.add_argument("--sealed", action="store_true",
                   help="held-out set: the report shows no paraphrase text")
    p.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    p.add_argument("--merge-with", type=Path, default=None,
                   help="a carried-over paraphrase set to merge the import with")
    return p


def main(argv: list[str] | None = None) -> int:
    """Imports the replies and writes the set.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.

    Returns:
        The exit code: 0 when written, 1 on an input error or an existing output.
    """
    args = _parser().parse_args(argv)
    out: Path = args.out
    if out.exists() and not args.force:
        print(f"refusing to overwrite {out.name} (pass --force)")
        return 1
    replies = sorted(args.replies_dir.glob("*.txt")) if args.replies_dir.is_dir() else []
    if not replies:
        print(f"error: no *.txt reply files in {args.replies_dir}")
        return 1
    try:
        sources = load_sources(args.intents, args.phrasebook, args.lexicon,
                               per_intent=args.per_intent, share=args.share, salt=args.salt,
                               split=args.split, forms_path=args.forms)
        pset = import_replies(sources, replies, prompt_version=args.prompt_version,
                              share=args.share, salt=args.salt, sealed=args.sealed)
        imported = pset
        if args.merge_with is not None:
            carried = load_paraphrase_set(args.merge_with)
            pset = merge_paraphrase_sets(carried, imported, sources)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(f"reply files: {len(replies)}")
    if args.merge_with is not None:
        print(f"imported: paraphrases={len(imported['paraphrases'])} "
              f"review={len(imported['review'])} duplicates={len(imported['duplicates'])}")
        print(f"carried: paraphrases={len(carried['paraphrases'])} "
              f"review={len(carried['review'])} duplicates={len(carried['duplicates'])}")
        print("merged:")
    print(render_import_report(pset), end="")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_paraphrase_set(pset, out)
    print(f"wrote {out.name}")
    return 0


if __name__ == "__main__":
    # Unsealed review texts may be non-ASCII; never fail on a Windows console code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
