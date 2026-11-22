"""Fixtures for the html10k-chunks spec tests.

Spec: docs/specs/html10k-chunks.md. Every document is synthetic, for an invented filer, written in
the tests and passed through ix_strip_html as in the flow. Fixtures are session-scoped so hypothesis
tests can use them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.chunking.html10k_chunking import Html10kChunk, chunk_filing
from src.ingest.html10k.ix_strip import ix_strip_html

XHTML = "http://www.w3.org/1999/xhtml"
ACCESSION = "0000000000-26-000001"
REPO_ROOT = Path(__file__).resolve().parents[3]
NOT_A_CONTENTS_BLOCK = " this paragraph is long enough not to look like a table of contents."


def _doc(body: str) -> bytes:
    return f'<html xmlns="{XHTML}"><head><title>t</title></head><body>{body}</body></html>'.encode()


def _strip(body: str) -> bytes:
    return ix_strip_html(_doc(body))


def _chunks(body: str, **kwargs: int) -> list[Html10kChunk]:
    return chunk_filing(_strip(body), ACCESSION, **kwargs)


def _at(doc: bytes, marker: str) -> int:
    """Returns the offset of the opening "<" of the element carrying ``marker``."""
    return doc.rfind(b"<", 0, doc.index(marker.encode()))


def _grid(headers: Sequence[str], rows: Sequence[tuple[str, Sequence[str]]], stub: str = "") -> str:
    """Builds a table: ``_grid(["2024"], [("Revenue", ["$1"])], stub="(in millions)")``."""
    head = f"<tr><td>{stub}</td>" + "".join(f"<td>{h}</td>" for h in headers) + "</tr>"
    body = "".join(
        f"<tr><td>{label}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        for label, cells in rows
    )
    return f"<table>{head}{body}</table>"


def _heading(item: str, title: str) -> str:
    return f"<div>Item {item}. {title}</div>"


def _para(token: str) -> str:
    return f"<p>{token}{NOT_A_CONTENTS_BLOCK}</p>"


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The document helpers, as one namespace."""
    return SimpleNamespace(
        doc=_doc,
        stripped=_strip,
        chunks=_chunks,
        at=_at,
        grid=_grid,
        heading=_heading,
        para=_para,
        accession=ACCESSION,
    )


@pytest.fixture(scope="session")
def source() -> Callable[[str], str]:
    """Reads a module's source as text, by repo-relative path."""
    return lambda relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
