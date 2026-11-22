# From the model's reply to an answer: citations are checked, and the calculator does the sums.
#
# The reply is untrusted. Each number in the answer comes from the calculator, over inputs that
# resolved to a number printed in a chunk given in this call. A `value` or `numeric` key is never
# read. A citation that doesn't line up exactly makes the answer hedged, not refused: only a number
# that isn't in the cited chunk at all blocks it.
#
# Spec: docs/specs/html-rag-arm.md.

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from src.retriever.html10k.retriever import RetrievedChunk
from src.retriever.xbrl.xbrl_calculator_tool import CalculatorError, XbrlCalculatorTool

_SCALES = (1, 1000, 1000000, 1000000000)
_SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_MARKER = re.compile(r"\[[^\]]*\]")  # a footnote marker, as the table parser prints it
_CURRENCY = re.compile(r"US\$|\$", re.IGNORECASE)
_LEADING_MINUS = ("-", "\N{MINUS SIGN}", "\N{EN DASH}")
_NUMBER = re.compile(
    r"(?P<digits>\d+(?:\.\d*)?|\.\d+)\s*(?:(?P<word>thousand|million|billion|trillion)s?)?",
    re.IGNORECASE,
)
_NUMBER_TOKEN = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?")
_CELL_BAR = re.compile(r"(?<!\\)\|")
_SEPARATOR_CELL = re.compile(r":?-+:?")

_EXACT = "exact"
_PRINTED = "printed"
_NONE = "none"


class _Printed(NamedTuple):
    """A printed number: its signed amount, and the multiplier of a scale word printed with it."""

    amount: float
    word_scale: float | None


def _read_printed(printed: str) -> _Printed | None:
    """Read a number as printed; ``None`` for a nil or a text that isn't one number."""
    text = _MARKER.sub("", unicodedata.normalize("NFKC", printed))
    text = _CURRENCY.sub("", text).replace(",", "").replace("%", "").strip()
    negative = text.startswith(_LEADING_MINUS)
    if negative:
        text = text[1:].strip()
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    match = _NUMBER.fullmatch(text)
    if match is None:
        return None
    amount = float(match["digits"])
    word = match["word"]
    return _Printed(
        -amount if negative else amount, _SCALE_WORDS[word.lower()] if word else None
    )


def printed_value(printed: str, scale: object) -> float | None:
    """The amount a printed number stands for: the one place scale and sign are applied.

    Args:
        printed: The number as the filing prints it.
        scale: What the filing's scale note makes of it: 1, 1000, 1000000 or 1000000000. A scale
            word printed with the number applies instead, whatever this holds.

    Returns:
        The signed amount in base units, or ``None`` for a nil, a non-number, or a number with no
        scale word and a bad scale.
    """
    number = _read_printed(printed)
    if number is None:
        return None
    if number.word_scale is not None:
        value = number.amount * number.word_scale
    elif isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale not in _SCALES:
        return None
    else:
        value = number.amount * scale
    return value if math.isfinite(value) else None


def _normalise(text: str) -> str:
    """Normalise a label or quote: NFKC, casefold, ``\\|``, markers, whitespace, a final colon."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("\\|", "|")
    return " ".join(_MARKER.sub("", text).split()).removesuffix(":").rstrip()


def _labels_match(left: str, right: str) -> bool:
    """Two labels match when they are equal or one contains the other; an empty one never."""
    a, b = _normalise(left), _normalise(right)
    return bool(a and b) and (a in b or b in a)


def _cells(line: str) -> list[str]:
    """Split a table line on its unescaped bars."""
    cells = [cell.strip() for cell in _CELL_BAR.split(line.strip())]
    return cells[1:-1] if len(cells) >= 2 else cells


def _numbers_in(text: str) -> set[float]:
    """Every number in a text, unsigned, as whole tokens: ``1,234`` is not in ``11,234``."""
    return {float(token.replace(",", "")) for token in _NUMBER_TOKEN.findall(text)}


def _cell_holds(text: str, row: str, column: str, printed: str) -> bool:
    """Whether the cell at this row and column prints the same signed number."""
    lines = [line for line in text.splitlines() if line.startswith("|")]
    if not lines:
        return False
    columns = [
        index for index, heading in enumerate(_cells(lines[0]))
        if index > 0 and _labels_match(column, heading)
    ]
    wanted = printed_value(printed, 1)
    for line in lines[1:]:
        cells = _cells(line)
        if not cells or _SEPARATOR_CELL.fullmatch(cells[0]):
            continue
        if _labels_match(row, cells[0]) and any(
            index < len(cells) and printed_value(cells[index], 1) == wanted for index in columns
        ):
            return True
    return False


def match_cell(text: str, row: str | None, column: str | None, printed: str) -> str:
    """How a cited number lines up with a chunk's text.

    Args:
        text: The chunk's text.
        row: The row label as cited, ``None`` for a number in prose.
        column: The column label as cited, ``None`` for a number in prose.
        printed: The number as cited.

    Returns:
        ``"exact"``, ``"printed"`` or ``"none"``.
    """
    number = _read_printed(printed)
    if number is None:
        return _NONE
    in_text = abs(number.amount) in _numbers_in(text)
    if row is None or column is None:
        return _EXACT if in_text else _NONE
    if _cell_holds(text, row, column, printed):
        return _EXACT
    return _PRINTED if in_text else _NONE


def build_excerpts(chunks: Sequence[RetrievedChunk]) -> list[dict[str, Any]]:
    """The excerpts the model sees: a closed set of fields, so nothing else on a point can leak.

    Args:
        chunks: The retrieved chunks.

    Returns:
        One object per chunk, in chunk order.
    """
    return [
        {
            "chunk_id": chunk.chunk_id,
            "ticker": chunk.ticker,
            "company_name": chunk.company_name,
            "fiscal_year": chunk.fiscal_year,
            "form_type": chunk.form_type,
            "section": chunk.section_label,
            "kind": "table" if chunk.contains_table else "prose",
            "text": chunk.text,
        }
        for chunk in chunks
    ]


class ReplyOutcome(NamedTuple):
    """What a reply comes to.

    Attributes:
        status: ``answered``, or the decline's status.
        reason: Why it declined, ``None`` when answered.
        text: The answer's text.
        numeric_values: The value, then each row's.
        rows: ``{"entity", "value"}`` each.
        chunk_ids: The chunks cited.
        refused: Whether it declined.
        hedged: Whether the answer is only partly supported.
        calculations: ``{"expr", "bindings", "value"}`` each, the value's first.
        unverified: ``{"chunk_id", "detail"}`` each.
    """

    status: str
    reason: str | None
    text: str
    numeric_values: list[float]
    rows: list[dict[str, Any]]
    chunk_ids: list[str]
    refused: bool
    hedged: bool
    calculations: list[dict[str, Any]]
    unverified: list[dict[str, str]]


def _declined(
    status: str, reason: str, *, text: str = "", unverified: list[dict[str, str]] | None = None
) -> ReplyOutcome:
    return ReplyOutcome(
        status, reason, text or reason, [], [], [], True, False, [], unverified or []
    )


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _label(value: object) -> str | None:
    """A label or printed number as text; the model may write ``2024`` for ``"2024"``."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _check_inputs(
    inputs: list[Any],
    chunks: Mapping[str, RetrievedChunk],
    unverified: list[dict[str, str]],
) -> tuple[dict[str, float | None], list[str]]:
    """Resolve the inputs; return the bindings and the chunk of each resolved input."""
    bindings: dict[str, float | None] = {}
    cited: list[str] = []
    seen: set[str] = set()
    for raw in inputs:
        if not isinstance(raw, dict):
            continue
        input_id = raw.get("id")
        if not isinstance(input_id, str) or not input_id.isidentifier() or input_id in seen:
            continue
        seen.add(input_id)
        chunk = chunks.get(str(raw.get("chunk_id")))
        printed = _label(raw.get("printed"))
        if chunk is None or printed is None:
            continue
        value = printed_value(printed, raw.get("scale"))
        row, column = _label(raw.get("row")), _label(raw.get("column"))
        match = match_cell(chunk.text, row, column, printed)
        if value is None or match == _NONE:
            continue
        bindings[input_id] = value
        cited.append(chunk.chunk_id)
        if match == _PRINTED:
            unverified.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "detail": f"{printed!r} is not at row {row!r}, column {column!r}",
                }
            )
    return bindings, cited


def _check_passages(
    passages: list[Any],
    chunks: Mapping[str, RetrievedChunk],
    unverified: list[dict[str, str]],
) -> list[str]:
    """Keep the passages of chunks given; a quote not in its chunk stays cited, unverified."""
    kept: list[str] = []
    for raw in passages:
        chunk = chunks.get(str(raw.get("chunk_id"))) if isinstance(raw, dict) else None
        if chunk is None:
            continue
        kept.append(chunk.chunk_id)
        quote = _normalise(_label(raw.get("quote")) or "")
        if not quote or quote not in _normalise(chunk.text):
            unverified.append(
                {"chunk_id": chunk.chunk_id, "detail": "the quote is not in the chunk"}
            )
    return kept


class _Calculator:
    """The calculator, keeping a record of each evaluation."""

    def __init__(self, bindings: dict[str, float | None]) -> None:
        self._tool = XbrlCalculatorTool()
        self._bindings = bindings
        self.calculations: list[dict[str, Any]] = []

    def not_meaningful(self, expr: str) -> str | None:
        return self._tool.not_meaningful_reason(expr, self._bindings)

    def evaluate(self, expr: str) -> float:
        value = self._tool.evaluate(expr, self._bindings)
        if not math.isfinite(value):
            raise CalculatorError(f"expr {expr!r} is not a finite number")
        used = self._tool.referenced_step_ids(expr)
        self.calculations.append(
            {"expr": expr, "bindings": {k: self._bindings[k] for k in used}, "value": value}
        )
        return value


def _evaluate_rows(
    raw_rows: list[Any], calculator: _Calculator
) -> tuple[list[dict[str, Any]], bool]:
    """Evaluate the rows; return those that stand, and whether any was dropped."""
    rows: list[dict[str, Any]] = []
    dropped = False
    for raw in raw_rows:
        expr = raw.get("expr") if isinstance(raw, dict) else None
        if not isinstance(expr, str) or calculator.not_meaningful(expr) is not None:
            dropped = True
            continue
        try:
            rows.append({"entity": str(raw.get("entity", "")), "value": calculator.evaluate(expr)})
        except CalculatorError:
            dropped = True
    return rows, dropped


def resolve_reply(
    reply: Mapping[str, Any] | None, chunks: Sequence[RetrievedChunk]
) -> ReplyOutcome:
    """Turn the model's reply into an answer or a decline.

    Args:
        reply: The reply's JSON object, ``None`` when it held none.
        chunks: The chunks given in this call.

    Returns:
        The outcome.
    """
    if reply is None:
        return _declined("unparseable", "the reply holds no JSON object")
    raw_text = reply.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    if reply.get("refused") is True:
        return _declined("model_declined", "the model declined", text=text)

    given = {chunk.chunk_id: chunk for chunk in chunks}
    unverified: list[dict[str, str]] = []
    bindings, input_chunks = _check_inputs(_as_list(reply.get("inputs")), given, unverified)
    passage_chunks = _check_passages(_as_list(reply.get("passages")), given, unverified)
    calculator = _Calculator(bindings)

    value: float | None = None
    not_meaningful: str | None = None
    expr = reply.get("value_expr")
    if expr is not None and expr != "":
        not_meaningful = calculator.not_meaningful(expr)
        if not_meaningful is None:
            try:
                value = calculator.evaluate(expr)
            except CalculatorError as exc:
                return _declined("unsupported", str(exc), unverified=unverified)

    rows, row_dropped = _evaluate_rows(_as_list(reply.get("rows")), calculator)
    if value is None and not_meaningful is None:
        if row_dropped and not rows:
            return _declined("unsupported", "no row could be worked out", unverified=unverified)
        if not rows and not input_chunks and not passage_chunks:
            return _declined(
                "unsupported", "no resolved input or passage supports it", unverified=unverified
            )

    return ReplyOutcome(
        status="answered",
        reason=None,
        text=f"{text} {not_meaningful}".strip() if not_meaningful else text,
        numeric_values=[*([] if value is None else [value]), *(row["value"] for row in rows)],
        rows=rows,
        chunk_ids=list(dict.fromkeys([*input_chunks, *passage_chunks])),
        refused=False,
        hedged=reply.get("hedged") is True
        or bool(unverified or row_dropped or not_meaningful),
        calculations=calculator.calculations,
        unverified=unverified,
    )
