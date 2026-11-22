"""Cut SEC's Financial Statement Data Sets quarters down to a CIK list.

Spec: docs/specs/hf-suite-build.md, requirement 10. Each ``<yyyy>q<n>.zip`` of ``--raw-dir``
is cut to the filers' ``sub`` rows, the ``num`` and ``pre`` rows of their filings and the
``tag`` rows those use (the rule of the development cut ``data/cache/sec_fsds/filtered``),
every kept line byte for byte, into ``<out-dir>/<yyyy>q<n>/<table>.tsv``, which the
statement witness reads (``--fsds-dir`` of ``build_v2_suite.py``). ``--out-dir`` must lie
outside the repository:

    conda run -n py312 python scripts/eval/cut_fsds.py --raw-dir data/cache/sec_fsds/raw \
        --ciks-file <ho>/ciks.txt --out-dir <ho>/fsds

For H-F the CIK list is every filer of ``xbrl_heldout`` (development and held-out), because
screens over ``covered`` witness the development members too. A CIK file holds one CIK per
line (blank lines and ``#`` comments ignored). It prints per quarter the kept row counts only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.sec_reference.fsds_cut import TABLES, cut_fsds  # noqa: E402
from src.eval.suites.heldout_build import require_outside  # noqa: E402


def read_ciks(path: Path) -> list[str]:
    """The CIKs of a CIK file: one per line, blank lines and ``#`` comments ignored.

    Args:
        path: The file.

    Returns:
        The CIKs, in file order.

    Raises:
        ValueError: A line that is not a CIK.
    """
    ciks = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        if not text.isdigit() or len(text) > 10:
            raise ValueError(f"{path.name} line {n}: not a CIK")
        ciks.append(text)
    return ciks


def main(argv: list[str] | None = None) -> int:
    """Parses the arguments, cuts the quarters and prints the kept row counts.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when None.

    Returns:
        The exit code: 0 cut, 2 refused.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--raw-dir", type=Path, required=True,
                   help="the directory of SEC's quarter zips (<yyyy>q<n>.zip)")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--cik", action="append", help="a filer's CIK (repeatable)")
    group.add_argument("--ciks-file", type=Path, help="a file of CIKs, one per line")
    p.add_argument("--out-dir", type=Path, required=True,
                   help="where the quarters are written (outside the repository)")
    args = p.parse_args(argv)
    try:
        out_dir = require_outside(args.out_dir, REPO_ROOT)
    except ValueError:
        print("refused: --out-dir must lie outside the repository", file=sys.stderr)
        return 2
    ciks = list(args.cik) if args.cik else read_ciks(args.ciks_file)
    if not ciks:
        print("refused: no CIK given", file=sys.stderr)
        return 2
    zips = sorted(args.raw_dir.glob("*.zip"))
    if not zips:
        print(f"refused: {args.raw_dir} holds no quarter zip", file=sys.stderr)
        return 2
    counts = cut_fsds(zips, ciks, out_dir)
    print(f"filers {len(set(int(c) for c in ciks))}; quarters {len(counts)}")
    for quarter, tables in counts.items():
        print(f"  {quarter}: " + ", ".join(f"{t} {tables[t]}" for t in TABLES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
