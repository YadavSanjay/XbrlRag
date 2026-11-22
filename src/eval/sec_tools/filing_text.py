"""Filing text: canonical (X)HTML reduced to plain text, one block per line.

A pure function shared by the long-context arms and the ``frontier_sec_tools`` arm, which
is why it lives here, outside any arm that reads our filing store
(docs/specs/frontier-sec-tools.md, requirement 13). ``src.eval.systems.long_context`` keeps
importing the name.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree  # type: ignore[import-untyped]  # lxml ships no stubs
from lxml import html as lxml_html  # type: ignore[import-untyped]  # lxml ships no stubs

_DROPPED = frozenset({"script", "style"})
# Elements that start (and end) a line. ``tr`` and ``br`` are handled on their own.
_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "caption", "center", "dd",
        "div", "dl", "dt", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "head", "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre",
        "section", "table", "tbody", "td", "tfoot", "th", "thead", "title", "ul",
    }
)
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def _names(el: Any) -> tuple[str, str]:
    """The element's (namespace URI or prefix, lower-case local name).

    Handles both parsers: the XML parser gives ``{uri}local`` and the HTML fallback
    keeps a prefixed name such as ``ix:header``.
    """
    tag = el.tag
    if not isinstance(tag, str):  # a comment or processing instruction
        return "", ""
    if tag.startswith("{"):
        uri, _, local = tag[1:].partition("}")
        return uri, local.lower()
    prefix, sep, local = tag.rpartition(":")
    return (prefix, local.lower()) if sep else ("", tag.lower())


def _is_dropped(el: Any) -> bool:
    """True for ``script``, ``style`` and the hidden inline-XBRL header."""
    ns, local = _names(el)
    if local in _DROPPED:
        return True
    return local == "header" and ("inlinexbrl" in ns.lower() or ns.lower() == "ix")


def _is_html(el: Any, local: str) -> bool:
    """True when *el* is an (X)HTML element named *local* (not, e.g., ``ix:header``)."""
    ns, name = _names(el)
    return name == local and (ns == "" or "xhtml" in ns.lower())


class _Lines:
    """Accumulates inline text and emits collapsed, non-empty lines."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._parts: list[str] = []

    def add(self, text: str | None) -> None:
        if text:
            self._parts.append(text)

    def flush(self) -> None:
        line = _WHITESPACE.sub(" ", "".join(self._parts)).strip()
        self._parts.clear()
        if line:
            self.lines.append(line)

    def add_line(self, line: str) -> None:
        self.flush()
        if line:
            self.lines.append(line)


def _inline_text(el: Any) -> str:
    """All visible text under *el* on one line (blocks and breaks become spaces)."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if _is_dropped(node):
            return
        _, local = _names(node)
        if local and (local in _BLOCKS or local in ("br", "tr")):
            parts.append(" ")
        if local:
            parts.append(node.text or "")
            for child in node:
                walk(child)
                parts.append(child.tail or "")
        if local and (local in _BLOCKS or local == "tr"):
            parts.append(" ")

    walk(el)
    return _WHITESPACE.sub(" ", "".join(parts)).strip()


def _walk(el: Any, out: _Lines) -> None:
    """Appends the text of *el* (not its tail) to *out*."""
    if _is_dropped(el):
        return
    _, local = _names(el)
    if not local:  # comment / processing instruction: no text of its own
        return
    if _is_html(el, "br"):
        out.flush()
        return
    if _is_html(el, "tr"):
        cells = [_inline_text(child) for child in el if not _is_dropped(child)]
        out.add_line(" | ".join(cell for cell in cells if cell))
        return
    block = local in _BLOCKS and _is_html(el, local)
    if block:
        out.flush()
    out.add(el.text)
    for child in el:
        _walk(child, out)
        out.add(child.tail)
    if block:
        out.flush()


def _parse(html: str) -> Any:
    """Parses canonical XHTML as XML; falls back to the forgiving HTML parser."""
    body = _XML_DECLARATION.sub("", html, count=1).encode("utf-8")
    try:
        parser = etree.XMLParser(
            huge_tree=True, resolve_entities=False, no_network=True, remove_comments=True
        )
        return etree.fromstring(body, parser=parser)
    except etree.XMLSyntaxError:
        return lxml_html.fromstring(body, parser=lxml_html.HTMLParser(encoding="utf-8"))


def filing_text(html: str) -> str:
    """Reduces a canonical filing's HTML to plain text, one block per line.

    The hidden inline-XBRL header (``ix:header``), ``script`` and ``style`` are
    dropped. Each paragraph, heading, list item, division, table row and line break
    starts a new line; a table row's non-empty cells are joined by ``" | "``.
    Whitespace inside a line collapses to single spaces, and empty lines are dropped.
    A leading ``<?xml ...?>`` declaration is accepted.

    Args:
        html: The canonical HTML of a filing.

    Returns:
        The visible text, its lines joined by ``"\\n"`` (no trailing newline).
    """
    root = _parse(html)
    out = _Lines()
    _walk(root, out)
    out.flush()
    return "\n".join(out.lines)
