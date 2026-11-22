"""Ignore a period mention that names no year when the query's other periods resolved.

The IntentRouter LLM sometimes lifts a word such as "year-over-year" or "each year" as a
period mention. It resolves to no year (``could_not_resolve_year``), and one ambiguous period
degrades the whole query to the narrative branch even though "fiscal 2024" resolved. A mention
with no year evidence is dropped instead, but only when another period resolved; a mention
that names a year and fails still degrades (docs/specs/yearless-period-mention.md).

Pure: no I/O, no clock, no logging. The word lists are deliberately generous, so the rule can
only fail towards degrading (today's behaviour), never towards masking a real failure.
"""

from __future__ import annotations

import re

from src.retriever.xbrl.entity_extractor import ResolvedPeriod

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_RELATIVE_WORDS = frozenset({
    "last", "latest", "recent", "current", "this", "prior", "previous", "preceding", "ago",
    "past", "trailing", "next", "earlier", "ytd", "today", "now",
})
_SUB_ANNUAL_WORDS = frozenset({
    "quarter", "quarters", "quarterly", "half", "halves", "month", "months", "date",
})
_NUMBER_WORDS = frozenset({
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "first", "second", "third", "fourth",
})
_MONTH_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec",
})
_EVIDENCE_WORDS = _RELATIVE_WORDS | _SUB_ANNUAL_WORDS | _NUMBER_WORDS | _MONTH_WORDS


def has_year_evidence(mention: str) -> bool:
    """Return whether a period mention carries any evidence of which year it means.

    Args:
        mention: The raw period mention the router extracted.

    Returns:
        True when it holds a digit, or a whole-word relative, sub-annual, number or month word
        (docs/specs/yearless-period-mention.md, Requirement 1); else False.
    """
    if any(ch.isdigit() for ch in mention):
        return True
    return any(token in _EVIDENCE_WORDS for token in _TOKEN_RE.findall(mention.lower()))


def partition_yearless_periods(
    periods: list[ResolvedPeriod],
) -> tuple[list[ResolvedPeriod], list[ResolvedPeriod]]:
    """Split resolved periods into those kept and the yearless failures to ignore.

    Args:
        periods: The periods ``EntityExtractor.resolve_periods`` returned, in order.

    Returns:
        ``(kept, ignored)``: ignored are the ambiguous periods whose mention has no year
        evidence, and only when some period resolved; both lists keep the input order.
    """
    if not any(not p.ambiguous for p in periods):
        return list(periods), []
    kept: list[ResolvedPeriod] = []
    ignored: list[ResolvedPeriod] = []
    for period in periods:
        if period.ambiguous and not has_year_evidence(period.mention):
            ignored.append(period)
        else:
            kept.append(period)
    return kept, ignored
