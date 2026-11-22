# The chunk stage of the HTML 10-K flow: ix_strip_html output into prose and table chunks.
#
# The walk and the close rules are those of the XBRL flow's chunker, over other bytes. A table's
# text is what the filing prints: every number is the parser's printed text, so a scale error of
# the parser cannot reach the LLM.
#
# Spec: docs/specs/html10k-chunks.md.

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, NamedTuple

from src import CHUNK_SIZE_IN_CHARS
from src.ingest.html10k.sections import (
    Html10kSection,
    _Document,
    _local,
    _read,
    detect_chunk_sections,
)
from src.ingest.html10k.tables import (
    PrintedRow,
    PrintedTable,
    note_statement,
    parse_printed_tables,
)

_CHUNK_ID_NAMESPACE = uuid.UUID("5d1c3f0e-7a42-4e6b-9b3d-2c8e1f6a4d70")
_PROSE_TAGS = frozenset(
    {
        "div", "p", "li", "td", "th", "dd", "dt", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article",
    }
)  # fmt: skip
_TABLE = "table"
_NO_SECTION = "no_section"
_MAX_CAPTION_CHARS = 300
_MAX_HEADER_CHARS = 80  # a column's name; the longest of the dev filers' text tables is 67
_SIGNED_RE = re.compile(r"/s/", re.IGNORECASE)
_PAGE_HEADER_RE = re.compile(r"(?:pages?|part)\b", re.IGNORECASE)
_MIN_INDEX_ENTRIES = 3
_MAX_PAGE = 1000


class Html10kChunk(NamedTuple):
    """One chunk of a stripped 10-K.

    Attributes:
        chunk_id: The chunk's id, a UUID string.
        accession_no: The filing's accession number.
        text: The chunk's text.
        kind: ``"prose"`` or ``"table"``.
        section_key: The innermost section, or ``no_section``.
        section_keys: Every section holding the chunk, outermost first.
        section_label: The innermost section's label, empty for no section.
        char_start: UTF-8 byte offset of the chunk's start in the stripped document.
        char_end: UTF-8 byte offset of the next chunk's start, or the document's length.
        table_index: The ``<table>`` ordinal of a data table's chunk, ``None`` for prose.
        part: The 0-based part of a split table, 0 otherwise.
        parts: How many parts the table has, 1 otherwise.
        prev_chunk_id: The chunk before, or ``None``.
        next_chunk_id: The chunk after, or ``None``.
    """

    chunk_id: str
    accession_no: str
    text: str
    kind: str
    section_key: str
    section_keys: tuple[str, ...]
    section_label: str
    char_start: int
    char_end: int
    table_index: int | None
    part: int
    parts: int
    prev_chunk_id: str | None
    next_chunk_id: str | None


class _Place(NamedTuple):
    """The sections a block is in."""

    keys: tuple[str, ...]
    label: str


class _Block(NamedTuple):
    """A ``<table>`` or a leaf prose element of the walk."""

    element: Any
    start: int
    position: int  # what decides the block's section


class _Pending(NamedTuple):
    """A chunk before its range is known; a table has one text per part."""

    start: int
    place: _Place
    kind: str
    texts: tuple[str, ...]
    table_index: int | None


def _own_rows_start(table: Any, document: _Document) -> int:
    """Return where a table's first row starts, or the table's start when it has none."""
    for row in table.iter("{*}tr"):
        if next(row.iterancestors("{*}table"), None) is table:
            return document.offsets[row]
    return document.offsets[table]


def _walk(document: _Document) -> Iterator[_Block]:
    """Yield every table and every leaf prose element, in document order."""
    holds_block: set[Any] = set()
    for element in reversed(document.elements):
        parent = element.getparent()
        if parent is not None and (
            element in holds_block or _local(element) in _PROSE_TAGS or _local(element) == _TABLE
        ):
            holds_block.add(parent)

    consumed: set[Any] = set()  # what a table or a leaf has taken with it
    for element in document.elements:
        parent = element.getparent()
        if parent in consumed:
            consumed.add(element)
        local = _local(element)
        if local == _TABLE:
            consumed.add(element)
            start = document.offsets[element]
            yield _Block(element, start, _own_rows_start(element, document))
        elif element not in consumed and local in _PROSE_TAGS and element not in holds_block:
            consumed.add(element)
            start = document.offsets[element]
            yield _Block(element, start, start)


def _runs(element: Any) -> Iterator[str]:
    """Yield the text runs under an element, leaving out tables nested in it."""
    if element.text:
        yield element.text
    for child in element:
        if _local(child) != _TABLE:
            yield from _runs(child)
        if child.tail:
            yield child.tail


def _block_text(element: Any) -> str:
    return " ".join(run.strip() for run in _runs(element) if run.strip())


def _place_of(position: int, sections: Sequence[Html10kSection]) -> _Place:
    holding = [s for s in sections if s.char_start <= position < s.char_end]
    if not holding:
        return _Place((_NO_SECTION,), "")
    return _Place(tuple(s.key for s in holding), holding[-1].label)


def _reads_as_header(text: str) -> bool:
    """Tell a column's name from a first row of body text that the parser took for a header."""
    return (
        text[0].isalpha()
        and len(text) <= _MAX_HEADER_CHARS
        and not text.endswith(".")
        and not _SIGNED_RE.match(text)
    )


def _is_page_index(printed: PrintedTable) -> bool:
    """Tell an index from a table of data: its only numbers are rising page numbers.

    Args:
        printed: The table.

    Returns:
        True when one column holds every number, under no header or one that opens with ``Page``
        or, in a table of contents, ``Part``, and they are at least three whole numbers that
        never fall.
    """
    table = printed.table
    filled = [
        (position, cell)
        for row in table.rows
        for position, cell in enumerate(row.cells)
        if cell.value is not None or cell.is_nil
    ]
    columns = {position for position, _ in filled}
    if len(columns) != 1 or len(filled) < _MIN_INDEX_ENTRIES:
        return False
    header = " ".join(table.columns[columns.pop()].headers)
    pages = [cell.value for _, cell in filled if cell.value is not None]
    return (
        (not header or _PAGE_HEADER_RE.match(header) is not None)
        and len(pages) == len(filled)
        and all(page == int(page) < _MAX_PAGE for page in pages)
        and all(cell.currency is None and not cell.is_percent for _, cell in filled)
        and pages == sorted(pages)
    )


def _is_layout(printed: PrintedTable) -> bool:
    """Tell a table that is really text from one that holds data.

    Args:
        printed: The table.

    Returns:
        True for a table with no body row, a table of text with no real header, and an index.
    """
    table = printed.table
    if not table.rows:
        return True
    if any(cell.value is not None or cell.is_nil for row in table.rows for cell in row.cells):
        return _is_page_index(printed)
    named = [text for text in (printed.stub, *printed.headers) if text]
    has_column_header = any(printed.headers)
    return not has_column_header or not all(map(_reads_as_header, named))


def _shortened(text: str) -> str:
    """Keep a long caption's end, from the first word that fits whole."""
    if len(text) <= _MAX_CAPTION_CHARS:
        return text
    kept = text[-_MAX_CAPTION_CHARS:]
    if not text[-_MAX_CAPTION_CHARS - 1].isspace():
        kept = kept.partition(" ")[2] or kept
    return "…" + kept.lstrip()


def _title_and_note(printed: PrintedTable) -> tuple[str, str]:
    """Return the ``Table:`` and ``Note:`` text, keeping a scale statement a cut would lose.

    Args:
        printed: The table.

    Returns:
        The title and the note, each within the caption limit apart from a kept statement.
    """
    title = _shortened(printed.title)
    if not printed.note:
        statement = note_statement(printed.title)
        return title, "" if statement in title else statement
    note = _shortened(printed.note)
    statement = note_statement(printed.note)
    return title, note if statement in note else f"{statement} {note}"


def _escaped(text: str) -> str:
    return text.replace("|", "\\|")


def _row_line(cells: Iterable[str]) -> str:
    return "| " + " | ".join(_escaped(cell) for cell in cells) + " |"


def _row_groups(rows: Sequence[PrintedRow]) -> list[tuple[list[str], list[str]]]:
    """Return each row group as its group-row lines and the lines of the rows under it."""
    groups: list[tuple[list[str], list[str]]] = []
    for row in rows:
        line = _row_line(row.cells)
        if row.is_group:
            if groups and not groups[-1][1]:
                groups[-1][0].append(line)
            else:
                groups.append(([line], []))
        elif groups:
            groups[-1][1].append(line)
        else:
            groups.append(([], [line]))
    return groups


def _split_rows(
    groups: Sequence[tuple[list[str], list[str]]], prelude: Sequence[str], limit: int
) -> list[list[str]]:
    """Fill parts with row groups, splitting a group only when it fits in no part of its own.

    Args:
        groups: The row groups.
        prelude: The lines every part starts with.
        limit: The largest text of a part, in characters.

    Returns:
        The body lines of each part.
    """

    def fits(lines: Sequence[str]) -> bool:
        return len("\n".join([*prelude, *lines])) <= limit

    parts: list[list[str]] = []
    current: list[str] = []
    for heads, rows in groups:
        whole = heads + rows
        if current and fits(current + whole):
            current += whole
            continue
        if current:
            parts.append(current)
        if fits(whole):
            current = whole
            continue
        current = list(heads)
        for row in rows:
            if len(current) > len(heads) and not fits([*current, row]):
                parts.append(current)
                current = list(heads)
            current.append(row)
    if current:
        parts.append(current)
    return parts


def _table_texts(printed: PrintedTable, place: _Place, table_max_chars: int) -> tuple[str, ...]:
    """Render a data table as the text of one chunk, or of one chunk per part.

    Args:
        printed: The table and what is printed around it.
        place: The sections it is in.
        table_max_chars: The text size above which it splits.

    Returns:
        The text of each part.
    """
    title, note = _title_and_note(printed)
    prelude = [f"Section: {_escaped(place.label) if place.label else 'none'}"]
    if title:
        prelude.append(f"Table: {_escaped(title)}")
    if note:
        prelude.append(f"Note: {_escaped(note)}")
    prelude.append(_row_line([printed.stub, *printed.headers]))
    prelude.append(_row_line(["---"] * (len(printed.headers) + 1)))

    groups = _row_groups(printed.rows)
    every_row = [line for heads, rows in groups for line in heads + rows]
    if len("\n".join([*prelude, *every_row])) <= table_max_chars:
        return ("\n".join([*prelude, *every_row]),)
    return tuple(
        "\n".join([*prelude, *part]) for part in _split_rows(groups, prelude, table_max_chars)
    )


def _pending_chunks(
    document: _Document,
    sections: Sequence[Html10kSection],
    tables: Sequence[PrintedTable],
    chunk_size_chars: int,
    table_max_chars: int,
) -> list[_Pending]:
    """Walk the document and apply the close rules.

    Args:
        document: The stripped document.
        sections: Its sections.
        tables: Its tables, in document order.
        chunk_size_chars: The prose size at which a chunk closes.
        table_max_chars: The text size above which a data table splits.

    Returns:
        The chunks, in document order, before ranges and ids.

    Raises:
        ValueError: The walk and the parser disagree on how many tables the document has.
    """
    pending: list[_Pending] = []
    texts: list[str] = []
    opened: tuple[int, _Place] | None = None

    def close() -> None:
        nonlocal opened
        if opened is not None:
            pending.append(_Pending(*opened, "prose", (" ".join(texts),), None))
        texts.clear()
        opened = None

    ordinal = 0
    for block in _walk(document):
        place = _place_of(block.position, sections)
        if _local(block.element) == _TABLE:
            if ordinal >= len(tables):
                raise ValueError(f"more than the {len(tables)} tables the parser read")
            printed = tables[ordinal]
            ordinal += 1
            if not _is_layout(printed):
                close()
                parts = _table_texts(printed, place, table_max_chars)
                pending.append(
                    _Pending(block.start, place, "table", parts, printed.table.table_index)
                )
                continue
        text = _block_text(block.element)
        if not text:
            continue
        if opened is not None and opened[1].keys != place.keys:
            close()
        if opened is None:
            opened = (block.start, place)
        texts.append(text)
        if sum(map(len, texts)) >= chunk_size_chars:
            close()
    close()
    if ordinal != len(tables):
        raise ValueError(f"{ordinal} tables in the walk, {len(tables)} parsed")
    return pending


def html10k_chunk_id(accession_no: str, start: int, end: int, part: int) -> str:
    """Return a chunk's id, the one place it is formed.

    Args:
        accession_no: The filing's accession number.
        start: The chunk's first byte in the stripped document.
        end: The byte after the chunk.
        part: The 0-based part of a split table, 0 otherwise.

    Returns:
        The id, a UUID string.
    """
    return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, f"{accession_no}:{start}:{end}:{part}"))


def chunk_filing(
    stripped_html: bytes,
    accession_no: str,
    *,
    chunk_size_chars: int = CHUNK_SIZE_IN_CHARS,
    table_max_chars: int = 2000,
) -> list[Html10kChunk]:
    """Chunk one stripped filing.

    Args:
        stripped_html: Output of ``ix_strip_html``.
        accession_no: The filing's accession number; it seeds the chunk ids.
        chunk_size_chars: The prose size at which a chunk closes.
        table_max_chars: The text size above which a data table splits by row groups.

    Returns:
        The chunks in document order.

    Raises:
        ValueError: The input is not ``ix_strip_html`` output.
    """
    document = _read(stripped_html)
    pending = _pending_chunks(
        document,
        detect_chunk_sections(stripped_html),
        parse_printed_tables(stripped_html.decode("utf-8")),
        chunk_size_chars,
        table_max_chars,
    )

    chunks: list[Html10kChunk] = []
    for position, block in enumerate(pending):
        start = 0 if position == 0 else block.start
        end = pending[position + 1].start if position + 1 < len(pending) else document.length
        for part, text in enumerate(block.texts):
            chunks.append(
                Html10kChunk(
                    chunk_id=html10k_chunk_id(accession_no, start, end, part),
                    accession_no=accession_no,
                    text=text,
                    kind=block.kind,
                    section_key=block.place.keys[-1],
                    section_keys=block.place.keys,
                    section_label=block.place.label,
                    char_start=start,
                    char_end=end,
                    table_index=block.table_index,
                    part=part,
                    parts=len(block.texts),
                    prev_chunk_id=None,
                    next_chunk_id=None,
                )
            )

    ids: list[str | None] = [None, *(chunk.chunk_id for chunk in chunks), None]
    return [
        chunk._replace(prev_chunk_id=ids[index], next_chunk_id=ids[index + 2])
        for index, chunk in enumerate(chunks)
    ]
