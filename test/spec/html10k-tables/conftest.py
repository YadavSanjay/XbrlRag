"""Fixtures for the html10k-tables spec tests.

Spec: docs/specs/html10k-tables.md. The only input is the committed HTML snippets next to this
file; each says in its first line where it comes from. Session-scoped so hypothesis tests can
use them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]
TABLES_PY = REPO_ROOT / "src" / "ingest" / "html10k" / "tables.py"


def _load(name: str) -> str:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text.split("-->", 1)[1].strip()


@pytest.fixture(scope="session")
def load() -> Callable[[str], str]:
    """Reads a snippet, without its provenance comment."""
    return _load


@pytest.fixture(scope="session")
def tables_source() -> str:
    """The source of the table module, as text."""
    return TABLES_PY.read_text(encoding="utf-8")


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


def _row(table, label: str, nth: int = 0):
    return [r for r in table.rows if r.label == label][nth]


@pytest.fixture(scope="session")
def row() -> Callable[..., object]:
    """Finds the ``nth`` body row with this label; labels repeat, as ``Basic`` does."""
    return _row


def _double_colspans(html: str) -> str:
    def widen(match: re.Match[str]) -> str:
        attrs = match.group(1)
        span = re.search(r'colspan="(\d+)"', attrs)
        if span:
            return "<td" + attrs.replace(span.group(0), f'colspan="{int(span.group(1)) * 2}"') + ">"
        return f'<td{attrs} colspan="2">'

    return re.sub(r"<td([^>]*)>", widen, html)


@pytest.fixture(scope="session")
def double_colspans() -> Callable[[str], str]:
    """Doubles every cell's width: the same table in a finer layout grid."""
    return _double_colspans


def _wrap_tags(html: str, scale: str, sign: str, unit: str) -> str:
    """Wraps each number in a ``<td>`` in an inline tag whose attributes are unrelated to it."""
    attrs = f'name="us-gaap:Assets" contextRef="c1" unitRef="{unit}" scale="{scale}" {sign}'

    def wrap(match: re.Match[str]) -> str:
        head, number, tail = match.group(1), match.group(2), match.group(3)
        return f"{head}<ix:nonFraction {attrs}>{number}</ix:nonFraction>{tail}"

    plain = html.replace("&#160;", " ").replace("&#8226;", "•")  # no digits in entities
    tagged = re.sub(r"(<td[^>]*>[^<]*?)(\d[\d,.]*\d|\d)([^<]*</td>)", wrap, plain)
    return tagged.replace("<table", '<table xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"')


@pytest.fixture(scope="session")
def wrap_tags() -> Callable[[str, str, str, str], str]:
    """Adds tagging markup with false attributes to tag-free HTML."""
    return _wrap_tags
