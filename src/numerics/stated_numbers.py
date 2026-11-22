"""Reads the numbers a piece of prose states, and compares them at a stated precision.

A *stated number* is a run of digits with an optional currency prefix, thousands commas, a
decimal part and either a scale word (``million``, ``bn``, a glued ``k`` ...) or a percent.
Years, fiscal labels, dates, form names, ranks and counts are exempt and never returned.
Each number carries its *step*: the unit of its last stated digit with the scale applied
(``$391.0 billion`` has step 1e8). The rules are those of docs/specs/citation-integrity.md
("Stated numbers"), and the precision helpers follow the number-comparison rules approved on
2026-11-07 (docs/design/XbrlRag_Test_Strategy.md#number-comparison).

This module has no retriever or eval imports, so the query pipeline's citation checks and the
eval grader can share it.

Typical use:

    for n in read_stated_numbers("Revenue was $391.0 billion."):
        within_precision(n, 391_035_000_000.0, rule_step=1e6)  # True
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal

# Precision of a per-share money amount (cents) and of a percentage (0.1 point).
PER_SHARE_STEP = 0.01
PERCENT_POINT_STEP = 0.1

# Relative slack added to every comparison, for float rounding of scaled values.
FLOAT_SLACK = 1e-9

_SCALES: dict[str, int] = {
    "thousand": 3, "thousands": 3, "k": 3,
    "million": 6, "millions": 6, "mn": 6, "mm": 6, "m": 6,
    "billion": 9, "billions": 9, "bn": 9, "b": 9,
    "trillion": 12, "trillions": 12, "tn": 12, "t": 12,
}

_NUMBER = re.compile(
    r"(?P<cur>US\$|NT\$|USD\s?|\$|€|£|¥)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
)
_ORDINAL = re.compile(r"(?:st|nd|rd|th)(?![A-Za-z])", re.IGNORECASE)
_COMPOUND = re.compile(r"-[A-Za-z]")
_SCALE_WORD = re.compile(
    r"\s?(thousands?|millions?|mn|mm|billions?|bn|trillions?|tn)(?![A-Za-z])", re.IGNORECASE
)
_SCALE_LETTER = re.compile(r"([kmbt])(?![A-Za-z])", re.IGNORECASE)
_PERCENT = re.compile(r"\s?%|\s?percent(?![A-Za-z])", re.IGNORECASE)
_MULTIPLE = re.compile(r"x(?![A-Za-z])", re.IGNORECASE)
_COUNT = re.compile(
    r"\s+(?:companies|company|filers|filer|firms|issuers|peers|months|month|weeks|years|"
    r"quarters|days)(?![A-Za-z])",
    re.IGNORECASE,
)
_RANK_BEFORE = re.compile(
    r"(?:#|\b(?:no\.|number|rank|ranked|ranks|top|bottom)\s*)$", re.IGNORECASE
)
_MONTH_BEFORE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+$",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")


@dataclass(frozen=True)
class StatedNumber:
    """One number a text states.

    Attributes:
        text: The text's own substring for the number (currency and scale included, sign
            excluded).
        value: The unsigned magnitude with the scale applied; a percent stays in points.
        step: The unit of the last stated digit with the scale applied.
        percent: Whether the number is stated as a percent.
        start: Offset of ``text`` in the source text.
    """

    text: str
    value: float
    step: float
    percent: bool
    start: int

    def to_dict(self) -> dict:
        """Returns ``{"text", "value", "step", "percent"}``."""
        return {"text": self.text, "value": self.value, "step": self.step,
                "percent": self.percent}


def read_stated_numbers(text: str) -> list[StatedNumber]:
    """Returns the numbers *text* states, in order, skipping exempt ones.

    Args:
        text: Any prose (an answer statement, a chunk of filing text).

    Returns:
        One ``StatedNumber`` per stated number. Years, fiscal labels (``FY2024``, ``Q3``),
        dates, form names and compound words (``10-K``, ``5-year``), ranks (``3rd``, ``#1``,
        ``top 3``), counts of companies and periods (``5 companies``, ``12 months``) and
        numbers in words are not returned.
    """
    iso_spans = [m.span() for m in _ISO_DATE.finditer(text)]
    found: list[StatedNumber] = []
    for m in _NUMBER.finditer(text):
        start, end = m.start(), m.end()
        if any(a <= start < b for a, b in iso_spans):
            continue
        number = _read_one(text, m.group("cur"), m.group("num"), start, end)
        if number is not None:
            found.append(number)
    return found


def _read_one(text: str, currency: str | None, digits: str, start: int,
              end: int) -> StatedNumber | None:
    """Reads one regex match as a stated number, or returns None when it is exempt."""
    before = text[:start]
    if before and (before[-1].isalpha() or before[-1].isdigit() or before[-1] == "."):
        return None  # a fiscal label or id (FY2024, s1), or the tail of another number
    rest = text[end:]
    plain = digits.replace(",", "")
    is_integer = "." not in plain
    if _COMPOUND.match(rest):
        return None  # 10-K, 20-F, 5-year
    if is_integer and _ORDINAL.match(rest):
        return None  # 1st, 3rd

    exponent = 0
    percent = False
    suffix_end = end
    if scale := _SCALE_WORD.match(rest):
        exponent = _SCALES[scale.group(1).lower()]
        suffix_end += scale.end()
    elif scale := _SCALE_LETTER.match(rest):
        exponent = _SCALES[scale.group(1).lower()]
        suffix_end += scale.end()
    elif pct := _PERCENT.match(rest):
        percent = True
        suffix_end += pct.end()
    elif mult := _MULTIPLE.match(rest):
        suffix_end += mult.end()
    elif rest[:1].isalpha():
        return None  # digits glued to a word: an id, not an amount

    bare = currency is None and exponent == 0 and not percent
    if bare and is_integer:
        if "," not in digits and len(plain) == 4 and 1900 <= int(plain) <= 2100:
            return None  # a year
        if _COUNT.match(rest):
            return None  # 5 companies, 12 months
        if _RANK_BEFORE.search(before):
            return None  # #1, No. 4, top 3
        if _MONTH_BEFORE.search(before):
            return None  # September 28, Sept. 28

    amount = Decimal(plain)
    exp = amount.as_tuple().exponent
    places = -exp if isinstance(exp, int) else 0  # always an int: the digits are finite
    value = float(amount.scaleb(exponent))
    step = float(Decimal(1).scaleb(exponent - places))
    return StatedNumber(text=text[start:suffix_end], value=value, step=step, percent=percent,
                        start=start)


def within_precision(stated: StatedNumber, candidate: float, rule_step: float = 0.0) -> bool:
    """Whether *stated* matches *candidate*, compared as an unsigned magnitude.

    They match when ``|s - |v|| <= max(stated.step, rule_step) / 2``, plus a relative float
    slack of ``FLOAT_SLACK``. A stated number coarser than the rule (``$391 billion`` for
    391,035,000,000) is a faithful rounding and matches.

    Args:
        stated: The stated number.
        candidate: The value it is compared with (percent candidates already in points).
        rule_step: The candidate's own precision (see ``reported_step``, ``ratio_step``).

    Returns:
        True when the two agree within half the coarser of the two steps.
    """
    v = abs(candidate)
    tolerance = max(stated.step, rule_step) / 2
    return abs(stated.value - v) <= tolerance + FLOAT_SLACK * max(stated.value, v)


_ISO_CURRENCY = re.compile(r"[A-Z]{3}")
_SHARES = "shares"


def display_unit(unit: str | None) -> str | None:
    """The unit an answer shows: a stored per-share product as ``<currency>/shares``.

    The fact store writes a per-share unit as its measures joined by ``*``, in either order;
    this is the one place that maps it to the ``USD/shares`` form answers and keys use. The
    currency is kept, so ``shares*TWD`` never becomes ``USD/shares``.

    Args:
        unit: The stored unit string, e.g. ``"shares*USD"``.

    Returns:
        ``"USD/shares"`` for ``"shares*USD"`` or ``"USD*shares"``; any other unit unchanged.
    """
    if unit is None:
        return None
    parts = unit.split("*")
    if len(parts) != 2 or parts.count(_SHARES) != 1:
        return unit
    currency = parts[1] if parts[0] == _SHARES else parts[0]
    if not _ISO_CURRENCY.fullmatch(currency):
        return unit
    return f"{currency}/{_SHARES}"


def is_per_share_money(unit: str | None) -> bool:
    """Whether an XBRL unit names money per share (``USD/shares``, ``shares*USD``).

    Args:
        unit: The unit as the fact store renders it.

    Returns:
        True when one part of the unit is shares and another a currency code.
    """
    if not unit:
        return False
    parts = [p.strip() for p in re.split(r"[/*]", unit) if p.strip()]
    has_shares = any("share" in p.lower() for p in parts)
    has_money = any(re.fullmatch(r"[A-Za-z]{3}", p) and p.lower() != "pure" for p in parts)
    return has_shares and has_money


def reported_step(unit: str | None, decimals: object = None) -> float:
    """The precision of a reported (looked-up) amount.

    Args:
        unit: The fact's unit.
        decimals: The fact's XBRL ``decimals``; anything but an integer (``INF``, None)
            means no stated precision.

    Returns:
        ``PER_SHARE_STEP`` for money per share, else ``10**-decimals`` for an integer
        ``decimals``, else 0.
    """
    if is_per_share_money(unit):
        return PER_SHARE_STEP
    if isinstance(decimals, int) and not isinstance(decimals, bool):
        return float(Decimal(1).scaleb(-decimals))
    return 0.0


def ratio_step(value: float) -> float:
    """The precision of a computed value: 3 significant figures.

    Args:
        value: The computed value.

    Returns:
        ``10 ** (floor(log10 |value|) - 2)``, or 0 for a zero or non-finite value.
    """
    v = abs(value)
    if v == 0 or not math.isfinite(v):
        return 0.0
    return 10.0 ** (math.floor(math.log10(v)) - 2)
