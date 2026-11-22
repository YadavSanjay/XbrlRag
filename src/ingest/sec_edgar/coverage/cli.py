# The coverage-card CLI: prints the empty-cell report.
# Spec: docs/specs/coverage-card.md (requirement 8, C7).
#
#   python -m src.ingest.sec_edgar.coverage [--cik CIK] [--fiscal-year YEAR] [--strict]

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from src.ingest.sec_edgar.coverage.base import CoverageCardSource
from src.ingest.sec_edgar.coverage.card import cards_from_rows, empty_cells, pad_cik, render_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.ingest.sec_edgar.coverage",
        description="Print the empty cells (component_only or absent) of the coverage cards.")
    parser.add_argument("--cik", help="Only this filer (zero-padded to 10 digits).")
    parser.add_argument("--fiscal-year", type=int, help="Only this fiscal year.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 when the report has an empty cell.")
    return parser


def main(argv: Sequence[str] | None = None, source: CoverageCardSource | None = None,
         out: TextIO | None = None) -> int:
    """Prints the empty cells of the coverage cards.

    Args:
        argv: Arguments: ``--cik``, ``--fiscal-year``, ``--strict``.
        source: The card rows; the fact store's ``mv_coverage_card`` when ``None``.
        out: Where the report goes; standard output when ``None``.

    Returns:
        0, or 1 under ``--strict`` when there is an empty cell, or 2 when no card matches.
    """
    args = _parser().parse_args(argv)
    if source is None:
        from src.ingest.sec_edgar.coverage.pg_source import PostgresCoverageCardSource

        source = PostgresCoverageCardSource()
    stream = sys.stdout if out is None else out
    cik = None if args.cik is None else pad_cik(args.cik)
    cards = cards_from_rows(source.rows(cik, args.fiscal_year), cik, args.fiscal_year)
    if not cards:
        stream.write(f"no coverage cards match cik={cik or '*'} "
                     f"fiscal_year={args.fiscal_year or '*'}\n")
        return 2
    stream.write(render_report(cards))
    return 1 if args.strict and empty_cells(cards) else 0
