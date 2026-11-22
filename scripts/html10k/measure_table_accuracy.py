r"""Measure the table parser against the cell oracle (docs/specs/html10k-tables.md, Notes).

Each filing is stripped by ``ix_strip_html`` and parsed by ``parse_tables``, so the score can't
lean on tags. The accuracy is pooled over every oracle cell, and printed per filer too, because
pooling lets a large filer hide a small one's failure.

    conda run -n py312 python scripts/html10k/measure_table_accuracy.py `
        --edgar-root <repo>\data\storage\sec_edgar --fiscal-year 2024
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_table_oracle import (  # noqa: E402  (needs the script folder on the path)
    DEFAULT_EDGAR_ROOT,
    DEFAULT_OUT_DIR,
    Filing,
    iter_filings,
)

from src.ingest.html10k.ix_strip import ix_strip_html  # noqa: E402  (needs REPO_ROOT on the path)
from src.ingest.html10k.tables import (  # noqa: E402  (needs REPO_ROOT on the path)
    OracleCell,
    ParsedTable,
    TableScore,
    parse_oracle_record,
    parse_tables,
    score_tables,
)

Stripper = Callable[[bytes, str], bytes]


class Measurement(NamedTuple):
    """The pooled score, the score of each filer and the filings that would not parse."""

    pooled: TableScore
    by_filer: dict[str, TableScore]
    unparsed: list[str]


def load_oracle(path: Path) -> list[OracleCell]:
    """Reads the oracle file, validating every record."""
    with path.open(encoding="utf-8") as lines:
        return [parse_oracle_record(json.loads(line)) for line in lines]


def parse_filings(
    filings: Sequence[Filing], strip: Stripper
) -> tuple[dict[str, list[ParsedTable]], list[str]]:
    """Strips and parses each filing.

    Args:
        filings: The filings to parse.
        strip: The tag stripper.

    Returns:
        The parsed tables by accession, and the accessions that failed; those count as missing.
    """
    parsed: dict[str, list[ParsedTable]] = {}
    unparsed: list[str] = []
    for filing in filings:
        stripped = strip(filing.path.read_bytes(), filing.path.name)
        try:
            parsed[filing.accession] = parse_tables(stripped.decode("utf-8"))
        except ValueError as error:
            print(f"cannot parse {filing.ticker} {filing.fiscal_year}: {error}", file=sys.stderr)
            unparsed.append(filing.accession)
    return parsed, unparsed


def measure(
    oracle: Sequence[OracleCell], filings: Sequence[Filing], strip: Stripper = ix_strip_html
) -> Measurement:
    """Scores the parser over the filings that have oracle cells.

    Args:
        oracle: The oracle cells.
        filings: The filings on disk.
        strip: The tag stripper; the exit measurement uses the default.

    Returns:
        The pooled and per-filer scores.
    """
    accessions = {cell.accession for cell in oracle}
    scope = [f for f in filings if f.accession in accessions]
    parsed, unparsed = parse_filings(scope, strip)
    by_filer: dict[str, TableScore] = {}
    tickers = sorted({f.ticker for f in scope})
    for ticker in tickers:
        own = {f.accession for f in scope if f.ticker == ticker}
        by_filer[ticker] = score_tables(parsed, [c for c in oracle if c.accession in own])
    return Measurement(score_tables(parsed, oracle), by_filer, unparsed)


def describe(score: TableScore) -> str:
    """Formats one score with its error counts by kind."""
    return (
        f"{score.accuracy:.4%} exact {score.exact}/{score.total}  missing={score.missing} "
        f"value={score.value_errors} scale={score.scale_errors} sign={score.sign_errors}"
    )


def write_mismatches(score: TableScore, path: Path) -> None:
    """Writes each mismatch as one JSON line, for reading the failure classes."""
    with path.open("w", encoding="utf-8", newline="\n") as out:
        for oracle_cell, cell in score.mismatches:
            line = {
                "accession": oracle_cell.accession,
                "location": oracle_cell[1:4],
                "printed": oracle_cell.printed,
                "concept": oracle_cell.concept,
                "unit": oracle_cell.unit,
                "oracle": [str(oracle_cell.value), oracle_cell.negative, oracle_cell.scale],
                "parsed": None if cell is None else [str(cell.value), cell.scale, cell.text],
            }
            out.write(json.dumps(line, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Prints the pooled and per-filer accuracy."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--oracle", type=Path, default=DEFAULT_OUT_DIR / "table_oracle.jsonl")
    parser.add_argument("--edgar-root", type=Path, default=DEFAULT_EDGAR_ROOT)
    parser.add_argument("--fiscal-year", type=int, action="append", default=[])
    parser.add_argument("--filer", action="append", default=[])
    parser.add_argument("--mismatches", type=Path, default=None)
    args = parser.parse_args(argv)

    filings = list(iter_filings(args.edgar_root, args.filer, args.fiscal_year))
    accessions = {f.accession for f in filings}
    oracle = [c for c in load_oracle(args.oracle) if c.accession in accessions]
    result = measure(oracle, filings)
    print(f"pooled: {describe(result.pooled)}")
    for ticker, score in result.by_filer.items():
        print(f"  {ticker:6} {describe(score)}")
    print(f"filings with cells: {len(Counter(c.accession for c in oracle))}")
    print(f"filings that did not parse: {result.unparsed}")
    if args.mismatches is not None:
        write_mismatches(result.pooled, args.mismatches)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
