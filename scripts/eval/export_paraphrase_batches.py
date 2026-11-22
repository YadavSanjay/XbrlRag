"""Export one split's phrased questions as paraphrase batch files for an LLM web UI.

Phrasing source 2 of the v2 eval (docs/specs/eval-paraphrases.md). Each batch file starts
with the versioned instruction block (prompts/templates/eval_paraphrase/<version>/) and is
pasted by hand into the web UI of a model from a fourth family (not OpenAI, Anthropic or
DeepSeek). No LLM call is made here, and nothing printed carries question text: only counts,
file names and (with --list-ids) case ids.

    conda run -n py312 python scripts/eval/export_paraphrase_batches.py `
        --intents <intents.json> --per-intent 3 --share 0.3 --salt <salt> `
        --split dev --out-dir <dir> [--case-ids <ids.txt>]

``--share``, ``--salt`` and ``--per-intent`` must be the suite's own, so the phrased cases
(and their case ids) match the ones the import will check against. Costs $0.

Subset export (a suite re-frozen after it was paraphrased; preregistration freezes of
2026-11-09 and Amendment 1.2). ``--case-ids`` names a file of case ids, one per line (blank
lines and ``#`` lines are skipped); only those cases are exported, in the same batch format
with the same instruction block. A listed id that is not a phrased case of the split is an
error. The re-freeze flow, per split:

    1. ``scripts/eval/paraphrase_carryover.py`` (old suite, new suite, old set) writes the
       ids to paraphrase (added + reworded cases) and the carried-over set.
    2. This script with ``--case-ids <ids file>`` writes the batches for those ids.
    3. ``scripts/eval/import_paraphrases.py ... --merge-with <carried set>`` imports the
       saved replies and writes the merged, hashed set.
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
    ParaphraseSource,
    export_batches,
    instructions_path,
)


def read_case_ids(path: Path) -> list[str]:
    """Reads a case-id list: one id per line; blank lines and ``#`` lines are skipped.

    Args:
        path: The file.

    Returns:
        The ids, in file order.

    Raises:
        ValueError: When the file cannot be read, lists no id or repeats one.
    """
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        raise ValueError(f"cannot read {path.name}") from None
    ids = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if not ids:
        raise ValueError(f"{path.name} lists no case id")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path.name} repeats a case id")
    return ids


def select_sources(sources: list[ParaphraseSource], ids: list[str]) -> list[ParaphraseSource]:
    """Keeps the sources whose case id is listed.

    Args:
        sources: The split's sources.
        ids: The case ids to keep.

    Returns:
        The listed sources, sorted by case id.

    Raises:
        ValueError: When a listed id is not among the sources.
    """
    by_id = {s.case_id: s for s in sources}
    unknown = sorted(set(ids) - set(by_id))
    if unknown:
        raise ValueError(f"{len(unknown)} listed case id(s) are not phrased cases of the "
                         f"split, e.g. {', '.join(unknown[:5])}")
    return [by_id[cid] for cid in sorted(ids)]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    add_source_arguments(p)
    p.add_argument("--out-dir", type=Path, required=True, help="where the batch files go")
    p.add_argument("--prompt-version", default="v1", help="instruction block version")
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--personas-per-case", type=int, default=2)
    p.add_argument("--case-ids", type=Path, default=None,
                   help="export only these case ids (a file, one id per line)")
    p.add_argument("--list-ids", action="store_true", help="also print each batch's case ids")
    return p


def main(argv: list[str] | None = None) -> int:
    """Builds the split's sources and writes one file per batch.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.

    Returns:
        The exit code: 0 when written, 1 on an input error or existing batch files.
    """
    args = _parser().parse_args(argv)
    out_dir: Path = args.out_dir
    if out_dir.is_dir() and any(out_dir.glob("batch_*.txt")):
        print(f"refusing to write: {out_dir} already holds batch files; use an empty directory")
        return 1
    try:
        sources = load_sources(args.intents, args.phrasebook, args.lexicon,
                               per_intent=args.per_intent, share=args.share, salt=args.salt,
                               split=args.split, forms_path=args.forms)
        if args.case_ids is not None:
            sources = select_sources(sources, read_case_ids(args.case_ids))
        instructions = instructions_path(args.prompt_version).read_text(encoding="utf-8")
        batches = export_batches(sources, instructions=instructions,
                                 prompt_version=args.prompt_version,
                                 batch_size=args.batch_size,
                                 personas_per_case=args.personas_per_case)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(batches)
    ordered = sorted(s.case_id for s in sources)
    print(f"split: {args.split}")
    print(f"questions: {len(sources)}")
    print(f"batches: {n}")
    for i, text in enumerate(batches, start=1):
        name = f"batch_{i:03d}_of_{n:03d}.txt"
        (out_dir / name).write_bytes(text.encode("utf-8"))
        ids = ordered[(i - 1) * args.batch_size:i * args.batch_size]
        print(f"{name}: {len(ids)} questions")
        if args.list_ids:
            print("".join(f"  {cid}\n" for cid in ids), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
