"""Fixtures and an independent raw parser for the fsds-reader spec tests.

Spec: docs/specs/fsds-reader.md. The committed excerpts under ``fixtures/`` are SEC's
Financial Statement Data Sets files (header + selected rows, byte-for-byte; provenance in
``fixtures/provenance.json``) and a companyfacts excerpt. No network, no ``data/``.

``raw_rows`` is the tests' own oracle for "what the file says": a plain split on ``\\n``
and ``\\t`` (SEC readme §4), written here without the reader under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
QUARTER_DIRS = sorted(p for p in FIXTURES.iterdir() if p.is_dir())

# Accessions in the fixtures (see the spec's Notes table).
AAPL_24 = "0000320193-24-000123"
AAPL_23 = "0000320193-23-000106"
AAPL_22 = "0000320193-22-000108"
MSFT_24 = "0000950170-24-087843"
ORCL_25 = "0000950170-25-087926"
TSM_24 = "0001193125-25-083423"
AVGO_24 = "0001730168-24-000139"
COST_23 = "0000909832-23-000042"
CEG_23 = "0001868275-24-000014"


def raw_rows(table: str, dirs=None) -> list[dict[str, str]]:
    """Every row of ``table`` across ``dirs`` (default: all fixture quarters), in order."""
    out: list[dict[str, str]] = []
    for d in dirs or QUARTER_DIRS:
        text = (Path(d) / f"{table}.tsv").read_bytes().decode("utf-8")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        header = lines[0].split("\t")
        for ln in lines[1:]:
            out.append(dict(zip(header, ln.split("\t"), strict=True)))
    return out


def write_tables(directory: Path, tables: dict[str, tuple[list[str], list[list[str]]]]) -> None:
    """Writes ``{table: (header, rows)}`` as SEC-format TSV files into ``directory``."""
    for name, (header, rows) in tables.items():
        body = "\n".join("\t".join(r) for r in [header, *rows]) + "\n"
        (directory / f"{name}.tsv").write_bytes(body.encode("utf-8"))


NUM_HEADER = [
    "adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value", "footnote",
]
PRE_HEADER = [
    "adsh", "report", "line", "stmt", "inpth", "rfile", "tag", "version", "plabel", "negating",
]
SUB_HEADER = ["adsh", "cik", "name", "form", "period", "fy", "fp", "filed"]
TAG_HEADER = ["tag", "version", "custom", "abstract", "datatype", "iord", "crdr", "tlabel", "doc"]


@pytest.fixture(scope="session")
def kit():
    """Paths, accession ids, helpers and the companyfacts excerpt."""
    return SimpleNamespace(
        dirs=QUARTER_DIRS,
        raw_rows=raw_rows,
        write_tables=write_tables,
        companyfacts=json.loads(
            (FIXTURES / "companyfacts_excerpt.json").read_text(encoding="utf-8")
        ),
        NUM_HEADER=NUM_HEADER,
        PRE_HEADER=PRE_HEADER,
        SUB_HEADER=SUB_HEADER,
        TAG_HEADER=TAG_HEADER,
        AAPL_24=AAPL_24,
        AAPL_23=AAPL_23,
        AAPL_22=AAPL_22,
        MSFT_24=MSFT_24,
        ORCL_25=ORCL_25,
        TSM_24=TSM_24,
        AVGO_24=AVGO_24,
        COST_23=COST_23,
        CEG_23=CEG_23,
    )
