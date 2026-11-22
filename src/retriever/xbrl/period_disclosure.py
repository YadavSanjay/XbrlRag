"""The period a number covers, and its length, as answers state them.

Spec: docs/specs/period-disclosure.md. Pure functions over stored ``mv_financial_facts``
rows, whose ``period_end`` is one day after the period's last day (Arelle's next-midnight
date), so ``period_end - period_start`` is the period's length in days. The SQL views mirror
``duration_class`` and ``average_based_ratio``; answers use ``year_length_disclosure`` and
``derive_q4`` directly.

Every date these functions emit is a stated date: an emitted end is the stored end - 1 day,
an emitted start is the stored start (R6).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

# Inclusive day-count bands (R1). Inside a band the class equals FSDS ``qtrs``
# (round(days * 4 / 365.25)); every other length is "other".
DURATION_BANDS: tuple[tuple[str, int, int], ...] = (
    ("quarter", 84, 119),
    ("half", 168, 189),
    ("nine_month", 252, 280),
    ("annual", 350, 380),
)

# Two compared years whose day counts are this many days apart or more are disclosed (R3).
YEAR_LENGTH_DIFF_DAYS = 6
# A 53-week fiscal year: 371 days, give or take a day of calendar drift.
_FIFTY_THREE_WEEK_DAYS = (370, 371, 372)

_ONE_DAY = dt.timedelta(days=1)
# A taxonomy release year at the end of a namespace ("http://fasb.org/us-gaap/2025").
_NAMESPACE_YEAR = re.compile(r"/\d{4}(?=:)")


def _as_date(value: dt.date | str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _days(start: dt.date | str, end: dt.date | str) -> int:
    return (_as_date(end) - _as_date(start)).days


def _class_of_days(days: int) -> str:
    for kind, lo, hi in DURATION_BANDS:
        if lo <= days <= hi:
            return kind
    return "other"


def _stated_period(row: Mapping[str, Any]) -> tuple[str, str]:
    """``(start, last day)`` of a stored duration row, as ISO strings."""
    start = _as_date(row["period_start"])
    last = _as_date(row["period_end"]) - _ONE_DAY
    return start.isoformat(), last.isoformat()


def duration_class(period_start: dt.date | str | None, period_end: dt.date | str) -> str:
    """The duration class of a stored period, by its length in days.

    Args:
        period_start: The stored start, or None for an instant.
        period_end: The stored end (one day after the period's last day).

    Returns:
        ``"quarter"`` (84-119 days), ``"half"`` (168-189), ``"nine_month"`` (252-280),
        ``"annual"`` (350-380), ``"other"`` for any other length, or ``"instant"``.
    """
    if period_start is None:
        return "instant"
    return _class_of_days(_days(period_start, period_end))


def year_length_disclosure(current: Mapping[str, Any], prior: Mapping[str, Any]) -> dict[str, Any]:
    """The lengths of two compared fiscal periods, and the note when they differ.

    Args:
        current: The current period's stored row (``period_start``, ``period_end``).
        prior: The prior period's stored row.

    Returns:
        ``{"days_current", "days_prior", "current_period", "prior_period", "differs",
        "note"}`` as in the spec's R3.
    """
    days_current = _days(current["period_start"], current["period_end"])
    days_prior = _days(prior["period_start"], prior["period_end"])
    current_period = _stated_period(current)
    prior_period = _stated_period(prior)
    differs = abs(days_current - days_prior) >= YEAR_LENGTH_DIFF_DAYS
    note = None
    if differs:
        long_year = (
            " (one is a 53-week year)"
            if days_current in _FIFTY_THREE_WEEK_DAYS or days_prior in _FIFTY_THREE_WEEK_DAYS
            else ""
        )
        note = (
            f"The compared fiscal periods differ in length{long_year}: "
            f"{current_period[0]} to {current_period[1]} has {days_current} days and "
            f"{prior_period[0]} to {prior_period[1]} has {days_prior} days; the change is "
            "on the reported basis, not adjusted for the different number of days."
        )
    return {
        "days_current": days_current,
        "days_prior": days_prior,
        "current_period": current_period,
        "prior_period": prior_period,
        "differs": differs,
        "note": note,
    }


def opening_balance(
    instants: Iterable[Mapping[str, Any]], period_start: dt.date | str
) -> dict[str, Any] | None:
    """The opening balance of a period: the valued instant stored at the period's start.

    Args:
        instants: One filing's instant rows of one concept and unit (``fact_id``,
            ``value``, ``period_end``).
        period_start: The stored start of the duration the balance opens.

    Returns:
        The matching row (lowest ``fact_id``), or None.
    """
    start = _as_date(period_start)
    matches = [
        r for r in instants
        if r.get("value") is not None
        and r.get("period_end") is not None
        and _as_date(r["period_end"]) == start
    ]
    if not matches:
        return None
    return dict(min(matches, key=lambda r: int(r["fact_id"])))


def average_based_ratio(numerator: float, closing: float, opening: float | None) -> dict[str, Any]:
    """A ratio over the average balance, or over the closing balance when no opening exists.

    Args:
        numerator: The flow value (revenue, net income, cost of revenue).
        closing: The closing balance.
        opening: The opening balance, or None when it was not filed.

    Returns:
        ``{"value", "basis", "denominator", "note"}`` as in the spec's R4.
    """
    if opening is None:
        denominator = closing
        basis = "closing"
        note: str | None = (
            "The opening balance was not filed, so the closing balance was used in place "
            "of the average of the opening and closing balances."
        )
    else:
        denominator = (opening + closing) / 2
        basis = "average"
        note = None
    value = None if denominator == 0 else numerator / denominator
    return {"value": value, "basis": basis, "denominator": denominator, "note": note}


def _same_concept(a: Any, b: Any) -> bool:
    """One element: equal QNames, ignoring a taxonomy release year in the namespace."""
    if a is None or b is None:
        return False
    return _NAMESPACE_YEAR.sub("", str(a)) == _NAMESPACE_YEAR.sub("", str(b))


def _decimals_key(d: Any) -> float:
    """``decimals`` as a number for comparison; ``INF`` (exact) is +infinity."""
    if isinstance(d, str) and d.strip().upper() == "INF":
        return math.inf
    return float(d)


def _coarser_decimals(a: Any, b: Any) -> Any:
    known = [d for d in (a, b) if d is not None]
    if not known:
        return None
    return min(known, key=_decimals_key)


def _difference(a: Any, b: Any) -> Any:
    if isinstance(a, float) or isinstance(b, float):
        return float(a) - float(b)
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return Decimal(a) - Decimal(b)
    return a - b


def _refusal(reason: str) -> dict[str, Any]:
    return {
        "status": "refused", "value": None, "unit": None, "period_start": None,
        "period_end": None, "days": None, "decimals": None, "fact_ids": None,
        "accessions": None, "note": None, "reason": reason,
    }


def derive_q4(annual: Mapping[str, Any], nine_month: Mapping[str, Any]) -> dict[str, Any]:
    """Q4 as the fiscal year minus its nine-month year-to-date, or a refusal.

    Args:
        annual: The fiscal year's stored row.
        nine_month: The same year's nine-month year-to-date stored row.

    Returns:
        The derived or refused result as in the spec's R5.
    """
    if annual.get("value") is None or nine_month.get("value") is None:
        return _refusal("a value is nil (not reported), so Q4 cannot be derived")
    if not _same_concept(annual.get("concept"), nine_month.get("concept")):
        return _refusal(
            f"different concepts ({annual.get('concept')} and {nine_month.get('concept')})"
        )
    if annual.get("unit") != nine_month.get("unit"):
        return _refusal(f"different units ({annual.get('unit')} and {nine_month.get('unit')})")
    if annual.get("period_start") is None or nine_month.get("period_start") is None:
        return _refusal("an instant has no Q4")
    if _as_date(annual["period_start"]) != _as_date(nine_month["period_start"]):
        return _refusal(
            "the year and the nine-month year-to-date start on different days "
            f"({_as_date(annual['period_start'])} and {_as_date(nine_month['period_start'])}),"
            " so they are not one fiscal year on one basis"
        )
    annual_class = duration_class(annual["period_start"], annual["period_end"])
    if annual_class != "annual":
        return _refusal(f"the year's row is not a year ({annual_class})")
    nine_class = duration_class(nine_month["period_start"], nine_month["period_end"])
    if nine_class != "nine_month":
        return _refusal(f"the year-to-date row is not nine months ({nine_class})")
    remainder = _days(nine_month["period_end"], annual["period_end"])
    if _class_of_days(remainder) != "quarter":
        return _refusal(f"the remainder of {remainder} days is not a quarter")
    q4_start = _as_date(nine_month["period_end"]).isoformat()
    q4_end = (_as_date(annual["period_end"]) - _ONE_DAY).isoformat()
    return {
        "status": "derived",
        "value": _difference(annual["value"], nine_month["value"]),
        "unit": annual.get("unit"),
        "period_start": q4_start,
        "period_end": q4_end,
        "days": remainder,
        "decimals": _coarser_decimals(annual.get("decimals"), nine_month.get("decimals")),
        "fact_ids": (annual.get("fact_id"), nine_month.get("fact_id")),
        "accessions": (annual.get("accession_no"), nine_month.get("accession_no")),
        "note": (
            f"Q4 ({q4_start} to {q4_end}, {remainder} days) is derived: the fiscal year "
            "minus the nine-month year-to-date; no Q4 figure is filed."
        ),
        "reason": None,
    }
