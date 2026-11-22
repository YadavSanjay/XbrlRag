r"""Measure how often a money table's scale reaches its chunk text (html10k-tables-scale-2, Notes).

A *money table* is a table with an oracle cell of unit USD and scale 3 or more. The reading is the
share of money tables with a scale word in the text of every chunk part, because the part is what
the model reads. A money table with no chunk counts as a miss: nothing states its scale.

    conda run -n py312 python scripts/html10k/measure_chunk_scale.py `
        --oracle data\eval\html10k_oracle\table_oracle.jsonl `
        --edgar-root data\storage\sec_edgar

Test tooling, like the oracle: it reads the oracle to choose the tables, and only printed chunk
text to score them. Each line it prints is a count, never filing text.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from pathlib import Path
from typing import NamedTuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_table_oracle import (  # noqa: E402  (needs the script folder on the path)
    DEFAULT_EDGAR_ROOT,
    DEFAULT_OUT_DIR,
    Filing,
    iter_filings,
)
from measure_table_accuracy import load_oracle  # noqa: E402  (needs the script folder on the path)

from src.chunking.html10k_chunking import (  # noqa: E402  (needs REPO_ROOT on the path)
    Html10kChunk,
    chunk_filing,
)
from src.ingest.html10k.ix_strip import ix_strip_html  # noqa: E402  (needs REPO_ROOT on the path)
from src.ingest.html10k.tables import OracleCell  # noqa: E402  (needs REPO_ROOT on the path)

RAG_CONFIG = REPO_ROOT / "configs" / "pipelines" / "html10k" / "rag.yaml"
MONEY_UNIT = "USD"
MONEY_SCALE = 3
TABLE_KIND = "table"
# "000" as a heading ("$000", "(in 000s)"), not the last digits of "1,000".
SCALE_WORD = re.compile(
    r"\b(?:thousands|millions|billions)\b|(?<![\d,.])000(?:['’]?s)?(?!\d)", re.IGNORECASE
)
BAR_POOLED = 0.97
BAR_FILER = 0.90


class ChunkScale(NamedTuple):
    """The reading of one set of money tables."""

    money_tables: int
    with_scale: int
    without_chunk: int

    @property
    def share(self) -> float:
        """Returns the share of money tables with a scale word in every part."""
        return self.with_scale / self.money_tables if self.money_tables else 0.0


def load_table_max_chars(path: Path = RAG_CONFIG) -> int:
    """Returns the size of a table part the load uses, so the reading is of what is loaded."""
    return int(yaml.safe_load(path.read_text(encoding="utf-8"))["table_max_chars"])


def money_tables(oracle: Iterable[OracleCell]) -> dict[str, set[int]]:
    """Returns the money tables of each filing, by accession number."""
    tables: dict[str, set[int]] = defaultdict(set)
    for cell in oracle:
        if cell.unit == MONEY_UNIT and cell.scale >= MONEY_SCALE:
            tables[cell.accession].add(cell.table_index)
    return dict(tables)


def read_chunks(chunks: Iterable[Html10kChunk], tables: set[int]) -> ChunkScale:
    """Reads one filing's chunks.

    Args:
        chunks: The filing's chunks.
        tables: The ordinals of its money tables.

    Returns:
        The filing's reading.
    """
    parts: dict[int, list[str]] = defaultdict(list)
    for chunk in chunks:
        if chunk.kind == TABLE_KIND and chunk.table_index in tables:
            parts[chunk.table_index].append(chunk.text)
    with_scale = sum(
        1 for texts in parts.values() if all(SCALE_WORD.search(text) for text in texts)
    )
    return ChunkScale(len(tables), with_scale, len(tables) - len(parts))


def _read_filing(filing: Filing, tables: set[int], max_chars: int) -> tuple[str, ChunkScale]:
    stripped = ix_strip_html(filing.path.read_bytes(), filing.path.name)
    chunks = chunk_filing(stripped, filing.accession, table_max_chars=max_chars)
    return filing.ticker, read_chunks(chunks, tables)


def measure(
    filings: Sequence[Filing], tables: Mapping[str, set[int]], max_chars: int, workers: int
) -> dict[str, ChunkScale]:
    """Reads every filing that has a money table.

    Args:
        filings: The filings on disk.
        tables: The money tables by accession number.
        max_chars: The size a table part may reach.
        workers: How many processes to use.

    Returns:
        The reading of each filer.
    """
    scope = [f for f in filings if f.accession in tables]
    by_filer: dict[str, ChunkScale] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        wanted = [tables[f.accession] for f in scope]
        for ticker, reading in pool.map(_read_filing, scope, wanted, repeat(max_chars)):
            held = by_filer.get(ticker, ChunkScale(0, 0, 0))
            by_filer[ticker] = ChunkScale(*(a + b for a, b in zip(held, reading, strict=True)))
    return by_filer


def describe(reading: ChunkScale) -> str:
    """Formats one reading."""
    return (
        f"{reading.share:.2%} {reading.with_scale}/{reading.money_tables} money tables "
        f"without_chunk={reading.without_chunk}"
    )


def main(argv: list[str] | None = None) -> int:
    """Prints the pooled and per-filer reading against the bar."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--oracle", type=Path, default=DEFAULT_OUT_DIR / "table_oracle.jsonl")
    parser.add_argument("--edgar-root", type=Path, default=DEFAULT_EDGAR_ROOT)
    parser.add_argument("--fiscal-year", type=int, action="append", default=[])
    parser.add_argument("--filer", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--table-max-chars", type=int, default=load_table_max_chars())
    args = parser.parse_args(argv)

    filings = list(iter_filings(args.edgar_root, args.filer, args.fiscal_year))
    accessions = {f.accession for f in filings}
    oracle = [c for c in load_oracle(args.oracle) if c.accession in accessions]
    by_filer = measure(filings, money_tables(oracle), args.table_max_chars, args.workers)
    pooled = ChunkScale(*(sum(column) for column in zip(*by_filer.values(), strict=True)))
    print(f"pooled: {describe(pooled)}")
    for ticker, reading in sorted(by_filer.items()):
        print(f"  {ticker:6} {describe(reading)}")
    met = pooled.share >= BAR_POOLED and all(r.share >= BAR_FILER for r in by_filer.values())
    print(f"bar ({BAR_POOLED:.0%} pooled, no filer below {BAR_FILER:.0%}): {met}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
