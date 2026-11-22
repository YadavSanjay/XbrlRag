"""Fixtures for the html10k-tables-scale-2 spec tests.

Spec: docs/specs/html10k-tables-scale-2.md. The only input is synthetic: the HTML snippets next to
this file, each saying in its first line what it is, and documents written in the tests.
Session-scoped so hypothesis tests can use them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.chunking.html10k_chunking import Html10kChunk, chunk_filing
from src.ingest.html10k.ix_strip import ix_strip_html

FIXTURES = Path(__file__).resolve().parent / "fixtures"
XHTML = "http://www.w3.org/1999/xhtml"
ACCESSION = "0000000000-26-000001"


def _load(name: str) -> str:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text.split("-->", 1)[1].strip()


def _mini(header: Sequence[str], rows: Sequence[tuple[str, Sequence[str]]], stub: str = "") -> str:
    head = f"<tr><td>{stub}</td>" + "".join(f"<td>{h}</td>" for h in header) + "</tr>"
    body = "".join(
        f"<tr><td>{label}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        for label, cells in rows
    )
    return f"<table>{head}{body}</table>"


def _doc(*parts: str) -> str:
    """Joins blocks and tables into a document; a part not starting with ``<`` is a block."""
    blocks = "".join(p if p.startswith("<") else f"<div>{p}</div>" for p in parts)
    return f'<html xmlns="{XHTML}"><head><title>t</title></head><body>{blocks}</body></html>'


def _chunks(document: str, **kwargs: int) -> list[Html10kChunk]:
    return chunk_filing(ix_strip_html(document.encode("utf-8")), ACCESSION, **kwargs)


def _row(table, label: str, nth: int = 0):
    return [r for r in table.rows if r.label == label][nth]


def _scales(row_) -> list[int]:
    return [c.scale for c in row_.cells]


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The helpers, as one namespace."""
    return SimpleNamespace(
        load=_load, mini=_mini, doc=_doc, chunks=_chunks, row=_row, scales=_scales
    )


@pytest.fixture(scope="session")
def plain(kit: SimpleNamespace) -> Callable[..., str]:
    """A table with nothing of its own to say about scale."""
    return lambda label="Goodwill": kit.mini(["2024"], [(label, ["700"])])
