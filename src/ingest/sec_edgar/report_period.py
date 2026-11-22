# A filing's statement period, read from its XBRL instance. Pure: no database, no filesystem.
# Spec: docs/specs/tenq-fiscal-labels.md, requirement 6.

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from src.ingest.sec_edgar.fiscal_labels import ANNUAL_FORMS, QUARTERLY_FORMS

_XBRLI = "{http://www.xbrl.org/2003/instance}"
# Inclusive day lengths (both ends counted) of a statement period, by kind of form.
_ANNUAL_DAYS = (350, 380)
_QUARTER_DAYS = (80, 100)


@dataclass(frozen=True)
class _Context:
    start: date | None
    end: date | None
    dimensional: bool


def _date(text: str | None) -> date | None:
    """An instance date (``YYYY-MM-DD``, optionally followed by a time), else ``None``."""
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _contexts(root: ET.Element) -> dict[str, _Context]:
    """Every context of the instance by id: its period and whether it carries dimensions."""
    out: dict[str, _Context] = {}
    for ctx in root.iter(f"{_XBRLI}context"):
        period = ctx.find(f"{_XBRLI}period")
        if period is None:
            continue
        instant = _date(period.findtext(f"{_XBRLI}instant"))
        start = _date(period.findtext(f"{_XBRLI}startDate"))
        end = _date(period.findtext(f"{_XBRLI}endDate")) or instant
        dimensional = (ctx.find(f"{_XBRLI}entity/{_XBRLI}segment") is not None
                       or ctx.find(f"{_XBRLI}scenario") is not None)
        out[ctx.get("id", "")] = _Context(start, end, dimensional)
    return out


def _document_type_context(root: ET.Element) -> str | None:
    """The ``contextRef`` of the instance's ``dei:DocumentType`` fact, if any."""
    for elem in root.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.endswith("}DocumentType") and "/dei/" in tag:
            return elem.get("contextRef")
    return None


def report_period_bounds(instance: bytes, form_type: str) -> tuple[date, date] | None:
    """The first and last day of the period a filing's statements cover.

    Args:
        instance: The filing's XBRL instance (``*_htm.xml``).
        form_type: The filing's form, e.g. ``10-K`` or ``10-Q``.

    Returns:
        ``(start, end)``, inclusive dates as the instance writes them: ``end`` from the context
        of ``dei:DocumentType``, ``start`` from the context without dimensions that ends then
        and lasts a year (annual forms) or a quarter (10-Q); ``None`` when there is none.
    """
    form = form_type.upper().strip()
    if form in ANNUAL_FORMS:
        low, high = _ANNUAL_DAYS
    elif form in QUARTERLY_FORMS:
        low, high = _QUARTER_DAYS
    else:
        return None
    try:
        root = ET.fromstring(instance)
    except ET.ParseError:
        return None
    contexts = _contexts(root)
    dei_ref = _document_type_context(root)
    dei_ctx = contexts.get(dei_ref) if dei_ref is not None else None
    if dei_ctx is None or dei_ctx.end is None:
        return None
    end = dei_ctx.end
    starts = {c.start for c in contexts.values()
              if not c.dimensional and c.start is not None and c.end == end
              and low <= (end - c.start).days + 1 <= high}
    if not starts:
        return None
    # The DEI context itself when it is statement-length; otherwise the shortest such period.
    if dei_ctx.start in starts and not dei_ctx.dimensional:
        return dei_ctx.start, end
    return max(starts), end
