# 10-K section detection for the HTML flow (docs/specs/html10k-strip-sections.md).
# Finds Items 1, 1A, 7, 7A, 8 and the notes in ix_strip_html output, from the table of contents
# when the filing has one and from headings when it does not.
#
# Offsets come from lining up two views of the same bytes. ix_strip_html output has no comments
# and escapes every "<" in text and attribute values, so the n-th "<name" in the bytes is the n-th
# element in document order.

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import NamedTuple

import lxml.etree as etree

from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import _RE_PAGE_CELL

# Requirement 12. Matched in full against text from _comparable.
_TITLES = {
    "item_1": re.compile(r"business|business and properties|description of business"),
    "item_1a": re.compile(r"risk factors"),
    "item_7": re.compile(r"management's discussion and analysis(?: of .*)?"),
    "item_7a": re.compile(r"quantitative and qualitative disclosures? about market risks?"),
    "item_8": re.compile(r"(?:consolidated )?financial statements(?: and supplementary data)?"),
    "notes": re.compile(
        r"(?:combined )?notes to (?:the )?(?:consolidated )?(?:combined )?financial statements"
    ),
}
_ITEM_OF_KEY = {"item_1": "1", "item_1a": "1a", "item_7": "7", "item_7a": "7a", "item_8": "8"}
_KEY_OF_ITEM = {item: key for key, item in _ITEM_OF_KEY.items()}
_NOTES = "notes"
_ITEM_15 = "15"
_EXHIBITS = "exhibits"

_BOUNDARY_TITLES = (
    "unresolved staff comments",
    "cybersecurity",
    "properties",
    "legal proceedings",
    "mine safety disclosures",
    "market for",
    "[reserved]",
    "reserved",
    "selected financial data",
    "changes in and disagreements",
    "controls and procedures",
    "other information",
    "disclosure regarding foreign jurisdictions",
    "directors",
    "executive compensation",
    "security ownership",
    "certain relationships",
    "principal account",
    "exhibits",
    "form 10-k summary",
    "signature",
)
_SIGNATURE_BLOCKS = ("signature", "signatures")

_PART = r"(?:PART\s+[IVX]+\b[\s,.:;\-–—]*)?"
_ITEM = r"ITEMS?\s+(\d+)([A-Z]?)(?![A-Z0-9])"
# An item number at the start of a ToC label (Requirement 14).
_LABEL_ITEM = re.compile(_PART + _ITEM, re.IGNORECASE)
# The start of a heading block, up to its rest (Requirement 15).
_HEADING_ITEM = re.compile(
    _PART + _ITEM + r"(?:\s+AND\s+\d+[A-Z]?)?\s*(?:[.:]|[-–—]+)?\s*(.*)", re.IGNORECASE
)

_PAGE_REFERENCE = r"\d+(?:\s*[-–—]\s*\d+)?|F-\d+(?:\s*[-–—]\s*F-\d+)?"
_PAGE_CELL_TEXT = re.compile(_PAGE_REFERENCE, re.IGNORECASE)
# What makes a block a line of an index and not a heading, looked for in the block's rest.
_TRAILING_REFERENCE = re.compile(
    rf"(?:^|\s)(?:(?:{_PAGE_REFERENCE})|Pages?\s+\d.*|Not applicable)\.?$", re.IGNORECASE
)

_MAX_HEADING_CHARS = 200
_MIN_CONTENTS_RUN = 5
_MIN_TEXT_BETWEEN_HEADINGS = 20

_START_TAG = re.compile(rb"<[A-Za-z_]")
_BLOCK_TAGS = frozenset({"p", "div", "h1", "h2", "h3", "h4", "h5", "h6"})


class Html10kSection(NamedTuple):
    """One detected section, as a half-open UTF-8 byte range into the stripped document.

    Attributes:
        key: One of ``item_1``, ``item_1a``, ``item_7``, ``item_7a``, ``item_8``, ``notes``, and
            ``item_15`` from ``detect_chunk_sections``.
        label: The ToC entry's label, or the heading block's text.
        source: ``"toc"`` or ``"heading"``.
        char_start: Offset of the opening ``<`` of the target element or heading block.
        char_end: Offset of the first boundary after ``char_start``, or the document's length.
    """

    key: str
    label: str
    source: str
    char_start: int
    char_end: int


class _Entry(NamedTuple):
    """A place in the document that names a section, from a ToC link or a heading block."""

    label: str
    start: int


class _Candidate(NamedTuple):
    """An item candidate of Requirement 15."""

    item: str
    block: etree._Element
    entry: _Entry


@dataclass(frozen=True)
class _Document:
    """The stripped document, with what the two detection paths look up."""

    root: etree._Element
    elements: list[etree._Element]
    offsets: dict[etree._Element, int]
    text: str
    text_starts: dict[etree._Element, int]
    text_ends: dict[etree._Element, int]
    length: int

    def text_of(self, element: etree._Element) -> str:
        """Return an element's visible text, collapsed, without its tail."""
        return _collapse(self.text[self.text_starts[element]:self.text_ends[element]])

    def cells_of(self, row: etree._Element) -> list[str]:
        """Return the collapsed text of each cell of a row."""
        return [self.text_of(cell) for cell in row]


def _read(stripped_html: bytes) -> _Document:
    """Parse the document and index its elements by byte and text position.

    Raises:
        ValueError: The input is not ``ix_strip_html`` output, so offsets cannot be trusted.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    root = etree.fromstring(stripped_html, parser)

    elements: list[etree._Element] = []
    pieces: list[str] = []
    text_starts: dict[etree._Element, int] = {}
    text_ends: dict[etree._Element, int] = {}
    position = 0
    for event, element in etree.iterwalk(root, events=("start", "end")):
        run = element.text if event == "start" else element.tail
        if event == "start":
            elements.append(element)
            text_starts[element] = position
        else:
            text_ends[element] = position
        if run:
            pieces.append(run)
            position += len(run)

    starts = [match.start() for match in _START_TAG.finditer(stripped_html)]
    if len(starts) != len(elements):
        raise ValueError(
            f"{len(starts)} start tags for {len(elements)} elements: not ix_strip_html output"
        )
    return _Document(
        root=root,
        elements=elements,
        offsets=dict(zip(elements, starts, strict=True)),
        text="".join(pieces),
        text_starts=text_starts,
        text_ends=text_ends,
        length=len(stripped_html),
    )


def _local(element: etree._Element) -> str:
    return element.tag.rpartition("}")[2]


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _comparable(text: str) -> str:
    """Reduce text to the form titles are compared in (Requirement 12)."""
    collapsed = _collapse(text).lower().replace("’", "'").replace("&", "and")
    return collapsed.rstrip(".: ")


def _item_id(number: str, letter: str) -> str:
    return f"{number}{letter.lower()}"


def _is_page_cell(cell: str) -> bool:
    return bool(_RE_PAGE_CELL.match(cell))


def _toc_label(document: _Document, link: etree._Element) -> str | None:
    """Return a link's label, or ``None`` when it belongs to a cross-reference index."""
    row = next(link.iterancestors("{*}tr"), None)
    if row is None:
        text = document.text_of(link)
        return None if _is_page_cell(text) else text
    cells = document.cells_of(row)
    if any(_is_page_cell(cell) for cell in cells):
        return None
    return " ".join(c for c in cells if c and not _PAGE_CELL_TEXT.fullmatch(c))


def _toc_id(label: str) -> str | None:
    """Return what a ToC label names: an item, ``notes``, a boundary title, or ``None``."""
    numbered = _LABEL_ITEM.match(label)
    if numbered:
        return _item_id(numbered.group(1), numbered.group(2))
    comparable = _comparable(label)
    for key, title in _TITLES.items():
        if title.fullmatch(comparable):
            return _ITEM_OF_KEY.get(key, _NOTES)
    return next((t for t in _BOUNDARY_TITLES if comparable.startswith(t)), None)


def _toc_entries(document: _Document) -> dict[str, _Entry]:
    """Return the first ToC entry of each item, notes and boundary title (Requirements 13, 14)."""
    targets: dict[str, etree._Element] = {}
    for element in document.elements:
        for attribute in ("id", "name"):
            value = element.get(attribute)
            if value:
                targets.setdefault(value, element)

    entries: dict[str, _Entry] = {}
    for element in document.elements:
        href = element.get("href")
        if not href or not href.startswith("#") or href[1:] not in targets:
            continue
        label = _toc_label(document, element)
        named = _toc_id(label) if label else None
        if label and named and named not in entries:
            entries[named] = _Entry(label, document.offsets[targets[href[1:]]])
    return entries


def _blocks(elements: list[etree._Element]) -> list[etree._Element]:
    """Return the blocks of Requirement 15, in document order."""
    holds_block: set[etree._Element] = set()
    for element in reversed(elements):
        parent = element.getparent()
        if parent is not None and (
            element in holds_block or _local(element) in _BLOCK_TAGS or _local(element) == "tr"
        ):
            holds_block.add(parent)

    under_row: set[etree._Element] = set()
    for element in elements:
        parent = element.getparent()
        if parent is not None and (parent in under_row or _local(parent) == "tr"):
            under_row.add(element)

    return [
        element
        for element in elements
        if _local(element) == "tr"
        or (
            _local(element) in _BLOCK_TAGS
            and element not in holds_block
            and element not in under_row
        )
    ]


def _block_text(document: _Document, block: etree._Element) -> str:
    if _local(block) == "tr":
        return " ".join(cell for cell in document.cells_of(block) if cell)
    return document.text_of(block)


def _index_tables(document: _Document) -> set[etree._Element]:
    """Return the tables with a ``Page <n>`` cell in any row: cross-reference indexes."""
    tables: set[etree._Element] = set()
    for element in document.elements:
        if _local(element) == "tr" and any(map(_is_page_cell, document.cells_of(element))):
            table = next(element.iterancestors("{*}table"), None)
            if table is not None:
                tables.add(table)
    return tables


def _heading_item(text: str) -> str | None:
    """Return the item a block's text is a heading for, or ``None`` (Requirement 15)."""
    match = _HEADING_ITEM.fullmatch(text)
    if match is None:
        return None
    item, rest = _item_id(match.group(1), match.group(2)), match.group(3)
    if not rest:
        return item
    if _TRAILING_REFERENCE.search(rest):
        return None
    key = _KEY_OF_ITEM.get(item)
    if key is not None:
        return item if _TITLES[key].fullmatch(_comparable(rest)) else None
    return item if rest[0].isupper() or rest[0] == "[" else None


def _without_contents_blocks(
    candidates: list[_Candidate], document: _Document
) -> list[_Candidate]:
    """Drop runs of candidates that form an unlinked table of contents (Requirement 16)."""
    kept: list[_Candidate] = []
    start = 0
    while start < len(candidates):
        end = start + 1
        seen = {candidates[start].item}
        while end < len(candidates) and candidates[end].item not in seen:
            between = document.text[
                document.text_ends[candidates[end - 1].block]:
                document.text_starts[candidates[end].block]
            ]
            if len(_collapse(between)) >= _MIN_TEXT_BETWEEN_HEADINGS:
                break
            seen.add(candidates[end].item)
            end += 1
        if end - start < _MIN_CONTENTS_RUN:
            kept.extend(candidates[start:end])
        start = end
    return kept


class _Headings(NamedTuple):
    """What the heading scan found."""

    items: dict[str, _Entry]
    notes: list[_Entry]
    signatures: list[int]


def _headings(document: _Document) -> _Headings:
    """Scan the blocks for item headings, notes headings and signature lines."""
    index_tables = _index_tables(document)
    candidates: list[_Candidate] = []
    notes: list[_Entry] = []
    signatures: list[int] = []

    for block in _blocks(document.elements):
        text = _block_text(document, block)
        if not text:
            continue
        comparable = _comparable(text)
        start = document.offsets[block]
        if comparable in _SIGNATURE_BLOCKS:
            signatures.append(start)
        if len(text) > _MAX_HEADING_CHARS or "(continued)" in comparable:
            continue
        if _local(block) == "tr" and next(block.iterancestors("{*}table"), None) in index_tables:
            continue
        if _TITLES[_NOTES].fullmatch(comparable):
            notes.append(_Entry(text, start))
        item = _heading_item(text)
        if item is not None:
            candidates.append(_Candidate(item, block, _Entry(text, start)))

    items: dict[str, _Entry] = {}
    for candidate in _without_contents_blocks(candidates, document):
        items.setdefault(candidate.item, candidate.entry)
    return _Headings(items, notes, signatures)


class _Scan(NamedTuple):
    """What both section functions read from a document."""

    length: int
    toc: dict[str, _Entry]
    headings: _Headings
    boundaries: list[int]
    sections: list[Html10kSection]

    def boundary_after(self, start: int) -> int:
        """Return the first boundary after ``start``, or the document's length."""
        following = bisect.bisect_right(self.boundaries, start)
        return self.boundaries[following] if following < len(self.boundaries) else self.length


def _scan(stripped_html: bytes) -> _Scan:
    """Read a stripped document and find its sections.

    Raises:
        ValueError: The input is not ``ix_strip_html`` output.
    """
    document = _read(stripped_html)
    toc = _toc_entries(document)
    headings = _headings(document)

    found: dict[str, tuple[_Entry, str]] = {}
    for key, item in _ITEM_OF_KEY.items():
        if item in toc:
            found[key] = (toc[item], "toc")
        elif item in headings.items:
            found[key] = (headings.items[item], "heading")

    if _NOTES in toc:
        found[_NOTES] = (toc[_NOTES], "toc")
    else:
        floor = found["item_8"][0].start if "item_8" in found else 0
        first_notes = next((n for n in headings.notes if n.start >= floor), None)
        if first_notes is not None:
            found[_NOTES] = (first_notes, "heading")

    boundaries = sorted(
        {entry.start for named, entry in toc.items() if named != _NOTES}
        | {entry.start for item, entry in headings.items.items() if item not in toc}
        | set(headings.signatures)
    )

    scan = _Scan(document.length, toc, headings, boundaries, [])
    for key, (entry, source) in found.items():
        end = scan.boundary_after(entry.start)
        scan.sections.append(Html10kSection(key, entry.label, source, entry.start, end))
    scan.sections.sort(key=lambda section: section.char_start)
    return scan


def detect_sections(stripped_html: bytes) -> list[Html10kSection]:
    """Find the 10-K sections of a stripped document.

    Args:
        stripped_html: Output of ``ix_strip_html``.

    Returns:
        The sections found, sorted by ``char_start``, at most one per key. Empty when none
        is found.

    Raises:
        ValueError: The input is not ``ix_strip_html`` output.
    """
    return _scan(stripped_html).sections


def _item_15(scan: _Scan) -> Html10kSection | None:
    """Return the section that holds a stub filer's statements, if the filing names Item 15.

    A stub Item 8 prints the statements after Item 15, in some filers after Item 16 and the
    Signatures too, so the section runs to the end of the notes when they come after it.

    Args:
        scan: The document's scan.

    Returns:
        The section, or ``None``.
    """
    if _ITEM_15 in scan.toc:
        entry, source = scan.toc[_ITEM_15], "toc"
    elif _ITEM_15 in scan.headings.items:
        entry, source = scan.headings.items[_ITEM_15], "heading"
    elif _EXHIBITS in scan.toc:
        entry, source = scan.toc[_EXHIBITS], "toc"
    else:
        return None
    notes = next((s for s in scan.sections if s.key == _NOTES), None)
    if notes is not None and notes.char_start > entry.start:
        end = notes.char_end
    else:
        end = scan.boundary_after(entry.start)
    return Html10kSection("item_15", entry.label, source, entry.start, end)


def detect_chunk_sections(stripped_html: bytes) -> list[Html10kSection]:
    """Find the sections chunks are tagged with: ``detect_sections`` plus ``item_15``.

    Args:
        stripped_html: Output of ``ix_strip_html``.

    Returns:
        What ``detect_sections`` returns plus ``item_15`` when found, sorted by ``char_start``.

    Raises:
        ValueError: The input is not ``ix_strip_html`` output.
    """
    scan = _scan(stripped_html)
    item_15 = _item_15(scan)
    found = scan.sections if item_15 is None else [*scan.sections, item_15]
    return sorted(found, key=lambda section: section.char_start)
