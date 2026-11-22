# The table stage of the HTML 10-K flow: one HTML table into a structured grid.
#
# The parser reads what a reader of the page reads: printed text and table structure. It takes
# tag-free HTML and never looks at inline-tagging markup, the fact store or any field of the
# tagged flow, so the HTML flow stays a fair baseline. The scorer at the end of the module
# compares a parsed filing with a cell oracle, which is test data, not parser input.
#
# Specs: docs/specs/html10k-tables.md and, for how filings print scale,
# docs/specs/html10k-tables-scale.md and docs/specs/html10k-tables-scale-2.md.

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple

from lxml import html as lxml_html


class Cell(NamedTuple):
    """One value position of a row, after split cells have been merged.

    Attributes:
        text: The printed text of the merged cell, with whitespace collapsed.
        value: The printed number with its sign (``(169)`` is ``Decimal("-169")``), before any
            scale. ``None`` for a nil, a text cell or an empty position.
        scale: The power of ten that applies to ``value``: 6 for millions, 3 for thousands,
            0 for units, -2 for a percent.
        is_nil: True when the cell prints a dash meaning nothing to report.
        is_percent: True when the value is a percent.
        currency: The ISO 4217 code of a currency symbol printed with this cell, else ``None``.
        footnotes: The footnote markers beside the value, without brackets, in printed order.
        source_row: The 0-based ``<tr>`` ordinal in the table, or ``None`` for an empty position.
        source_cell: The 0-based ``<td>``/``<th>`` ordinal in that row of the cell holding the
            number (or the dash, or the text), or ``None`` for an empty position.
    """

    text: str
    value: Decimal | None
    scale: int
    is_nil: bool
    is_percent: bool
    currency: str | None
    footnotes: tuple[str, ...]
    source_row: int | None
    source_cell: int | None

    @property
    def scaled_value(self) -> Decimal | None:
        """Returns ``value`` times ten to the ``scale``, or ``None`` when there is no value."""
        if self.value is None:
            return None
        return self.value * Decimal(10) ** self.scale


class Row(NamedTuple):
    """One body row.

    Attributes:
        label: The row label with its footnote markers removed.
        footnotes: The footnote markers beside the label.
        cells: One ``Cell`` per column of the table, in column order.
        source_row: The 0-based ``<tr>`` ordinal in the table.
    """

    label: str
    footnotes: tuple[str, ...]
    cells: tuple[Cell, ...]
    source_row: int


class Column(NamedTuple):
    """One value column.

    Attributes:
        headers: The header texts above this column, top to bottom, a spanning header repeated
            under every column it covers. A header row with nothing above this column adds no
            entry.
        period: ``YYYY-MM-DD`` for a full date, ``YYYY`` for a year alone, ``None`` otherwise.
    """

    headers: tuple[str, ...]
    period: str | None


class ParsedTable(NamedTuple):
    """One HTML table as a grid.

    Attributes:
        table_index: The 0-based ordinal of this ``<table>`` in the document.
        caption: The text directly above the table, as given or as found.
        scale: The table-wide scale; cells may differ from it by row, column or percent.
        currency: The table's ISO 4217 code, or ``None`` when it states none.
        header_row_count: How many non-blank leading rows form the header.
        columns: The value columns, left to right, excluding the label column.
        rows: The body rows in order; blank spacer rows are dropped.
    """

    table_index: int
    caption: str
    scale: int
    currency: str | None
    header_row_count: int
    columns: tuple[Column, ...]
    rows: tuple[Row, ...]


class PrintedRow(NamedTuple):
    """One body row as the page prints it.

    Attributes:
        cells: The label, then one text per printed column, footnote markers in brackets.
        is_group: True for a label with nothing beside it.
    """

    cells: tuple[str, ...]
    is_group: bool


class PrintedTable(NamedTuple):
    """One parsed table, with what the page prints that ``ParsedTable`` leaves out.

    ``ParsedTable`` holds the value columns. The printed columns are those and any column of text
    the parser found no value position for, so every printed cell has a place here.

    Attributes:
        table: The parsed table, as ``parse_tables`` returns it.
        stub: The text of the header-row cells left of the first value column.
        title: The nearest block above that is not a running line; a caption that is only a note
            is not a title.
        note: The note printed for this table: a caption that is one, however often it prints,
            else the note from outside the table in force, empty when the table's header or
            caption states a scale.
        headers: One header text per printed column.
        rows: The body rows.
    """

    table: ParsedTable
    stub: str
    title: str
    note: str
    headers: tuple[str, ...]
    rows: tuple[PrintedRow, ...]


class OracleCell(NamedTuple):
    """One record of the table cell oracle; see the spec for how each field is derived.

    Attributes:
        accession: The filing's accession number.
        table_index: The 0-based ``<table>`` ordinal in the document.
        source_row: The 0-based ``<tr>`` ordinal in the table.
        source_cell: The 0-based ``<td>``/``<th>`` ordinal in that row.
        value: The printed magnitude, never negative; ``None`` for a printed nil.
        negative: True when the number is printed negative.
        scale: The power of ten of the tag.
        nil: True when the cell prints a dash for a nil or fixed-zero tag.
        printed: The cell's printed text, for the hand-check.
        concept: The tagged concept, for the hand-check.
        unit: The tagged unit, for diagnosis; not scored.
        tagged_negative: True when the tag itself negates; for diagnosis, not scored.
    """

    accession: str
    table_index: int
    source_row: int
    source_cell: int
    value: Decimal | None
    negative: bool
    scale: int
    nil: bool
    printed: str
    concept: str
    unit: str
    tagged_negative: bool


class TableScore(NamedTuple):
    """The result of scoring parsed tables against an oracle.

    Attributes:
        total: Oracle cells scored.
        exact: Cells correct in value, scale and sign.
        missing: Oracle cells with no parsed cell at their location.
        value_errors: Cells whose magnitude, or nil-ness, differs.
        scale_errors: Numeric cells whose scale differs.
        sign_errors: Numeric, non-zero cells whose sign differs.
        mismatches: Each cell that is not exact, as (oracle cell, parsed cell or ``None``).
    """

    total: int
    exact: int
    missing: int
    value_errors: int
    scale_errors: int
    sign_errors: int
    mismatches: tuple[tuple[OracleCell, Cell | None], ...]

    @property
    def accuracy(self) -> float:
        """Returns ``exact / total``."""
        return self.exact / self.total


# ---- Reading one cell ------------------------------------------------------------------------

_MAX_SPAN = 1000  # a colspan or rowspan beyond this is a malformed attribute, not a layout
_NO_COLUMN = 10**9
_CURRENCY_CODES = {"US$": "USD", "NT$": "TWD", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CURRENCY = r"US\$|NT\$|\$|€|£|¥"
_MARK = r"\(\d{1,2}\)|\([a-z]{1,2}\)|\[[a-z0-9]{1,2}\]|\*+|[†‡§]"
_MARKS_ONLY_RE = re.compile(rf"(?:{_MARK})(?:[\s,]*(?:{_MARK}))*")
_TRAILING_MARK_RE = re.compile(rf"\s*({_MARK})$")
_LONE_PAREN_NUMBER_RE = re.compile(r"\(\d{1,2}\)")
_BARE_MARK_RE = re.compile(r"\s(\d{1,2})$")
_VALUE_RE = re.compile(
    rf"(?P<head>(?:{_CURRENCY}|[(\s])*)"
    r"(?P<minus>[-−–]\s*)?"
    rf"(?P<head2>(?:{_CURRENCY}|[(\s])*)"
    r"(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d*)?|\d+(?:\.\d*)?|\.\d+)"
    r"(?P<tail>[\s)%]*)"
)
_OPENER_RE = re.compile(rf"(?:{_CURRENCY}|[(\s])+")
_CLOSER_RE = re.compile(r"[\s)%]*[)%][\s)%]*")
_DASHES = "-‐‑‒–—―−"
_NIL_RE = re.compile(rf"(?:{_CURRENCY}|[(\s%])*[{_DASHES}][{_DASHES}\s]*(?:{_CURRENCY}|[)\s%])*")
_YEAR_RE = re.compile(r"(?:19|20)\d\d")
_RAISED_STYLE_RE = re.compile(r"top:\s*-|vertical-align:\s*super", re.IGNORECASE)
_RAISED_MARK_RE = re.compile(
    r"[\s(\[]*(?:\d{1,2}|[a-z]|\*+|[†‡§])[\s)\]]*(?:,[\s(\[]*\w+[\s)\]]*)*"
)
_GLUED_MARK_RE = re.compile(r"\d|[a-z]")
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")
_WHITESPACE_RE = re.compile(r"\s+")
_BREAKING_TAGS = frozenset(
    {"br", "p", "div", "li", "ul", "ol", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}
)


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", _ZERO_WIDTH_RE.sub("", text)).strip()


class _Token(NamedTuple):
    """What one ``<td>`` prints, before it is merged with its neighbours."""

    kind: str  # empty, symbol, closer, marker, nil, number or text
    text: str
    value: Decimal | None = None
    negative: bool = False
    percent: bool = False
    currency: str | None = None
    footnotes: tuple[str, ...] = ()
    ambiguous: bool = False  # a "(1)": a marker beside a value, the number -1 alone
    is_year: bool = False


def _strip_mark(mark: str) -> str:
    return mark.strip("()[] ")


def _peel_marks(text: str) -> tuple[str, tuple[str, ...]]:
    """Splits trailing footnote markers off ``text``; a text of nothing else is left alone."""
    marks: list[str] = []
    while True:
        found = _TRAILING_MARK_RE.search(text)
        if found is None or not text[: found.start()].strip():
            return text.strip(), tuple(reversed(marks))
        marks.append(_strip_mark(found.group(1)))
        text = text[: found.start()]


def _currency_of(text: str) -> str | None:
    found = re.search(_CURRENCY, text)
    return _CURRENCY_CODES[found.group(0)] if found else None


def _read_number(body: str, marks: tuple[str, ...]) -> _Token | None:
    """Reads ``body`` as a printed number, or returns ``None`` when it is not one."""
    found = _VALUE_RE.fullmatch(body)
    if found is None:
        return None
    head = found["head"] + found["head2"]
    number = found["number"]
    magnitude = Decimal(number.replace(",", ""))
    negative = "(" in head or found["minus"] is not None
    return _Token(
        "number",
        body,
        value=-magnitude if negative else magnitude,
        negative=negative,
        percent="%" in found["tail"],
        currency=_currency_of(head),
        footnotes=marks,
        is_year=_YEAR_RE.fullmatch(number) is not None and not head.strip(),
    )


def _read_token(text: str, raised: tuple[str, ...] = ()) -> _Token:
    """Classifies the printed content of one ``<td>``.

    Args:
        text: The cell's collapsed text, without its raised markers.
        raised: The cell's raised footnote markers.

    Returns:
        The token.
    """
    if not text:
        return _Token("marker" if raised else "empty", text, footnotes=raised)
    if _LONE_PAREN_NUMBER_RE.fullmatch(text):
        return _Token("marker", text, footnotes=(_strip_mark(text), *raised), ambiguous=True)
    if _MARKS_ONLY_RE.fullmatch(text):
        marks = tuple(_strip_mark(m.group(0)) for m in re.finditer(_MARK, text))
        return _Token("marker", text, footnotes=marks + raised)
    body, marks = _peel_marks(text)
    if marks and _OPENER_RE.fullmatch(body):  # "$(23)": a symbol alone has nothing to footnote
        whole = _read_number(text, raised)
        if whole is not None:
            return whole
    marks += raised
    number = _read_number(body, marks)
    if number is not None:
        return number
    if _NIL_RE.fullmatch(body):
        return _Token(
            "nil", body, percent="%" in body, currency=_currency_of(body), footnotes=marks
        )
    if _CLOSER_RE.fullmatch(body):
        return _Token("closer", body, percent="%" in body, footnotes=marks)
    if _OPENER_RE.fullmatch(body):
        return _Token("symbol", body, negative="(" in body, currency=_currency_of(body))
    return _Token("text", text, footnotes=raised)


def _is_raised(element: Any) -> bool:
    if element.tag == "sup":
        return True
    return element.tag == "span" and bool(_RAISED_STYLE_RE.search(element.get("style", "")))


def _is_glued_marker(child: Any, printed: Sequence[str]) -> bool:
    """Spots a raised marker whose styling is gone.

    Ix-strip removes ``style``, so the raised span of a filing agent reaches the parser as a bare
    ``<span>`` holding a digit or a letter, glued to the word or number before it and ending the
    cell. Nothing else prints like that: a space or a ``$`` comes between other runs.

    Args:
        child: The element.
        printed: The text gathered before it.

    Returns:
        True when the element is a marker.
    """
    if child.tag != "span" or child.tail or len(child) or child.getnext() is not None:
        return False
    before = "".join(printed)
    if not before or not (before[-1].isalnum() or before[-1] in ")%"):
        return False
    return _GLUED_MARK_RE.fullmatch(child.text_content()) is not None


def _gather_text(element: Any, printed: list[str], raised: list[str]) -> None:
    """Appends the printed text under ``element``, lifting raised footnote markers out of it."""
    if element.text:
        printed.append(element.text)
    for child in element:
        if isinstance(child.tag, str) and child.tag != "table":
            is_marker = _is_raised(child) or _is_glued_marker(child, printed)
            if is_marker and _RAISED_MARK_RE.fullmatch(child.text_content()):
                marks = re.split(r"[,\s]+", child.text_content().strip())
                raised.extend(_strip_mark(m) for m in marks if m)
            else:
                _gather_text(child, printed, raised)
            if child.tag in _BREAKING_TAGS:
                printed.append(" ")
        if child.tail:
            printed.append(child.tail)


def _span_of(element: Any, name: str) -> int:
    try:
        return min(max(int(element.get(name, "1")), 1), _MAX_SPAN)
    except ValueError:
        return 1


# ---- Scale, currency and period notes --------------------------------------------------------

_SCALES = {"thousands": 3, "millions": 6, "billions": 9}
_MILLIONS = _SCALES["millions"]
_LARGEST_SHARE_COUNT = 100_000  # as many millions of shares is more than any filer has
_LARGEST_SCALED_NUMBER = 100_000_000  # as many millions is more than any filer reports
_LARGEST_RATE = 1_000
_CENTS_DECIMALS = 2
_CENTS_SCALES = frozenset({_SCALES["thousands"], _MILLIONS})  # billions do print "$1.34"
_SCALE_WORD = r"(thousands|millions|billions)"
_THOUSANDS_MARK = r"\$?\s*(?:in\s+)?\$?\s*000[’']?s?"
_NOTE_LEAD = (
    r"(?:(?:all|tabular|u\.?s\.?|us|amounts?|dollars?|euros?|pounds?|yen|figures|numbers|values"
    r"|are|stated|shown|expressed|reported|presented)\s+){0,4}"
)
_NOTE_END = r"(?=\s*(?:$|[,;:.)\]]|of\s+(?:u\.?s\.?\s+)?dollars|except\b|and\b))"
_NOTE_HEAD_RE = re.compile(rf"\W*{_NOTE_LEAD}(?:in\s+)?{_SCALE_WORD}\b{_NOTE_END}", re.IGNORECASE)
# "Tables present dollars in millions": any short lead, so no filer's words are listed.
_NOTE_FORM_RE = re.compile(
    rf"\W*(?:(?:u\.s\.|[\w'’&/-]+)\s+){{0,4}}(?:dollars|amounts)\s+in\s+{_SCALE_WORD}\b{_NOTE_END}",
    re.IGNORECASE,
)
_NOTE_PAREN_RE = re.compile(rf"\((?:[^()]*\bin\s+|\W*){_SCALE_WORD}\b[^()]*\)", re.IGNORECASE)
_NOTE_TAIL_RE = re.compile(
    rf"\bin\s+{_SCALE_WORD}(?:\s+of\s+[\w.]+(?:\s+dollars)?)?(?:,?\s+except\b[^.]*)?\W*$",
    re.IGNORECASE,
)
_AMOUNTS_STATED_RE = re.compile(
    rf"\bamounts\b[^.]{{0,120}}\b(?:stated|presented|expressed|shown|reported)\s+in\s+{_SCALE_WORD}\b",
    re.IGNORECASE,
)
_THOUSANDS_HEAD_RE = re.compile(rf"\W*{_THOUSANDS_MARK}\b", re.IGNORECASE)
_THOUSANDS_PAREN_RE = re.compile(rf"\(\s*{_THOUSANDS_MARK}\s*\)", re.IGNORECASE)
# "shares, which are reflected in thousands": the words of one clause, none of them about money.
_SHARE_SCALE_RE = re.compile(
    r"\b(?:number\s+of\s+)?shares?\b(?:(?!dollars|amounts)[^.;:()]){0,30}?"
    rf"\bin\s+{_SCALE_WORD}\b",
    re.IGNORECASE,
)
_EXCEPT_RE = re.compile(r"\bexcept\b(?:\s+for)?(?P<terms>[^)]*)", re.IGNORECASE)
_EXCEPT_NOISE_RE = re.compile(
    r"\b(?:data|amounts?|information|figures?|counts?|numbers?\s+of|the|items?)\b", re.IGNORECASE
)
_PER_SHARE_RE = re.compile(
    r"\bper[\s-]+(?:[\w-]+\s+){0,3}?(?:share|unit|ads|adr|award|option)s?\b|\beps\b"
    r"|\bexercise\s+price\b|\bgrant[\s-]+date\s+fair\s+value\b|/share\b",
    re.IGNORECASE,
)
_SHARES_AND_PER_SHARE_RE = re.compile(r"\bshares?\s+and\s+per[\s-]+", re.IGNORECASE)
# "Dividends ($1.20 per share)" and "Shares used in computing net income per share" name a
# per-share figure without being one: the row holds total dollars, or a share count.
_NOT_PER_SHARE_RE = re.compile(
    r"\$\s*[\d.,]+\s*(?:per\b|/)|\bper[\s-]+share\b[:\s(—–-]+\$|\bpar\s+value\b"
    r"|\bshares\b.*\bper[\s-]+(?:\w+\s+)?share\b",
    re.IGNORECASE,
)
_SHARE_COUNT_RE = re.compile(
    r"\bshares\b|\bdilutive\b|\bnumber\s+of\s+(?:[\w-]+\s+){0,3}?(?:units|options|awards)\b",
    re.IGNORECASE,
)
# A label that prints the count holds the dollars beside it.
_NOT_SHARE_COUNT_RE = re.compile(
    r"\bpar\b|\bauthorized\b|\bat\s+cost\b"
    r"|\d\s+(?:(?:thousand|million|billion)\s+)?(?:common\s+)?shares\b",
    re.IGNORECASE,
)
# An except term about shares, not about the price or value of one ("except share price").
_SHARE_TERM_RE = re.compile(r"shares?(?:\s+(?!.*\b(?:prices?|values?)\b).*)?")
# The first word that decides a column. "Total" is the weakest, so "Total Number of Shares" counts.
_COLUMN_MONEY_RE = re.compile(
    r"\bat\s+cost\b|\bamounts?\b|\bvalues?\b|\bcapital\b|\bearnings\b|\bincome\b|\bloss(?:es)?\b"
    r"|\bequity\b|\bdeficit\b",
    re.IGNORECASE,
)
_COLUMN_COUNT_RE = re.compile(
    r"\bshares\b|\bnumber\s+of\b|\bstock\s+outstanding\b|^\W*(?:units|options|awards|rsus|psus)\b",
    re.IGNORECASE,
)
_COLUMN_TOTAL_RE = re.compile(r"\btotal\b", re.IGNORECASE)
# "Number of projects" counts whole things; shares and the like are the share counts above.
_ITEM_COUNT_RE = re.compile(
    r"\W*number\s+of\s+"
    r"(?!(?:[\w-]+\s+){0,3}?(?:shares|units|options|awards|securities|rsus|psus)\b)",
    re.IGNORECASE,
)
_PERCENT_HEADER_RE = re.compile(r"%|\bpercent(?:age)?s?\b", re.IGNORECASE)
_PERCENT_LABEL_RE = re.compile(
    r"\(%\)|(?<![\d.])\s%$|\bas\s+a\s+percent|\bpercent(?:age)?s?$|\(percent(?:age)?\)$",
    re.IGNORECASE,
)
_NOTE_CURRENCIES = (
    (re.compile(r"\bNT\$|taiwan\s+dollars?", re.IGNORECASE), "TWD"),
    (re.compile(r"€|\beuros?\b", re.IGNORECASE), "EUR"),
    (re.compile(r"£|\bpounds?\b", re.IGNORECASE), "GBP"),
    (re.compile(r"¥|\byen\b", re.IGNORECASE), "JPY"),
    (re.compile(r"\$|\bdollars?\b", re.IGNORECASE), "USD"),
)
_MONTH_NUMBERS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_MONTH = (
    r"(january|jan\.?|february|feb\.?|march|mar\.?|april|apr\.?|may|june|jun\.?|july|jul\.?"
    r"|august|aug\.?|september|sept\.?|sep\.?|october|oct\.?|november|nov\.?|december|dec\.?)"
)
_FULL_DATE_RE = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})\s*,?\s*((?:19|20)\d\d)\b", re.IGNORECASE)
_MONTH_DAY_RE = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})\b", re.IGNORECASE)
_ANY_YEAR_RE = re.compile(r"(?<![\d,.])(?:19|20)\d\d(?![\d,])")
_YEAR_ALONE_RE = re.compile(r"(?:(?:fiscal|calendar|fy|year)\s*)*((?:19|20)\d\d)\W*", re.IGNORECASE)
_COMPARISON_RE = re.compile(r"\bvs\b|\bversus\b|\bcompared\b|\bchange\b|\bover\b", re.IGNORECASE)


def _note_word_at_head(text: str) -> re.Match[str] | None:
    return _NOTE_HEAD_RE.match(text) or _NOTE_FORM_RE.match(text)


def _note_scale(text: str) -> int | None:
    """Returns the power of ten ``text`` states as a note, or ``None`` when it states none.

    Prose with a number in it is not a note: only a statement at the head of the text, in
    parentheses, or closing it counts.

    Args:
        text: A caption, a block of text, a header or a label.

    Returns:
        The stated power of ten, or ``None``.
    """
    primary = _SHARE_SCALE_RE.sub(" ", text)
    found = _note_word_at_head(primary) or _NOTE_PAREN_RE.search(primary)
    if found:
        return _SCALES[found.group(1).lower()]
    if _THOUSANDS_HEAD_RE.match(primary) or _THOUSANDS_PAREN_RE.search(primary):
        return 3
    found = _NOTE_TAIL_RE.search(primary) or _AMOUNTS_STATED_RE.search(primary)
    if found:
        return _SCALES[found.group(1).lower()]
    return _share_scale(text)


def note_statement(text: str) -> str:
    """Returns the words of ``text`` that state its scale, for ``_note_scale``'s reading of it.

    Args:
        text: A caption or a note.

    Returns:
        The statement, or ``""`` when the text states none.
    """
    primary = _SHARE_SCALE_RE.sub(" ", text)
    for found in (
        _note_word_at_head(primary),
        _NOTE_PAREN_RE.search(primary),
        _THOUSANDS_HEAD_RE.match(primary),
        _THOUSANDS_PAREN_RE.search(primary),
        _NOTE_TAIL_RE.search(primary),
        _AMOUNTS_STATED_RE.search(primary),
        _SHARE_SCALE_RE.search(text),
    ):
        if found:
            return found.group(0).strip(" \t,;:.")
    return ""


def _share_scale(text: str) -> int | None:
    found = _SHARE_SCALE_RE.search(text)
    return _SCALES[found.group(1).lower()] if found else None


def _note_at_head(text: str) -> re.Match[str] | None:
    return _note_word_at_head(text) or _THOUSANDS_HEAD_RE.match(text)


def _begins_with_note(text: str) -> bool:
    return _note_at_head(text) is not None


def _except_terms(note: str) -> tuple[str, ...]:
    """Returns what a note's ``except`` list counts in units, as lower-case word stems."""
    found = _EXCEPT_RE.search(note)
    if found is None:
        return ()
    terms = []
    for term in re.split(r",|;|\band\b|\bor\b", found["terms"].lower()):
        cleaned = _collapse(_EXCEPT_NOISE_RE.sub(" ", term))
        if not cleaned or cleaned.startswith(("as ", "where", "otherwise")):
            continue
        if re.search(r"\bper\b", cleaned):  # names a per-share figure, which needs no except
            continue
        if cleaned.startswith(("percent", "ratio")):
            continue
        terms.append(cleaned.rstrip("s"))
    return tuple(terms)


def _note_currency(text: str) -> str | None:
    if _note_scale(text) is None:
        return None
    for pattern, code in _NOTE_CURRENCIES:
        if pattern.search(text):
            return code
    return None


def _is_per_share(label: str, *, group: bool = False) -> bool:
    if group and _SHARES_AND_PER_SHARE_RE.search(label):
        return False
    return _PER_SHARE_RE.search(label) is not None and not _NOT_PER_SHARE_RE.search(label)


def _is_share_count(label: str) -> bool:
    if _SHARE_COUNT_RE.search(label) is None or _NOT_SHARE_COUNT_RE.search(label):
        return False
    return not _is_per_share(label)


def _column_kind(texts: Sequence[str]) -> str | None:
    """Reads a header stack from the lowest header up.

    Args:
        texts: The column's headers, top to bottom.

    Returns:
        ``money`` or ``count``, from the first header that names either, or ``None``.
    """
    for text in reversed(texts):
        if _COLUMN_MONEY_RE.search(text):
            return "money"
        if _COLUMN_COUNT_RE.search(text):
            return "count"
        if _COLUMN_TOTAL_RE.search(text):
            return "money"
    return None


def _counts_items(label: str, header: _ColumnHeader) -> bool:
    """Tells whether a row label or the lowest column header counts things other than shares."""
    return _ITEM_COUNT_RE.match(label) is not None or (
        bool(header.texts) and _ITEM_COUNT_RE.match(header.texts[-1]) is not None
    )


def _is_percent_header(header: str) -> bool:
    return _PERCENT_HEADER_RE.search(header) is not None and _note_scale(header) is None


def _iso(year: int, month: str, day: int) -> str | None:
    try:
        return date(year, _MONTH_NUMBERS.index(month[:3].lower()) + 1, day).isoformat()
    except ValueError:
        return None


def _period(headers: Sequence[str], stubs: Sequence[str]) -> str | None:
    """Reads one column's period.

    Args:
        headers: The column's header stack.
        stubs: The stub cells beside the header rows.

    Returns:
        ``YYYY-MM-DD``, ``YYYY`` or ``None``.
    """
    text = " ".join(_peel_marks(h)[0] for h in headers)
    if _COMPARISON_RE.search(text):
        return None
    full = {_iso(int(y), m, int(d)) for m, d, y in _FULL_DATE_RE.findall(text)}
    if len(full) > 1:
        return None
    if full:
        return full.pop()
    years = set(_ANY_YEAR_RE.findall(text))
    if len(years) > 1:
        return None
    year = None
    for header in headers:
        alone = _YEAR_ALONE_RE.fullmatch(_peel_marks(header)[0])
        year = alone.group(1) if alone else year
    stub_text = " ".join(stubs)
    month_day = _MONTH_DAY_RE.search(text) or _MONTH_DAY_RE.search(stub_text)
    if year is not None and month_day is not None:
        return _iso(int(year), month_day.group(1), int(month_day.group(2))) or year
    if year is not None:
        return year
    if not years:
        stub_full = {_iso(int(y), m, int(d)) for m, d, y in _FULL_DATE_RE.findall(stub_text)}
        if len(stub_full) == 1:
            return stub_full.pop()
    return None


# ---- The grid --------------------------------------------------------------------------------


class _Source(NamedTuple):
    """One ``<td>``/``<th>`` of the source, placed on the table's layout grid."""

    ordinal: int
    start: int
    end: int
    token: _Token


class _SourceRow(NamedTuple):
    """One ``<tr>`` with its cells placed."""

    ordinal: int
    filled: tuple[_Source, ...]  # the cells of this <tr> that print something
    carried: tuple[_Source, ...]  # cells of earlier rows reaching into this one by rowspan


@dataclass
class _Slot:
    """One value position while a row is being merged."""

    text: str
    token: _Token
    source_row: int
    source_cell: int
    negative: bool = False
    percent: bool = False
    currency: str | None = None
    footnotes: list[str] = field(default_factory=list)


class _Stacked(NamedTuple):
    """A header cell in a column's stack, with the ``<tr>`` it is in."""

    row: int
    cell: _Source


class _MergedRow(NamedTuple):
    source: _SourceRow
    slots: list[_Slot | None]
    label_tokens: list[_Token]
    unplaced: list[_Source]  # printed cells that are in neither the label nor a slot


def _own_rows(table: Any) -> Iterator[Any]:
    """Yields the rows of this table, leaving out those of tables nested in it."""
    for row in table.iter("tr"):
        if next(row.iterancestors("table"), None) is table:
            yield row


def _read_rows(table: Any) -> list[_SourceRow]:
    """Lays each cell out on a grid of colspan units, honouring rowspans."""
    rows = []
    reaching: dict[int, tuple[int, _Source]] = {}  # grid unit -> (rows still to cover, cell)
    for row_ordinal, row in enumerate(_own_rows(table)):
        carried = {cell.start: cell for _, cell in reaching.values()}
        taken = set(reaching)
        cells: list[_Source] = []
        unit = 0
        for element in row:
            if element.tag not in ("td", "th"):
                continue
            while unit in taken:
                unit += 1
            printed: list[str] = []
            raised: list[str] = []
            _gather_text(element, printed, raised)
            token = _read_token(_collapse("".join(printed)), tuple(raised))
            cell = _Source(len(cells), unit, unit + _span_of(element, "colspan"), token)
            cells.append(cell)
            depth = _span_of(element, "rowspan")
            if depth > 1:
                for covered in range(cell.start, cell.end):
                    reaching[covered] = (depth, cell)
            unit = cell.end
        reaching = {u: (d - 1, c) for u, (d, c) in reaching.items() if d > 1}
        filled = tuple(c for c in cells if c.token.kind != "empty")
        rows.append(_SourceRow(row_ordinal, filled, tuple(carried.values())))
    return rows


def _is_value_row(row: _SourceRow, label_start: int) -> bool:
    for cell in row.filled:
        token = cell.token
        if cell.start == label_start or token.kind in ("text", "marker"):
            continue
        if not token.is_year:
            return True
    return False


def _is_header_like(row: _SourceRow, label_start: int) -> bool:
    return any(c.start > label_start for c in row.filled)


def _count_header_rows(rows: Sequence[_SourceRow], label_start: int) -> int:
    """Counts the leading rows above the first row of values.

    A cell that is itself a note holds the header open however many title rows lie above it.

    Args:
        rows: The non-blank rows.
        label_start: Where the label region starts.

    Returns:
        The number of header rows.
    """
    first_values = next(
        (i for i, row in enumerate(rows) if _is_value_row(row, label_start)), None
    )
    if first_values is None:
        return 1 if len(rows) > 1 and _is_header_like(rows[0], label_start) else 0
    last_note = max(
        (
            position
            for position, row in enumerate(rows[:first_values])
            if any(_begins_with_note(c.token.text) for c in row.filled)
        ),
        default=-1,
    )
    count = 0
    for position, row in enumerate(rows):
        if _is_value_row(row, label_start):
            break
        if position > last_note and not _is_header_like(row, label_start):
            following = rows[position + 1]
            states_note = any(_note_scale(c.token.text) is not None for c in row.filled)
            if not states_note and (
                _is_value_row(following, label_start)
                or not _is_header_like(following, label_start)
            ):
                break
        count += 1
    return count


def _overlap(start: int, end: int, cell: _Source) -> int:
    return max(0, min(end, cell.end) - max(start, cell.start))


def _seed_columns(cells: Sequence[_Source]) -> list[list[int]]:
    """Finds value positions from cells, narrowest first.

    A wide cell that touches two positions found from narrower ones is a value printed across
    both, so it joins neither.

    Args:
        cells: The cells to place.

    Returns:
        Sorted ``[start, end]`` layout ranges.
    """
    columns: list[list[int]] = []
    for cell in sorted(cells, key=lambda c: (c.end - c.start, c.start)):
        touched = [c for c in columns if c[0] < cell.end and cell.start < c[1]]
        if not touched:
            columns.append([cell.start, cell.end])
        elif len(touched) == 1:
            touched[0][0] = min(touched[0][0], cell.start)
            touched[0][1] = max(touched[0][1], cell.end)
    return sorted(columns)


def _leaf_headers(header_rows: Sequence[_SourceRow], label_start: int) -> list[_Source]:
    """Returns the lowest header text over each layout unit."""
    leaves: list[_Source] = []
    for row in reversed(header_rows):
        for cell in row.filled:
            is_note = _begins_with_note(cell.token.text)
            if (
                cell.start > label_start
                and not is_note
                and not any(_overlap(c.start, c.end, cell) for c in leaves)
            ):
                leaves.append(cell)
    return sorted(leaves, key=lambda c: c.start)


def _realign_headers(
    header_rows: Sequence[_SourceRow], columns: Sequence[list[int]], label_start: int
) -> list[_SourceRow]:
    """Moves a bottom header row laid out on a different grid than the numbers below it.

    When the row has one header per value position, in order, but they don't sit over them, the
    order is the only link left, so each header takes its position's range.

    Args:
        header_rows: The header rows.
        columns: The value positions found from the numbers.
        label_start: Where the label region starts.

    Returns:
        The header rows, the last one realigned when it needs to be.
    """
    if not header_rows:
        return list(header_rows)
    bottom = header_rows[-1]
    headers = [c for c in bottom.filled if c.start > label_start]
    pairs = list(zip(headers, columns, strict=False))
    if len(headers) != len(columns) or all(_overlap(s, e, c) for c, (s, e) in pairs):
        return list(header_rows)
    moved = [c._replace(start=s, end=e) for c, (s, e) in pairs]
    stub = tuple(c for c in bottom.filled if c.start <= label_start)
    return [*header_rows[:-1], bottom._replace(filled=stub + tuple(moved))]


def _label_end(rows: Sequence[_SourceRow], label_start: int, first_value: int) -> int:
    """Returns where the label region ends, given where the first value position starts."""
    end = label_start + 1
    for row in rows:
        for cell in row.filled:
            if cell.start == label_start and cell.end <= first_value:
                end = max(end, cell.end)
    return end


def _same_column(
    left: list[int], right: list[int], rows: Sequence[_SourceRow], leaves: Sequence[_Source]
) -> bool:
    """Tells whether two neighbouring positions are one column, split differently by row."""
    under_left = {c.start for c in leaves if _overlap(left[0], left[1], c)}
    under_right = {c.start for c in leaves if _overlap(right[0], right[1], c)}
    if leaves and not under_left & under_right:
        return False
    if not leaves and left[1] != right[0]:
        return False
    for row in rows:
        values = [c for c in row.filled if c.token.kind in ("number", "nil")]
        if any(_overlap(left[0], left[1], c) for c in values) and any(
            _overlap(right[0], right[1], c) for c in values
        ):
            return False
    return True


def _merge_split_columns(
    columns: list[list[int]], rows: Sequence[_SourceRow], leaves: Sequence[_Source]
) -> list[list[int]]:
    merged: list[list[int]] = []
    for column in columns:
        if merged and _same_column(merged[-1], column, rows, leaves):
            merged[-1][1] = column[1]
        else:
            merged.append(column)
    return merged


def _add_header_only_columns(
    columns: list[list[int]], leaves: Sequence[_Source], label_end: int
) -> list[list[int]]:
    """Places headers with no number below them.

    A header printed beside its numbers rather than above them, over the ``$`` say, claims the
    nearest position that has no header of its own. Any other starts a column, which a text cell
    may fill.

    Args:
        columns: The positions found from the numbers.
        leaves: The lowest header cells.
        label_end: Where the label region ends.

    Returns:
        The positions, sorted.
    """
    for leaf in leaves:
        if leaf.end <= label_end or any(_overlap(c[0], c[1], leaf) for c in columns):
            continue
        unclaimed = [c for c in columns if not any(_overlap(c[0], c[1], x) for x in leaves)]
        if unclaimed:
            nearest = min(unclaimed, key=lambda c: abs(c[0] - leaf.start))
            others = [c for c in columns if c is not nearest]
            start = max([nearest[0], *[c[1] for c in others if c[1] <= nearest[0]]])
            end = min([nearest[1], *[c[0] for c in others if c[0] >= nearest[1]]])
            nearest[0] = max(min(nearest[0], leaf.start), start if leaf.start < start else 0)
            nearest[1] = min(max(nearest[1], leaf.end), end if leaf.end > end else _NO_COLUMN)
        else:
            columns.append([leaf.start, leaf.end])
    return sorted(columns)


def _is_value_cell(cell: _Source, leaves: Sequence[_Source]) -> bool:
    """Tells whether a cell fixes a value position: a number, a dash, a ``(1)`` under a header."""
    if cell.token.kind in ("number", "nil"):
        return True
    return cell.token.ambiguous and any(_overlap(c.start, c.end, cell) for c in leaves)


def _find_columns(
    header_rows: Sequence[_SourceRow], body: Sequence[_SourceRow], label_start: int
) -> tuple[list[list[int]], list[_SourceRow]]:
    """Finds the value columns.

    Args:
        header_rows: The header rows.
        body: The body rows.
        label_start: Where the label region starts.

    Returns:
        The columns as half-open ranges of layout units, and the header rows laid on them.
    """
    leaves = _leaf_headers(header_rows, label_start)
    values = [
        cell
        for row in body
        for cell in row.filled
        if cell.start > label_start and _is_value_cell(cell, leaves)
    ]
    seeded = _merge_split_columns(_seed_columns(values), body, leaves)
    aligned = _realign_headers(header_rows, seeded, label_start)
    leaves = _leaf_headers(aligned, label_start)
    first_value = seeded[0][0] if seeded else _NO_COLUMN
    columns = _add_header_only_columns(seeded, leaves, _label_end(body, label_start, first_value))
    if not columns:
        columns = _seed_columns([c for r in body for c in r.filled if c.start > label_start])
    return columns, aligned


def _column_of(cell: _Source, columns: Sequence[list[int]]) -> int | None:
    best, best_overlap = None, 0
    for position, (start, end) in enumerate(columns):
        overlap = _overlap(start, end, cell)
        if overlap > best_overlap:
            best, best_overlap = position, overlap
    return best


def _stack_headers(
    header_rows: Sequence[_SourceRow], columns: Sequence[list[int]], label_start: int
) -> list[tuple[_Stacked, ...]]:
    stacks: list[tuple[_Stacked, ...]] = []
    for start, end in columns:
        stack = []
        for row in header_rows:
            above = [
                c for c in row.filled if c.start > label_start and _overlap(start, end, c) > 0
            ]
            if above:
                widest = max(above, key=lambda c: _overlap(start, end, c))
                stack.append(_Stacked(row.ordinal, widest))
        stacks.append(tuple(stack))
    return stacks


def _table_note_texts(
    header_rows: Sequence[_SourceRow], first_value: int, label_start: int
) -> list[str]:
    """Returns the header texts that can state a note for the whole table, in reading order.

    A stub cell can state it anywhere in its text. A header over the value columns states it only
    by beginning with it: ``Sales (in millions)`` is that column's scale, not the table's.

    Args:
        header_rows: The header rows.
        first_value: Where the first value position starts.
        label_start: Where the label region starts.

    Returns:
        The candidate texts.
    """
    texts = []
    for row in header_rows:
        for cell in row.filled:
            stub = cell.start < first_value or cell.start == label_start
            if stub or _begins_with_note(cell.token.text):
                texts.append(cell.token.text)
    return texts


def _merge_row(row: _SourceRow, columns: Sequence[list[int]]) -> _MergedRow:
    """Merges the split cells of one row into value positions."""
    slots: list[_Slot | None] = [None] * len(columns)
    first_value = columns[0][0] if columns else _NO_COLUMN
    label = [c.token for c in row.carried if c.start < first_value and c.token.kind != "empty"]
    last: _Slot | None = None
    pending: list[_Source] = []
    unplaced: list[_Source] = []
    for cell in row.filled:
        token = cell.token
        in_value_column = token.ambiguous and _column_of(cell, columns) is not None
        is_label_side = cell.start < first_value and not in_value_column
        if is_label_side and token.kind not in ("symbol", "closer"):
            label.append(token)
            if token.kind == "marker" and not token.text:
                unplaced.append(cell)  # the label keeps no text, so a raised mark needs a place
        elif token.kind == "symbol":
            pending.append(cell)
        elif token.kind == "closer":
            if last is not None:
                last.text += token.text
                last.percent = last.percent or token.percent
                last.footnotes.extend(token.footnotes)
            else:
                unplaced.append(cell)
        else:
            placed = _place(cell, columns, slots, row.ordinal, pending, last, unplaced)
            if placed is not None:
                last = placed
                pending = []
    return _MergedRow(row, slots, label, unplaced + pending)


def _place(
    cell: _Source,
    columns: Sequence[list[int]],
    slots: list[_Slot | None],
    row_ordinal: int,
    pending: Sequence[_Source],
    last: _Slot | None,
    unplaced: list[_Source],
) -> _Slot | None:
    """Puts a number, dash, text or marker into its slot.

    Args:
        cell: The cell.
        columns: The value positions.
        slots: The row's slots so far.
        row_ordinal: The ``<tr>`` ordinal.
        pending: Symbols printed before this cell.
        last: The slot filled last in this row.
        unplaced: Receives the cell when it has no slot to go to.

    Returns:
        The new slot, or ``None`` when the cell filled none.
    """
    token = cell.token
    position = _column_of(cell, columns)
    free = position is not None and slots[position] is None
    if token.kind == "marker":
        number = _read_number(token.text, token.footnotes[1:]) if token.ambiguous and free else None
        if number is None:
            if last is not None:
                last.footnotes.extend(token.footnotes)
            else:
                unplaced.append(cell)
            return None
        token = number
    if position is None or not free:
        unplaced.append(cell)
        return None
    slot = _Slot(token.text, token, row_ordinal, cell.ordinal)
    slot.negative = token.negative
    slot.percent = token.percent
    slot.currency = token.currency
    slot.footnotes.extend(token.footnotes)
    for symbol in (cell.token for cell in pending):
        slot.text = symbol.text + slot.text
        slot.currency = slot.currency or symbol.currency
        slot.negative = slot.negative or symbol.negative
    slots[position] = slot
    return slot


def _label_of(tokens: Sequence[_Token]) -> tuple[str, tuple[str, ...]]:
    """Joins the tokens of a label and lifts its markers."""
    marks: list[str] = []
    parts = []
    for token in tokens:
        text, peeled = _peel_marks(token.text)
        parts.append(text)
        marks.extend(peeled)
        marks.extend(token.footnotes if token.kind == "text" else ())
    return _collapse(" ".join(parts)), tuple(marks)


class _Note(NamedTuple):
    """The table-wide scale and what the note that stated it adds."""

    scale: int
    share_scale: int | None
    except_terms: tuple[str, ...]
    text: str = ""


class _Grid(NamedTuple):
    """What parsing one ``<table>`` gives.

    Attributes:
        table: The parsed table.
        note_used: The text of the note it used, empty when it used none.
        stub: The text of the header-row cells left of the first value column.
        outer_note: The note from outside the table in force, empty when the table's own header
            or caption states a scale.
        headers: One header text per printed column.
        rows: The body rows as printed.
    """

    table: ParsedTable
    note_used: str
    stub: str
    outer_note: str
    headers: tuple[str, ...]
    rows: tuple[PrintedRow, ...]


class _Group(NamedTuple):
    """The context a group row gives the rows below it."""

    per_share: bool = False
    scale: int | None = None
    shares: bool = False


class _RowScope(NamedTuple):
    """What the label of a row, and the group row above it, say about its cells."""

    label: str
    footnotes: tuple[str, ...]
    group: _Group


class _ColumnHeader(NamedTuple):
    """What the header stack of one column says about its cells."""

    texts: tuple[str, ...]
    note: _Note | None  # a note in the stack, with the same reach for this column
    percent: bool
    per_share: bool
    kind: str | None  # money, count or neither
    footnotes: frozenset[str]


def _read_note_text(text: str) -> _Note | None:
    scale = _note_scale(text)
    if scale is None:
        return None
    return _Note(scale, _share_scale(text), _except_terms(text), text)


def _carried_marks(text: str, raised: Iterable[str]) -> frozenset[str]:
    """Returns the footnote markers a header or label carries, a bare digit (``Value 1``) too."""
    bare = _BARE_MARK_RE.search(text)
    return frozenset((*raised, *_peel_marks(text)[1], *([bare.group(1)] if bare else ())))


def _read_column_header(tokens: Sequence[_Token]) -> _ColumnHeader:
    texts = [t.text for t in tokens]
    plain = [t for t in texts if _note_scale(t) is None or not _begins_with_note(t)]
    return _ColumnHeader(
        texts=tuple(plain),
        note=next((n for n in map(_read_note_text, texts) if n is not None), None),
        percent=any(_is_percent_header(t) for t in texts),
        per_share=any(_is_per_share(t) for t in plain),
        kind=_column_kind(plain),
        footnotes=frozenset(m for t in tokens for m in _carried_marks(t.text, t.footnotes)),
    )


def _read_note(in_table: Sequence[str], caption: str, scale_note: str) -> _Note:
    for text in (*in_table, caption, scale_note):
        note = _read_note_text(text)
        if note is not None:
            return note
    return _Note(0, None, ())


def _counts_shares(scope: _RowScope, header: _ColumnHeader) -> bool:
    """Tells whether a cell counts shares: its column decides, then its row, then its group."""
    if header.kind is not None:
        return header.kind == "count"
    label = scope.label
    if _NOT_SHARE_COUNT_RE.search(label) or _is_per_share(label):
        return False
    return _is_share_count(label) or scope.group.shares


def _excepts_shares(note: _Note) -> bool:
    return any(_SHARE_TERM_RE.fullmatch(term) for term in note.except_terms)


def _excepts_cell(note: _Note, label: str, header: _ColumnHeader) -> bool:
    """Tells whether a term of the ``except`` list, other than shares, names this cell.

    A label matches in the plural; a column header, being a name, in either number.

    Args:
        note: The note in force.
        label: The row label.
        header: What the column's headers say.

    Returns:
        True when the cell is counted in units.
    """
    head = " ".join(header.texts).lower()
    for term in note.except_terms:
        if _SHARE_TERM_RE.fullmatch(term):
            continue
        name = re.escape(term)
        if re.search(rf"\b{name}s\b", label.lower()) or re.search(rf"\b{name}s?\b", head):
            return True
    return False


def _footnote_scale(
    slot: _Slot | None, scope: _RowScope, header: _ColumnHeader, footnotes: Mapping[str, int]
) -> int | None:
    """Returns the scale a footnote below the table gives this cell, if it carries the marker."""
    if not footnotes:
        return None
    own = () if slot is None else tuple(slot.footnotes)
    marks = (*own, *sorted(_carried_marks(scope.label, scope.footnotes)), *sorted(header.footnotes))
    return next((footnotes[mark] for mark in marks if mark in footnotes), None)


def _cell_scale(
    scope: _RowScope,
    note: _Note,
    header: _ColumnHeader,
    slot: _Slot | None,
    *,
    footnote_scale: int | None = None,
    units_column: bool = False,
    money_line: bool = False,
) -> int:
    """Applies the precedence of requirement 4 to a cell that is not a percent.

    Args:
        scope: The row's label and group.
        note: The table's note.
        header: What the column's headers say.
        slot: The cell's content. A printed currency symbol means it counts no shares. A
            per-share figure prints cents: ``Net income for basic earnings per share`` and the
            rows of a ``Basic EPS:`` computation hold whole dollars in the table's scale.
        footnote_scale: The scale a footnote below the table gives this cell.
        units_column: True when the column holds a share count too large for a note of millions.
        money_line: True when the cell's row or column prints a currency symbol.

    Returns:
        The power of ten.
    """
    label, group = scope.label, scope.group
    counts = _counts_shares(scope, header)
    has_cents = slot is not None and "." in slot.text
    per_share = _is_per_share(label) or header.per_share or (group.per_share and not counts)
    if per_share and has_cents:
        return 0
    if footnote_scale is not None:
        return footnote_scale
    for stated in (_note_scale(label), group.scale):
        if stated is not None:
            return stated
    active = header.note or note
    if slot is not None:
        if _is_too_large_for_note(slot, active) or (money_line and _is_cents(slot, active)):
            return 0
        if slot.currency is not None:
            return active.scale
        if header.note is None and _counts_items(label, header):
            return 0
    if counts:
        if active.share_scale is not None:
            return active.share_scale
        if _excepts_shares(active) or (units_column and active.scale >= _MILLIONS):
            return 0
    return 0 if _excepts_cell(active, label, header) else active.scale


def _build_cell(slot: _Slot | None, scale: int, percent: bool) -> Cell:
    if slot is None:
        return Cell("", None, scale, False, False, None, (), None, None)
    value = slot.token.value
    if value is not None and slot.negative:
        value = -abs(value)
    is_percent = percent and slot.token.kind != "text"
    return Cell(
        text=slot.text,
        value=value,
        scale=-2 if is_percent else scale,
        is_nil=slot.token.kind == "nil",
        is_percent=is_percent,
        currency=slot.currency,
        footnotes=tuple(slot.footnotes),
        source_row=slot.source_row,
        source_cell=slot.source_cell,
    )


def _prints_at_least(slot: _Slot, bound: int) -> bool:
    value = slot.token.value
    return value is not None and abs(value) >= bound


def _is_too_large_for_note(slot: _Slot, note: _Note) -> bool:
    """Tells a whole number too large to be in millions: it is a full count, whatever it counts."""
    return (
        note.scale >= _MILLIONS
        and _prints_at_least(slot, _LARGEST_SCALED_NUMBER)
        and _decimals(slot) == 0
    )


def _is_cents(slot: _Slot, note: _Note) -> bool:
    return note.scale in _CENTS_SCALES and _decimals(slot) == _CENTS_DECIMALS


def _money_columns(merged: Sequence[_MergedRow], width: int) -> list[bool]:
    """Marks the columns that print a currency symbol in any row."""
    return [
        any(slot is not None and slot.currency for slot in (m.slots[column] for m in merged))
        for column in range(width)
    ]


def _rate_columns(merged: Sequence[_MergedRow], width: int) -> list[bool]:
    """Marks the columns that print ``%``, no currency symbol and no number of 1,000 or more."""
    rates = []
    for column in range(width):
        slots = [m.slots[column] for m in merged]
        filled = [s for s in slots if s is not None]
        rates.append(
            any(s.percent for s in filled)
            and not any(s.currency for s in filled)
            and not any(_prints_at_least(s, _LARGEST_RATE) for s in filled)
        )
    return rates


def _decimals(slot: _Slot) -> int:
    value = slot.token.value
    exponent = 0 if value is None else value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) else 0


def _percent_row_decimals(row: _MergedRow) -> frozenset[int]:
    """Returns the decimals of the printed ``%`` cells of a percent row, empty for any other."""
    filled = [s for s in row.slots if s is not None]
    if any(s.currency for s in filled):
        return frozenset()
    return frozenset(_decimals(s) for s in filled if s.percent and s.token.kind == "number")


def _rate_rows(merged: Sequence[_MergedRow]) -> list[frozenset[int]]:
    """Finds the decimals a number must print to be a percent, row by row.

    A filer may print ``%`` once in a row, or only on the first and last rows of a run: the rows
    between two percent rows of the same decimals are rates too, while no currency symbol lies
    between them.

    Args:
        merged: The body rows.

    Returns:
        For each row, the decimals that make a number a percent; empty when none does.
    """
    rows = [_percent_row_decimals(m) for m in merged]
    rates = list(rows)
    run_start: int | None = None
    for position, (row, decimals) in enumerate(zip(merged, rows, strict=True)):
        if decimals:
            if run_start is not None:
                shared = rows[run_start] & decimals
                for between in range(run_start + 1, position):
                    rates[between] = shared
            run_start = position
        elif any(s is not None and s.currency for s in row.slots):
            run_start = None
    return rates


def _is_rate_number(slot: _Slot, decimals: frozenset[int]) -> bool:
    """Tells whether a number takes the percent of its row: a whole number never does."""
    if slot.token.kind != "number" or _prints_at_least(slot, _LARGEST_RATE):
        return False
    places = _decimals(slot)
    return places > 0 and places in decimals


def _row_scopes(merged: Sequence[_MergedRow]) -> list[_RowScope]:
    """Reads each row's label and the group row that scopes it."""
    scopes = []
    group = _Group()
    after_group = False
    for row in merged:
        label, footnotes = _label_of(row.label_tokens)
        is_group = not any(row.slots)
        if is_group:
            per_share = _is_per_share(label, group=True) or (after_group and group.per_share)
            group = _Group(per_share, _note_scale(label), _is_share_count(label))
        after_group = is_group
        scopes.append(_RowScope(label, footnotes, group))
    return scopes


def _units_columns(
    merged: Sequence[_MergedRow], scopes: Sequence[_RowScope], headers: Sequence[_ColumnHeader]
) -> list[bool]:
    """Marks the columns with a share count too large for a note of millions.

    Only share-count cells are looked at: dollars in the same column prove nothing.

    Args:
        merged: The body rows.
        scopes: The label and group of each row.
        headers: What each column's headers say.

    Returns:
        One flag per column.
    """
    units = [False] * len(headers)
    for row, scope in zip(merged, scopes, strict=True):
        for column, (slot, header) in enumerate(zip(row.slots, headers, strict=True)):
            if (
                slot is not None
                and slot.currency is None
                and not slot.percent
                and _prints_at_least(slot, _LARGEST_SHARE_COUNT)
                and _counts_shares(scope, header)
            ):
                units[column] = True
    return units


def _marked(text: str, marks: Sequence[str]) -> str:
    return " ".join([text, *(f"[{mark}]" for mark in marks)]).strip()


def _printed_text(token: _Token) -> str:
    """Returns what a cell prints, a marker that is only raised included."""
    if token.kind == "marker" and token.text:
        return token.text
    return _marked(token.text, token.footnotes)


def _printed_grid(
    table: ParsedTable,
    merged: Sequence[_MergedRow],
    header_rows: Sequence[_SourceRow],
    stacks: Sequence[tuple[_Stacked, ...]],
    positions: Sequence[list[int]],
) -> tuple[str, tuple[str, ...], tuple[PrintedRow, ...]]:
    """Lays out everything a table prints, whether or not the parser has a place for it.

    Value positions come from numbers, so a column of text can have none: an exhibit list's
    descriptions, or an index's titles beside a column of page numbers. Such a cell joins the
    column it sits under, or starts a printed column of its own, between its neighbours. A cell
    left of every column joins the label.

    Args:
        table: The parsed table.
        merged: Its merged body rows.
        header_rows: Its header rows.
        stacks: The header cells stacked over each value position.
        positions: The value positions.

    Returns:
        The stub text, one header per printed column, and the body rows.
    """
    first_value = positions[0][0] if positions else _NO_COLUMN
    in_stack = {(s.row, s.cell.ordinal) for stack in stacks for s in stack}
    stub_tokens: list[_Token] = []
    lost_headers: list[_Stacked] = []
    for header in header_rows:
        for cell in header.filled:
            if cell.start < first_value:
                stub_tokens.append(cell.token)
            elif (header.ordinal, cell.ordinal) not in in_stack:
                lost_headers.append(_Stacked(header.ordinal, cell))

    lost = [s.cell for s in lost_headers] + [c for m in merged for c in m.unplaced]
    own_column = [c for c in lost if c.start >= first_value and _column_of(c, positions) is None]
    columns = sorted([*positions, *_seed_columns(own_column)])
    printed_at = [columns.index(position) for position in positions]

    # Each printed column is gathered as (where it is printed, text), and read in that order.
    head: list[list[tuple[int, str]]] = [[] for _ in columns]
    stub: list[str] = [_printed_text(token) for token in stub_tokens]
    for position, stack in zip(printed_at, stacks, strict=True):
        head[position].extend((s.row, _printed_text(s.cell.token)) for s in stack)
    for row_ordinal, cell in lost_headers:
        column = _column_of(cell, columns)
        if column is None:
            stub.append(_printed_text(cell.token))
        else:
            head[column].append((row_ordinal, _printed_text(cell.token)))

    rows = []
    for source, row in zip(merged, table.rows, strict=True):
        body: list[list[tuple[int, str]]] = [[] for _ in columns]
        for position, cell_of in zip(printed_at, row.cells, strict=True):
            if cell_of.text:
                ordinal = cell_of.source_cell if cell_of.source_cell is not None else 0
                body[position].append((ordinal, _marked(cell_of.text, cell_of.footnotes)))
        # A label that reads as a number, such as an exhibit's, keeps its markers on the token.
        marks = [m for t in source.label_tokens if t.kind in ("number", "nil") for m in t.footnotes]
        printed = _marked(row.label, [*row.footnotes, *marks])
        label = [printed] if printed else []
        for cell in source.unplaced:
            column = _column_of(cell, columns)
            if column is None:
                label.append(_printed_text(cell.token))
            else:
                body[column].append((cell.ordinal, _printed_text(cell.token)))
        texts = tuple(" ".join(t for _, t in sorted(found, key=lambda x: x[0])) for found in body)
        printed_label = " ".join(label)
        rows.append(PrintedRow((printed_label, *texts), bool(printed_label) and not any(texts)))
    headers = tuple(" ".join(t for _, t in sorted(found, key=lambda x: x[0])) for found in head)
    return " ".join(stub), headers, tuple(rows)


def _parse_grid(
    rows: Sequence[_SourceRow],
    caption: str,
    scale_note: str,
    table_index: int,
    footnotes: Mapping[str, int],
) -> _Grid:
    """Parses the rows of one ``<table>``.

    Args:
        rows: The table's rows, as ``_read_rows`` laid them out.
        caption: The text directly above the table.
        scale_note: The note in force from outside the table.
        table_index: The ordinal to record.
        footnotes: The scale each footnote marker below the table states.

    Returns:
        The parsed table, with the notes and the stub text around it.
    """
    rows = [r for r in rows if r.filled]
    label_start = min((c.start for r in rows for c in r.filled), default=0)
    header_count = _count_header_rows(rows, label_start)
    header_rows, body = rows[:header_count], rows[header_count:]
    positions, header_rows = _find_columns(header_rows, body, label_start)
    first_value = positions[0][0] if positions else _NO_COLUMN

    stubs = [c.token.text for r in header_rows for c in r.filled if c.start < first_value]
    placed_stacks = _stack_headers(header_rows, positions, label_start)
    token_stacks = [tuple(s.cell.token for s in stack) for stack in placed_stacks]
    stacks = [tuple(t.text for t in stack) for stack in token_stacks]
    in_table = _table_note_texts(header_rows, first_value, label_start)
    note = _read_note(in_table, caption, scale_note)
    headers = [_read_column_header(stack) for stack in token_stacks]

    merged = [_merge_row(row, positions) for row in body]
    rate_column = _rate_columns(merged, len(positions))
    rate_row = _rate_rows(merged)
    scopes = _row_scopes(merged)
    units_column = _units_columns(merged, scopes, headers)
    money_column = _money_columns(merged, len(positions))
    parsed_rows = []
    for position, ((row, slots, *_), scope) in enumerate(zip(merged, scopes, strict=True)):
        by_label = _PERCENT_LABEL_RE.search(scope.label) is not None
        money_row = any(slot is not None and slot.currency for slot in slots)
        cells = []
        for column, slot in enumerate(slots):
            stated = slot is not None and slot.percent
            by_context = by_label or headers[column].percent or rate_column[column]
            has_currency = slot is not None and slot.currency is not None
            by_row = slot is not None and _is_rate_number(slot, rate_row[position])
            percent = stated or (slot is not None and not has_currency and (by_context or by_row))
            scale = _cell_scale(
                scope,
                note,
                headers[column],
                slot,
                footnote_scale=_footnote_scale(slot, scope, headers[column], footnotes),
                units_column=units_column[column],
                money_line=money_row or money_column[column],
            )
            cells.append(_build_cell(slot, scale, percent))
        parsed_rows.append(Row(scope.label, scope.footnotes, tuple(cells), row.ordinal))

    currency = next(
        (c for c in map(_note_currency, (*in_table, caption)) if c is not None),
        next((s.currency for m in merged for s in m.slots if s and s.currency), None),
    )
    columns = tuple(Column(stack, _period(stack, stubs)) for stack in stacks)
    table = ParsedTable(
        table_index, caption, note.scale, currency, header_count, columns, tuple(parsed_rows)
    )
    states_its_own = _read_note(in_table, caption, "").text != ""
    stub, printed_headers, printed_rows = _printed_grid(
        table, merged, header_rows, placed_stacks, positions
    )
    return _Grid(
        table,
        note.text,
        stub,
        "" if states_its_own else scale_note,
        printed_headers,
        printed_rows,
    )


# ---- The document walk -----------------------------------------------------------------------

_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "center", "dd", "div", "dl", "dt",
        "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "ol", "p",
        "pre", "section", "ul",
    }
)  # fmt: skip
_UNPRINTED_TAGS = frozenset({"head", "script", "style", "title"})
_RUNNING_LINE_COPIES = 3
# Above this a block is a line that heads pages, in any case: a real heading such as a section's
# "Research and Development" can be printed three times.
_RUNNING_TITLE = 5
_TRAILING_PAGE_RE = re.compile(r"[\s|]*\d{1,4}$")
_MIN_FOOTER_WORDS = 2
_PAGE_NUMBER_RE = re.compile(r"(?:[A-Za-z]{1,2}-)?\d{1,4}")
_BLOCK_MARK = rf"{_MARK}|\d{{1,2}}(?=[\sA-Z])"
_BLOCK_MARK_RE = re.compile(_BLOCK_MARK)
_FOOTNOTE_OPEN_RE = re.compile(rf"\s*(?:{_BLOCK_MARK})(?:[\s,]*(?:{_MARK}))*\s*")
_AMOUNTS_SENTENCE_RE = re.compile(
    rf"\bamounts\s+(?:\w+\s+){{0,3}}?in\s+{_SCALE_WORD}\b", re.IGNORECASE
)
# "U.S. Dollars" goes on with a capital too; a sentence ends on something other than an initial.
_SECOND_SENTENCE_RE = re.compile(r"(?<![A-Z])\.\s+[A-Z]")
_SHARES_WORD_RE = re.compile(r"\bshares?\b", re.IGNORECASE)
_MONEY_WORD_RE = re.compile(r"\$|\bdollars?\b|\bamounts?\b", re.IGNORECASE)


def _parse_document(markup: str) -> Any:
    parser = lxml_html.HTMLParser(encoding="utf-8", huge_tree=True)
    return lxml_html.document_fromstring(markup.encode("utf-8"), parser=parser)


class _TableItem(NamedTuple):
    """A ``<table>`` in the document's run of blocks and tables."""

    rows: list[_SourceRow]
    is_furniture: bool


class _Walk:
    """Lists the blocks of text and the tables of a document, in document order."""

    def __init__(self) -> None:
        self.items: list[str | _TableItem] = []
        self._buffer: list[str] = []

    def _flush(self) -> None:
        text = _collapse("".join(self._buffer))
        self._buffer.clear()
        if text:
            self.items.append(text)

    def _table(self, element: Any) -> None:
        self._flush()
        for table in element.iter("table"):
            rows = _read_rows(table)
            printed = sum(len(row.filled) for row in rows)
            self.items.append(_TableItem(rows, printed <= 1))

    def visit(self, element: Any) -> None:
        """Walks ``element`` and everything below it."""
        if not isinstance(element.tag, str) or element.tag in _UNPRINTED_TAGS:
            return
        if element.tag == "table":
            self._table(element)
            return
        is_block = element.tag in _BLOCK_TAGS
        if is_block:
            self._flush()
        if element.text:
            self._buffer.append(element.text)
        for child in element:
            self.visit(child)
            if child.tail:
                self._buffer.append(child.tail)
        if is_block:
            self._flush()


def _block_footnote(text: str) -> tuple[tuple[str, ...], int] | None:
    """Reads a block that states a scale for a footnote marker.

    Args:
        text: The block's text.

    Returns:
        The block's markers and the scale, or ``None`` when it opens with no marker or states no
        scale by its head or by a sentence about amounts.
    """
    opened = _FOOTNOTE_OPEN_RE.match(text)
    if opened is None:
        return None
    rest = text[opened.end() :]
    if _begins_with_note(rest):
        scale = _note_scale(rest)
    else:
        stated = _AMOUNTS_SENTENCE_RE.search(rest)
        scale = _SCALES[stated.group(1).lower()] if stated else None
    if scale is None:
        return None
    marks = tuple(_strip_mark(m.group(0)) for m in _BLOCK_MARK_RE.finditer(opened.group(0)))
    return marks, scale


def _is_furniture(text: str, printed: Mapping[str, int]) -> bool:
    """Tells a page number or a running line from a block a reader reads as text."""
    if _PAGE_NUMBER_RE.fullmatch(text):
        return True
    return printed[text] >= _RUNNING_LINE_COPIES and _note_scale(text) is None


def _is_parenthesis_note(text: str) -> bool:
    """Tells whether a block is one parenthesis stating a scale for a table's dollars.

    The wording does not matter, only the form: ``(Dollars in tables in millions)``. A parenthesis
    about shares alone says nothing about the dollars; an ``except`` list does not make it so.

    Args:
        text: The block's text.

    Returns:
        True when the block is such a note.
    """
    if _NOTE_PAREN_RE.fullmatch(text) is None:
        return False
    stated = _EXCEPT_RE.sub("", text)
    return _SHARES_WORD_RE.search(stated) is None or _MONEY_WORD_RE.search(stated) is not None


def _is_block_note(text: str) -> bool:
    return _begins_with_note(text) or _is_parenthesis_note(text)


def _standing_note(text: str) -> str:
    """Returns the part of a block that stands for the tables after it, or an empty string.

    A note stands when it is the block's one statement: a footnote speaks of its marker, and a
    note that goes on to a second sentence describes what is beside it.

    Args:
        text: The block's text.

    Returns:
        The standing note, or ``""``.
    """
    if _FOOTNOTE_OPEN_RE.match(text):
        return ""
    head = _note_at_head(text)
    if head is not None:
        return "" if _SECOND_SENTENCE_RE.search(text, head.end()) else text
    if _is_parenthesis_note(text):
        return "" if _SECOND_SENTENCE_RE.search(text) else text
    stated = _AMOUNTS_STATED_RE.search(text)
    return stated.group(0) if stated else ""


def _footnotes_below(items: Sequence[str | _TableItem], position: int) -> dict[str, int]:
    """Reads the footnotes between the table at ``position`` and the next table.

    Args:
        items: The document's blocks and tables.
        position: Where the table is.

    Returns:
        The scale each marker states.
    """
    footnotes: dict[str, int] = {}
    for item in items[position + 1 :]:
        if isinstance(item, _TableItem):
            if item.is_furniture:
                continue
            break
        stated = _block_footnote(item)
        if stated is not None:
            for mark in stated[0]:
                footnotes.setdefault(mark, stated[1])
    return footnotes


def _line_key(text: str) -> str:
    """Returns what stays the same from page to page of a running line.

    The case can differ, and a footer ends with its page number. A word and a number alone
    (``Note 7``, ``Table 5``) is a numbered heading, so it keeps the number.

    Args:
        text: A block.

    Returns:
        The key to count copies by.
    """
    key = text.casefold()
    without_page = _TRAILING_PAGE_RE.sub("", key)
    return without_page if len(without_page.split()) >= _MIN_FOOTER_WORDS else key


def _printed_table(grid: _Grid, blocks: Sequence[str], copies: Mapping[str, int]) -> PrintedTable:
    """Reads what a table prints as its title and note.

    A line that runs through the document heads pages, not this table, so the title is the
    nearest block that is not one, and there is no title when every block above is one.

    Args:
        grid: The parsed table.
        blocks: The blocks above the table, last nearest, since the table before.
        copies: How many times each block is printed in the document, in any case.

    Returns:
        The table with its stub, title and note.
    """
    caption = blocks[-1] if blocks else ""
    note = caption if _is_block_note(caption) else grid.outer_note
    candidates = blocks[:-1] if _is_block_note(caption) else blocks
    title = next((b for b in reversed(candidates) if copies[_line_key(b)] < _RUNNING_TITLE), "")
    return PrintedTable(grid.table, grid.stub, title, note, grid.headers, grid.rows)


def _note_table_statement(item: _TableItem) -> str:
    """Returns what a table of no numbers states as a note for the tables after it.

    Some filers print a page's heading and its note as a little table. A year is not a number
    here, and the last cell that begins with a note wins. A table of numbers states a note for
    itself alone.

    Args:
        item: The table.

    Returns:
        The standing note, or ``""``.
    """
    tokens = [cell.token for row in item.rows for cell in row.filled]
    if any(t.kind == "nil" or (t.kind == "number" and not t.is_year) for t in tokens):
        return ""
    notes = [t.text for t in tokens if t.kind == "text" and _begins_with_note(t.text)]
    return _standing_note(notes[-1]) if notes else ""


def _read_document(items: Sequence[str | _TableItem]) -> list[PrintedTable]:
    """Parses the tables of a document with the caption and notes each one is under.

    Args:
        items: The document's blocks and tables, in order.

    Returns:
        One ``PrintedTable`` per table.
    """
    printed = Counter(item for item in items if isinstance(item, str))
    copies = Counter(_line_key(item) for item in items if isinstance(item, str))
    tables: list[PrintedTable] = []
    caption = standing = ""
    run_note = ""  # the note of the table this one may continue
    blocks: list[str] = []  # the captions in turn since the table before
    for position, item in enumerate(items):
        if isinstance(item, str):
            if _is_furniture(item, printed):
                continue
            run_note = ""
            if _block_footnote(item) is None:
                caption = item
                blocks.append(item)
                standing = _standing_note(item) or standing
            continue
        grid = _parse_grid(
            item.rows,
            caption,
            run_note or standing,
            len(tables),
            _footnotes_below(items, position),
        )
        tables.append(_printed_table(grid, blocks, copies))
        if not item.is_furniture:
            caption, run_note = "", grid.note_used
            blocks = []
        stated = _note_table_statement(item)
        if stated:  # it acts as a block: what follows continues nothing before it
            standing, run_note = stated, ""
    return tables


# ---- The oracle and its scorer ---------------------------------------------------------------

_ORACLE_ORDINAL_KEYS = ("table_index", "source_row", "source_cell")
_ORACLE_STR_KEYS = ("accession", "printed", "concept", "unit")
_ORACLE_BOOL_KEYS = ("negative", "nil", "tagged_negative")
_ORACLE_KEYS = frozenset(
    (*_ORACLE_ORDINAL_KEYS, *_ORACLE_STR_KEYS, *_ORACLE_BOOL_KEYS, "scale", "value")
)


def _oracle_value(raw: object, nil: bool) -> Decimal | None:
    if raw is None:
        if not nil:
            raise ValueError("a record that is not nil needs a value")
        return None
    if nil:
        raise ValueError("a nil record has a null value")
    if not isinstance(raw, str):
        raise ValueError(f"value must be a decimal string, not {type(raw).__name__}")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"value {raw!r} is not a decimal") from error
    if not value.is_finite() or value.is_signed():
        raise ValueError(f"value {raw!r} is not a printed magnitude")
    return value


def _oracle_int(record: Mapping[str, object], key: str) -> int:
    value = record[key]
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer")
    return value


def _oracle_bool(record: Mapping[str, object], key: str) -> bool:
    value = record[key]
    if type(value) is not bool:
        raise ValueError(f"{key} must be a boolean")
    return value


def _oracle_str(record: Mapping[str, object], key: str) -> str:
    value = record[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _score_cell(cell: Cell, oracle_cell: OracleCell) -> tuple[bool, bool, bool]:
    """Returns (value error, scale error, sign error) for a located cell."""
    if oracle_cell.nil and cell.is_nil:
        return False, False, False
    if cell.value is None or oracle_cell.value is None:
        return True, False, False
    magnitude = abs(cell.value)
    sign_differs = magnitude != 0 and (cell.value < 0) != oracle_cell.negative
    return magnitude != oracle_cell.value, cell.scale != oracle_cell.scale, sign_differs


def parse_table(
    table_html: str, *, caption: str = "", scale_note: str = "", table_index: int = 0
) -> ParsedTable:
    """Parses one ``<table>`` element.

    Args:
        table_html: The HTML of one table, free of tagging markup.
        caption: The text directly above the table.
        scale_note: A standing scale note inherited from earlier in the document.
        table_index: The ordinal to record on the result.

    Returns:
        The parsed grid.
    """
    table = next(_parse_document(table_html).iter("table"), None)
    if table is None:
        raise ValueError("no <table> element in the input")
    return _parse_grid(_read_rows(table), caption, scale_note, table_index, {}).table


def parse_tables(document_html: str) -> list[ParsedTable]:
    """Parses every ``<table>`` of a document, in document order.

    Args:
        document_html: The HTML of one document, free of tagging markup.

    Returns:
        One ``ParsedTable`` per ``<table>``, with ``table_index`` equal to its position.
    """
    return [printed.table for printed in parse_printed_tables(document_html)]


def parse_printed_tables(document_html: str) -> list[PrintedTable]:
    """Parses every ``<table>`` of a document, keeping what is printed around each one.

    Args:
        document_html: The HTML of one document, free of tagging markup.

    Returns:
        One ``PrintedTable`` per ``<table>``, in document order; each ``table`` is what
        ``parse_tables`` returns for it.
    """
    walk = _Walk()
    walk.visit(_parse_document(document_html))
    return _read_document(walk.items)


def parse_oracle_record(record: Mapping[str, object]) -> OracleCell:
    """Validates one decoded JSON line of the oracle.

    Args:
        record: The decoded record.

    Returns:
        The typed oracle cell.

    Raises:
        ValueError: On a missing or unknown key, or a field of the wrong type or range.
    """
    if set(record) != _ORACLE_KEYS:
        raise ValueError(f"bad keys: {sorted(set(record) ^ _ORACLE_KEYS)}")
    ordinals = [_oracle_int(record, key) for key in _ORACLE_ORDINAL_KEYS]
    if min(ordinals) < 0:
        raise ValueError(f"an ordinal is negative: {ordinals}")
    nil = _oracle_bool(record, "nil")
    return OracleCell(
        accession=_oracle_str(record, "accession"),
        table_index=ordinals[0],
        source_row=ordinals[1],
        source_cell=ordinals[2],
        value=_oracle_value(record["value"], nil),
        negative=_oracle_bool(record, "negative"),
        scale=_oracle_int(record, "scale"),
        nil=nil,
        printed=_oracle_str(record, "printed"),
        concept=_oracle_str(record, "concept"),
        unit=_oracle_str(record, "unit"),
        tagged_negative=_oracle_bool(record, "tagged_negative"),
    )


def score_tables(
    parsed: Mapping[str, Sequence[ParsedTable]], oracle: Iterable[OracleCell]
) -> TableScore:
    """Scores parsed tables against the oracle on exact value, scale and sign.

    Args:
        parsed: The parsed tables of each filing, by accession number.
        oracle: The oracle cells.

    Returns:
        The score over every oracle cell.

    Raises:
        ValueError: When the oracle is empty or holds the same location twice.
    """
    cells = list(oracle)
    if not cells:
        raise ValueError("the oracle is empty")
    locations = [(c.accession, c.table_index, c.source_row, c.source_cell) for c in cells]
    if len(set(locations)) != len(locations):
        raise ValueError("the oracle holds a location twice")
    index: dict[tuple[str, int, int, int], Cell] = {}
    for accession, tables in parsed.items():
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.source_row is not None and cell.source_cell is not None:
                        where = (accession, table.table_index, cell.source_row, cell.source_cell)
                        index[where] = cell
    exact = missing = value_errors = scale_errors = sign_errors = 0
    mismatches: list[tuple[OracleCell, Cell | None]] = []
    for location, oracle_cell in zip(locations, cells, strict=True):
        found = index.get(location)
        if found is None:
            missing += 1
            mismatches.append((oracle_cell, None))
            continue
        value_error, scale_error, sign_error = _score_cell(found, oracle_cell)
        value_errors += value_error
        scale_errors += scale_error
        sign_errors += sign_error
        if value_error or scale_error or sign_error:
            mismatches.append((oracle_cell, found))
        else:
            exact += 1
    return TableScore(
        len(cells), exact, missing, value_errors, scale_errors, sign_errors, tuple(mismatches)
    )
