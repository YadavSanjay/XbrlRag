"""Turning a baseline system's reply into a gradable `SystemAnswer`.

Baselines (naive RAG, long-context, closed-book) answer in prose or in a small JSON
object, never with XbrlRag's ``execution_result``. Both helpers here are deterministic,
so a baseline's numbers are read the same way on every run and the grader stays free
of an LLM judge.

Spec: [docs/specs/arm-neutral-grading.md](../../../docs/specs/arm-neutral-grading.md).
"""

from __future__ import annotations

import functools
import json
import re
import typing
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from src.eval.core.system import SystemAnswer

# Scale words and letters, as powers of ten.
_SCALE_WORDS = {
    "thousand": 3,
    "thousands": 3,
    "million": 6,
    "millions": 6,
    "mn": 6,
    "mm": 6,
    "billion": 9,
    "billions": 9,
    "bn": 9,
    "trillion": 12,
    "trillions": 12,
    "tn": 12,
}
_SCALE_LETTERS = {"k": 3, "m": 6, "b": 9, "t": 12}

# One candidate amount. Every candidate is matched in full and then accepted or
# skipped in code, so the regex never backtracks into a shorter, wrong number.
_NUMBER = re.compile(
    r"""
    (?<![\w.])
    (?P<open>\()?
    (?P<sign>[-+−])?
    (?:(?P<currency>US\$|NT\$|\$|USD|TWD)\ ?)?
    (?P<digits>\d{1,3}(?:,\d{3})+|\d+)
    (?P<decimal>\.\d+)?
    (?P<close>(?(open)\)|(?!)))?
    (?:
        (?P<percent>%|\ ?percent\b)
      | \ ?(?P<word>thousands?|millions?|mn|mm|billions?|bn|trillions?|tn)\b
      | (?P<letter>[kKmMbBtT])(?![A-Za-z])
    )?
    """,
    re.VERBOSE | re.IGNORECASE,
)
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_FORM_NAME_TAIL = re.compile(r"-[A-Za-z]")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _is_year(match: re.Match[str]) -> bool:
    digits = match["digits"]
    bare = not (
        match["currency"]
        or match["decimal"]
        or match["percent"]
        or match["word"]
        or match["letter"]
    )
    return bare and len(digits) == 4 and digits.isdigit() and 1900 <= int(digits) <= 2100


def _is_bare(match: re.Match[str]) -> bool:
    """Whether a candidate is a plain integer: no currency, decimal, percent or scale, and
    not a parenthesised negative (grader-v9-number-reading, requirement 3)."""
    return not (
        match["currency"]
        or match["decimal"]
        or match["percent"]
        or match["word"]
        or match["letter"]
        or (match["open"] and match["close"])
    )


def _scan(text: str) -> Iterator[tuple[re.Match[str], float, float]]:
    """Yields each stated amount of a text as ``(match, value, quantum)``, in order.

    The value carries the scale and the sign (a minus or parentheses); the quantum is
    the place value of the last stated digit times the scale. Years, dates and form
    names are skipped. The match is against the text with ISO dates blanked out, which
    keeps every position of the original text.
    """
    # Blank out ISO dates (same length, so positions are kept) before scanning.
    masked = _ISO_DATE.sub(lambda m: " " * len(m.group()), text)
    # Parts of written dates are never amounts (grading-dont-care-and-ties, req. 6).
    date_parts = {(start, end) for start, end, _v in _date_spans(text)}
    # Bare counts of days or weeks and rank numbers are never amounts
    # (grader-v9-number-reading, requirement 3).
    exempt = _exempt_spans(text)
    for match in _NUMBER.finditer(masked):
        core_end = match.end("decimal") if match["decimal"] else match.end("digits")
        if _FORM_NAME_TAIL.match(masked, core_end) or _is_year(match):
            continue
        if (match.start("digits"), core_end) in date_parts:
            continue
        if exempt and _is_bare(match) and match.span("digits") in exempt:
            continue
        amount = Decimal(match["digits"].replace(",", "") + (match["decimal"] or ""))
        scale = 0
        if match["word"]:
            scale = _SCALE_WORDS[match["word"].lower()]
        elif match["letter"]:
            scale = _SCALE_LETTERS[match["letter"].lower()]
        value = float(amount.scaleb(scale))
        if match["sign"] in ("-", "−"):
            value = -value
        if match["open"] and match["close"]:
            value = -value
        exponent = amount.as_tuple().exponent
        places = exponent if isinstance(exponent, int) else 0
        yield match, value, float(Decimal(1).scaleb(places + scale))


def extract_numbers(text: str) -> list[float]:
    """Returns the amounts stated in a text, in order of appearance.

    Args:
        text: Free text, such as a baseline system's answer.

    Returns:
        Raw amounts with scale words and signs applied; years, fiscal-period labels,
        dates and form names are skipped.
    """
    return [value for _, value, _ in _scan(text)]


def _first_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a reply, whether bare, fenced or within prose."""
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for start in (i for i, ch in enumerate(candidate) if ch == "{"):
            try:
                obj, _ = decoder.raw_decode(candidate, start)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def _as_amount(value: Any) -> float | None:
    """A JSON number, or the first amount in a numeric string; otherwise None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        found = extract_numbers(value)
        return found[0] if found else None
    return None


def parse_structured_answer(text: str) -> SystemAnswer:
    """Parses a baseline's JSON answer object into a SystemAnswer.

    Args:
        text: The system's reply, holding a JSON object bare, fenced or within prose.

    Returns:
        The gradable answer; falls back to free-text extraction when no object parses.
    """
    obj = _first_object(text)
    if obj is None:
        # Read with direction words, so "declined 5.2%" states -5.2 (requirement 4).
        values = [n.value for n in extract_stated_numbers(text)]
        return SystemAnswer(text=text, numeric_values=values, refused=not values)

    numeric_values: list[float] = []
    value = _as_amount(obj.get("value"))
    if value is not None:
        numeric_values.append(value)

    rows: list[dict] = []
    raw_rows = obj.get("rows")
    for raw in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(raw, dict):
            continue
        row_value = _as_amount(raw.get("value"))
        if row_value is None:
            continue
        rows.append({"entity": str(raw.get("entity", "")), "value": row_value})
        numeric_values.append(row_value)

    chunks = obj.get("cited_chunks")
    return SystemAnswer(
        text=text,
        numeric_values=numeric_values,
        rows=rows,
        fact_ids=[],
        chunk_ids=[str(c) for c in chunks] if isinstance(chunks, list) else [],
        refused=obj.get("refused") is True,
        hedged=obj.get("hedged") is True,
    )


def answer_prose(text: str) -> str:
    """The answer sentence of a baseline reply: its JSON object's ``"text"``, else the reply.

    Spec: docs/specs/judge-baseline-claim-text.md (requirement 1).

    Args:
        text: A system's reply, holding a JSON answer object bare, fenced or within prose,
            or plain prose.

    Returns:
        The ``"text"`` string of the object ``parse_structured_answer`` reads, unchanged;
        ``text`` itself when there is no object or its ``"text"`` is not a string.
    """
    obj = _first_object(text)
    if obj is not None and isinstance(obj.get("text"), str):
        return obj["text"]
    return text


class StatedNumber(typing.NamedTuple):
    """One amount a text states, with how precisely and in which direction it states it.

    Spec: docs/specs/grading-precision.md (requirements 3, 4 and 9).

    Attributes:
        value: The amount with scale, sign and a governing down word applied.
        quantum: The place value of the last stated digit times the scale
            ("$1.2 billion" is 1e8).
        percent: Whether the amount was stated as a percentage.
        direction: -1 for a governing down word, 1 for an up word, else 0.
        loss: Whether a loss word ("net loss of", "loss per share was", "... net
            loss") governs the amount; the value is not signed by it (requirement 9).
    """

    value: float
    quantum: float
    percent: bool
    direction: int
    loss: bool


# Words that may stand between a direction word and the number it governs.
_FILLERS = frozenset({
    "by", "of", "a", "an", "about", "around", "approximately", "roughly", "nearly", "some",
})
# Words that may also stand between a loss word and the number it governs.
_LOSS_FILLERS = _FILLERS | {"was", "were", "is", "totaled", "totalled"}
_LOSS_MODIFIERS = r"net|operating|pretax|pre-tax|comprehensive"
# A loss word, with a directly following per-share phrase, at the end of a text.
_LOSS_TAIL = re.compile(
    r"(?<![A-Za-z])(?:losses|loss)"
    r"(?:\s+per\s+(?:(?:basic\s+and\s+diluted|diluted|basic)\s+)?share)?$",
    re.IGNORECASE,
)
# What makes a loss word not one: an "or", "and", "/", "impairment" or an opening
# parenthesis right before it ("gain or loss", "income/loss", "net income (loss)").
_NOT_A_LOSS = re.compile(
    r"(?:(?<![A-Za-z])(?:or|and|impairment) +|/ *|\( *)$", re.IGNORECASE
)
# A loss word directly after a number: "$1.2 billion net loss", "$5.34 loss per share".
_LOSS_AFTER = re.compile(
    rf" +(?:(?:{_LOSS_MODIFIERS}) +)?(?:losses|loss)(?![A-Za-z])", re.IGNORECASE
)
# A currency prefix written outside an amount's parentheses: "$(5.34)".
_CURRENCY_TAIL = re.compile(r"(?:US\$|NT\$|\$|USD|TWD) ?$")
_LAST_WORD = re.compile(r"(?<![A-Za-z])([A-Za-z]+)$")
_NEXT_WORD = re.compile(r" +([A-Za-z]+)(?![A-Za-z])")


@functools.cache
def _direction_words() -> tuple[frozenset[str], frozenset[str]]:
    """The down and up words of requirement 4: the v2 lexicons plus "down" and "up"."""
    # Imported here: the graders import this module, and v2 imports the graders.
    from src.eval.core.graders.v2 import DOWN_WORDS, UP_WORDS

    return DOWN_WORDS | {"down"}, UP_WORDS | {"up"}


def _strip_fillers(before: str, fillers: frozenset[str], colon: bool) -> str:
    """Drops the trailing spaces (and colons) and filler words that precede a number."""
    while True:
        trimmed = before.rstrip(" :" if colon else " ")
        word = _LAST_WORD.search(trimmed)
        if word is None or word.group(1).lower() not in fillers:
            return trimmed
        before = trimmed[: word.start()]


def _direction(before: str, after: str) -> int:
    """The direction a word directly before or after a number gives it (requirement 4)."""
    down, up = _direction_words()
    previous = _LAST_WORD.search(_strip_fillers(before, _FILLERS, colon=False))
    following = _NEXT_WORD.match(after)
    for word in (previous, following):
        token = word.group(1).lower() if word is not None else ""
        if token in down:
            return -1
        if token in up:
            return 1
    return 0


def _governed_by_loss(before: str, after: str) -> bool:
    """Whether a loss word governs a number (requirement 9)."""
    head = _strip_fillers(before, _LOSS_FILLERS, colon=True)
    tail = _LOSS_TAIL.search(head)
    if tail is not None and not _NOT_A_LOSS.search(head[: tail.start()]):
        return True
    return _LOSS_AFTER.match(after) is not None


def extract_stated_numbers(text: str) -> list[StatedNumber]:
    """Returns the amounts stated in a text with their stated precision, in order.

    Spec: docs/specs/grading-precision.md (requirements 3, 4 and 9).

    Args:
        text: Free text, such as an answer.

    Returns:
        One ``StatedNumber`` per amount ``extract_numbers`` would return. A down word
        governing an amount makes its value negative.
    """
    found: list[StatedNumber] = []
    for match, value, quantum in _scan(text):
        before, after = text[: match.start()], text[match.end():]
        if match["open"]:
            before = _CURRENCY_TAIL.sub("", before)
        direction = _direction(before, after)
        if direction < 0:
            value = -abs(value)
        found.append(StatedNumber(
            value=value,
            quantum=quantum,
            percent=bool(match["percent"]),
            direction=direction,
            loss=_governed_by_loss(before, after),
        ))
    return found


def date_components(text: str) -> list[float]:
    """The numbers a text states as parts of written dates, in text order.

    Spec: docs/specs/grading-dont-care-and-ties.md (requirement 6).

    Args:
        text: Free text, such as an answer.

    Returns:
        Every day, month number and year of every written date ("December 31, 2024",
        "31 December 2024", "12/31/2024"), each as a float, in order of position.
    """
    return [value for _start, _end, value in _date_spans(text)]


def graded_values(text: str, values: typing.Sequence[float]) -> list[float]:
    """The produced values a grader reads: copies stated only as date parts dropped.

    Spec: docs/specs/grading-dont-care-and-ties.md (requirement 7).

    Args:
        text: The answer's text.
        values: The answer's produced values, as stored.

    Returns:
        ``values`` in order, less the earliest copies of each value the text states only
        as a date component.
    """
    dates = date_components(text or "")
    if not dates:
        return list(values)
    stated = [n.value for n in extract_stated_numbers(text)]
    drop: dict[float, int] = {}
    for v in dict.fromkeys(values):
        if not any(d == v for d in dates):
            continue
        n_p = sum(1 for x in values if x == v)
        n_a = sum(1 for x in stated if x == v)
        if n_p > n_a:
            drop[v] = n_p - n_a
    out: list[float] = []
    for v in values:
        if drop.get(v, 0) > 0:
            drop[v] -= 1
            continue
        out.append(v)
    return out


# Written dates (requirement 6). A month word: a name, its three-letter abbreviation or
# "Sept", optionally with a ".", not glued to a letter; longest alternatives first.
_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
)
_MONTH_WORD = (
    r"(?<![A-Za-z])(?:"
    + "|".join(sorted({*_MONTH_NAMES, *(m[:3] for m in _MONTH_NAMES), "sept"},
                      key=len, reverse=True))
    + r")(?![A-Za-z])\.?"
)
_DAY = r"(?<!\d)(?P<day>\d{1,2})(?:st|nd|rd|th)?(?![A-Za-z0-9])"
_YEAR = r"(?P<year>\d{4}|'\d{2})(?![A-Za-z0-9])"
_YEAR_TAIL = rf"(?:(?: *, *| +){_YEAR})?"
_MONTH_DAY = re.compile(rf"{_MONTH_WORD} +{_DAY}{_YEAR_TAIL}", re.IGNORECASE)
_DAY_MONTH = re.compile(rf"{_DAY} +(?:of +)?{_MONTH_WORD}{_YEAR_TAIL}", re.IGNORECASE)
_SLASH_DATE = re.compile(
    r"(?<![\d/.])(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<c>\d{4}|\d{2})(?![\d/])"
)


def _year_span(match: re.Match[str]) -> tuple[int, int, float] | None:
    if match["year"] is None:
        return None
    start = match.start("year") + (1 if match["year"].startswith("'") else 0)
    return start, match.end("year"), float(match["year"].lstrip("'"))


def _date_spans(text: str) -> list[tuple[int, int, float]]:
    """Each written date's components as ``(start, end, value)``, in text order.

    A component's span is its digits ("31" of "31st", "24" of "'24"). Dates that overlap
    an earlier-starting (or, at the same start, longer) date are dropped.
    """
    masked = _ISO_DATE.sub(lambda m: " " * len(m.group()), text)
    dates: list[tuple[int, int, list[tuple[int, int, float]]]] = []
    for pattern in (_MONTH_DAY, _DAY_MONTH):
        for m in pattern.finditer(masked):
            if not 1 <= int(m["day"]) <= 31:
                continue
            parts = [(m.start("day"), m.end("day"), float(m["day"]))]
            year = _year_span(m)
            if year is not None:
                parts.append(year)
            dates.append((m.start(), m.end(), parts))
    for m in _SLASH_DATE.finditer(masked):
        a, b = int(m["a"]), int(m["b"])
        if not (1 <= a <= 31 and 1 <= b <= 31):
            continue
        dates.append((m.start(), m.end(), [
            (m.start(g), m.end(g), float(m[g])) for g in ("a", "b", "c")]))
    dates.sort(key=lambda d: (d[0], -(d[1] - d[0])))
    out: list[tuple[int, int, float]] = []
    last_end = -1
    for start, end, parts in dates:
        if start < last_end:
            continue
        last_end = end
        out.extend(parts)
    return out


# The synthesizer's two fixed fallback summaries (XbrlRagSynthesizer._degraded_response),
# copied verbatim: an unusable reply (E) and a provider refusal (R).
_SYNTHESIS_ERROR_TEXT = (
    "This answer could not be completed: the synthesis step returned a response that was not "
    "valid JSON. The retrieved facts are preserved in execution_result; please retry the query."
)
_SYNTHESIS_REFUSAL_TEXT = (
    "This answer could not be completed: the model declined to answer the synthesis step. "
    "The retrieved facts are preserved in execution_result."
)

# The screen disclosure sentences (screen_disclosure_text): Universe, Not ranked, Fiscal
# years. A ticker has no whitespace, parentheses or ";"; a phrase's parentheses nest once.
_TICKER = r"[^\s();]+"
_PHRASE = r"(?:[^()\n]|\([^()\n]*\))*"
_ISO_DAY = r"\d{4}-\d{2}-\d{2}"
_DISCLOSURE_FORMS = (
    re.compile(r"Universe: the (?:covered companies|[^()\n]+ group) "
               r"\(\d+ compan(?:y|ies); \d+ ranked for FY\d+\)\."),
    re.compile(rf"Not ranked: {_TICKER} \({_PHRASE}\)(?:; {_TICKER} \({_PHRASE}\))*\."),
    re.compile(rf"Fiscal years end on different dates: {_TICKER} {_ISO_DAY}"
               rf"(?:, {_TICKER} {_ISO_DAY})*\."),
)


def synthesis_failure(text: str) -> str | None:
    """Whether a text carries the synthesizer's fixed fallback summary, and which.

    Spec: docs/specs/grader-synthesis-error.md (requirement 1).

    Args:
        text: An answer's text.

    Returns:
        ``"error"`` for the unusable-reply fallback, ``"refusal"`` for the refusal
        fallback, ``None`` otherwise.
    """
    text = text or ""
    if _SYNTHESIS_ERROR_TEXT in text:
        return "error"
    if _SYNTHESIS_REFUSAL_TEXT in text:
        return "refusal"
    return None


def disclosure_spans(text: str) -> list[tuple[int, int]]:
    """The spans of the screen disclosure sentences the pipeline appends to an answer.

    Spec: docs/specs/grader-synthesis-error.md (requirement 3).

    Args:
        text: An answer's text.

    Returns:
        ``(start, end)`` of every Universe / Not ranked / Fiscal years sentence, sorted.
    """
    text = text or ""
    found = sorted(
        {(m.start(), m.end()) for form in _DISCLOSURE_FORMS for m in form.finditer(text)},
        key=lambda span: (span[0], -(span[1] - span[0])),
    )
    out: list[tuple[int, int]] = []
    for start, end in found:
        if out and start < out[-1][1]:
            continue
        out.append((start, end))
    return out


def blank_disclosures(text: str) -> str:
    """``text`` with every disclosure sentence replaced by as many spaces.

    Spec: docs/specs/grader-synthesis-error.md (requirements 4 and 5).

    Args:
        text: An answer's text.

    Returns:
        A text of the same length, with each ``disclosure_spans`` span blanked.
    """
    text = text or ""
    chars = list(text)
    for start, end in disclosure_spans(text):
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def undisclosed_values(text: str, values: typing.Sequence[float]) -> list[float]:
    """The produced values a grader reads: copies stated only in disclosure sentences dropped.

    Spec: docs/specs/grader-synthesis-error.md (requirement 4).

    Args:
        text: The answer's text.
        values: The answer's produced values.

    Returns:
        ``values`` in order, less the earliest copies of each value the text states only
        inside a disclosure sentence.
    """
    text = text or ""
    spans = disclosure_spans(text)
    if not spans:
        return list(values)
    disclosed = [n.value for s, e in spans for n in extract_stated_numbers(text[s:e])]
    stated = [n.value for n in extract_stated_numbers(blank_disclosures(text))]
    drop: dict[float, int] = {}
    for v in dict.fromkeys(values):
        if not any(d == v for d in disclosed):
            continue
        n_p = sum(1 for x in values if x == v)
        n_a = sum(1 for x in stated if x == v)
        if n_p > n_a:
            drop[v] = n_p - n_a
    out: list[float] = []
    for v in values:
        if drop.get(v, 0) > 0:
            drop[v] -= 1
            continue
        out.append(v)
    return out


# Period lengths (grader-v9-number-reading, requirement 1). An integer, with commas as
# thousands separators; a period-length phrase is integers joined by a joiner, then a space
# or "-" and a day or week unit.
_INTEGER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
_INTEGER_RE = re.compile(_INTEGER)
_PERIOD_JOINER = r"(?:/|-|\u2013| or | and | to |- or |- and |- to )"
_PERIOD_LENGTH = re.compile(
    rf"(?<![A-Za-z0-9.,$])(?P<ints>{_INTEGER}(?:{_PERIOD_JOINER}{_INTEGER})*)"
    r"[ -](?:days|day|weeks|week)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# Ranks (requirement 2): a numeric ordinal, and the universe directly after a rank.
_NUMERIC_ORDINAL = re.compile(r"(?<![A-Za-z0-9])(?P<n>\d+)(?:st|nd|rd|th)(?![A-Za-z0-9])",
                              re.IGNORECASE)
_RANK_UNIVERSE = re.compile(
    rf" (?:out )?of (?P<m>{_INTEGER})(?!\d|[.,]\d|[A-Za-z])", re.IGNORECASE
)


@functools.cache
def _rank_grammar() -> tuple[re.Pattern[str], typing.Callable[[str, int], bool]]:
    """peer-rank-grading's numbered-rank form and its first-person count test."""
    # Imported here: the graders import this module, and v2 imports the graders.
    from src.eval.core.graders.v2 import _NUMBERED, is_count_phrase

    return _NUMBERED, is_count_phrase


def period_length_spans(text: str) -> list[tuple[int, int]]:
    """The digit spans of every count of days or weeks the text states.

    Spec: docs/specs/grader-v9-number-reading.md (requirement 1).

    Args:
        text: An answer's text.

    Returns:
        ``(start, end)`` of each integer of each period-length phrase, sorted, distinct.
    """
    spans: set[tuple[int, int]] = set()
    for m in _PERIOD_LENGTH.finditer(text or ""):
        base = m.start("ints")
        for i in _INTEGER_RE.finditer(m["ints"]):
            spans.add((base + i.start(), base + i.end()))
    return sorted(spans)


def rank_spans(text: str) -> list[tuple[int, int]]:
    """The digit spans of every rank number the text states, and of a rank's universe.

    Spec: docs/specs/grader-v9-number-reading.md (requirement 2).

    Args:
        text: An answer's text.

    Returns:
        ``(start, end)`` of each rank number, sorted, distinct.
    """
    text = text or ""
    numbered, is_count = _rank_grammar()
    spans: set[tuple[int, int]] = set()
    rank_ends: list[int] = []
    for m in _NUMERIC_ORDINAL.finditer(text):
        spans.add(m.span("n"))
        rank_ends.append(m.end())
    for m in numbered.finditer(text):
        if m.group(0)[0] in "rR" and is_count(text, m.start()):
            continue
        spans.add(m.span("n"))
        rank_ends.append(m.end())
    for end in rank_ends:
        universe = _RANK_UNIVERSE.match(text, end)
        if universe is not None:
            spans.add(universe.span("m"))
    return sorted(spans)


def _exempt_spans(text: str) -> set[tuple[int, int]]:
    """The digit spans of the period lengths and rank numbers of a text."""
    return {*period_length_spans(text), *rank_spans(text)}


def unexempt_values(text: str, values: typing.Sequence[float]) -> list[float]:
    """The produced values a grader reads: copies stated only as period lengths or ranks dropped.

    Spec: docs/specs/grader-v9-number-reading.md (requirement 4).

    Args:
        text: The answer's text.
        values: The answer's produced values, as stored.

    Returns:
        ``values`` in order, less the earliest copies of each value the text states only as
        a count of days or weeks or as a rank number.
    """
    text = text or ""
    exempt = {float(text[s:e].replace(",", "")) for s, e in _exempt_spans(text)}
    if not exempt:
        return list(values)
    stated = [n.value for n in extract_stated_numbers(text)]
    drop: dict[float, int] = {}
    for v in dict.fromkeys(values):
        if abs(v) not in exempt:
            continue
        n_p = sum(1 for x in values if x == v)
        n_a = sum(1 for x in stated if x == v)
        if n_p > n_a:
            drop[v] = n_p - n_a
    out: list[float] = []
    for v in values:
        if drop.get(v, 0) > 0:
            drop[v] -= 1
            continue
        out.append(v)
    return out


# Aggregate sentences (grading-stated-aggregate, requirement 1). A sentence ends at ".", "!"
# or "?" before whitespace or the end of the text (so never at a decimal point), or at a
# newline. Bare "total" is not a sum word: it names metrics ("total current assets").
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")
_AGGREGATE_WORDS = {
    "median": re.compile(r"\bmedian\b", re.IGNORECASE),
    "sum": re.compile(
        r"\b(?:sum|combined|aggregate|altogether|together|in\s+total|a\s+total\s+of|"
        r"totaled|totalled|totaling|totalling|adds?\s+up\s+to)\b",
        re.IGNORECASE,
    ),
}
_DECLINE_WORD = re.compile(
    r"\b(?:not|no|cannot|can['’]t|couldn['’]t|unable|unavailable|insufficient"
    r"|without|\w+n['’]t)\b"
    r"|(?<![A-Za-z0-9])n/a(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def aggregate_sentences(text: str, sub_kind: str) -> list[tuple[int, int]]:
    """The sentences of a text that name the aggregate and do not decline.

    Spec: docs/specs/grading-stated-aggregate.md (requirement 1).

    Args:
        text: An answer's text.
        sub_kind: The case's v2 sub-kind; ``"median"`` and ``"sum"`` have aggregate words,
            any other sub-kind has none.

    Returns:
        ``(start, end)`` of each such sentence, in text order, surrounding whitespace
        excluded.
    """
    word = _AGGREGATE_WORDS.get(sub_kind)
    if word is None or not text:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    for end in [*(m.end() for m in _SENTENCE_END.finditer(text)), len(text)]:
        piece = text[start:end]
        lead = len(piece) - len(piece.lstrip())
        s, e = start + lead, start + len(piece.rstrip())
        start = end
        if s >= e:
            continue
        sentence = text[s:e]
        if word.search(sentence) and not _DECLINE_WORD.search(sentence):
            spans.append((s, e))
    return spans
