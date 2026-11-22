"""Fixtures for the html10k-tables-scale spec tests.

Spec: docs/specs/html10k-tables-scale.md. The only input is the committed HTML snippets next to
this file; each says in its first line where it comes from. Session-scoped so hypothesis tests can
use them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> str:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text.split("-->", 1)[1].strip()


@pytest.fixture(scope="session")
def load() -> Callable[[str], str]:
    """Reads a snippet, without its provenance comment."""
    return _load


def _mini(header: Sequence[str], rows: Sequence[tuple[str, Sequence[str]]], stub: str = "") -> str:
    head = f"<tr><td>{stub}</td>" + "".join(f"<td>{h}</td>" for h in header) + "</tr>"
    body = "".join(
        f"<tr><td>{label}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        for label, cells in rows
    )
    return f"<table>{head}{body}</table>"


@pytest.fixture(scope="session")
def mini() -> Callable[..., str]:
    """Builds a small table: ``mini(["2024"], [("Label", ["1,234"])], stub="(in millions)")``."""
    return _mini


def _doc(*parts: str) -> str:
    """Joins blocks and tables into a document; a part not starting with ``<`` is a block."""
    blocks = [p if p.startswith("<") else f"<div>{p}</div>" for p in parts]
    return f"<html><body>{''.join(blocks)}</body></html>"


@pytest.fixture(scope="session")
def doc() -> Callable[..., str]:
    """Builds a document from block texts and table HTML, in order."""
    return _doc


def _row(table, label: str, nth: int = 0):
    return [r for r in table.rows if r.label == label][nth]


@pytest.fixture(scope="session")
def row() -> Callable[..., object]:
    """Finds the ``nth`` body row with this label; labels repeat, as ``Basic`` does."""
    return _row


def _scales(row_) -> list[int]:
    return [c.scale for c in row_.cells]


@pytest.fixture(scope="session")
def scales() -> Callable[[object], list[int]]:
    """The scale of each cell of a row."""
    return _scales
