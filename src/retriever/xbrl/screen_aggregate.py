"""A total, median, mean or count over a screen's rows, computed in code.

Spec: docs/specs/screen-aggregates.md (Requirements 7 and 9). The ``aggregate`` plan op
takes a screen step's ranked rows and returns one number with the rows it covers, their
fact ids and the screen's universe and exclusions. The synthesizer never adds figures
itself; it states this value. ``aggregate_disclosure_text`` is a fixed sentence naming the
aggregated members, appended beside the screen's own disclosure sentence (S2, S3) after the
faithfulness verifier, so no member can be dropped or misquoted by the LLM.

The aggregate covers exactly the screen's rows, after ``top_n`` and ``min_threshold``.
Members the screen could not rank are never in it; they stay named in ``exclusions``.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from typing import Any

from src.numerics.stated_numbers import is_per_share_money

AGGREGATE_FNS = ("sum", "median", "mean", "count")
# Units whose sum means nothing (a sum of ratios); per-share money is checked separately.
_UNSUMMABLE_UNITS = {"pure", "rate"}
# S2: period ends are disclosed when the aggregated rows' ends are more than three months
# apart (the same band as the screen's own disclosure).
_DISCLOSE_DAYS = 92
_LABELS = {"sum": "Total", "mean": "Average", "median": "Median", "count": "Count"}


def _iso(end: Any) -> str | None:
    """A row's stated period end as an ISO string, or None."""
    if end is None:
        return None
    if isinstance(end, dt.datetime):
        return end.date().isoformat()
    if isinstance(end, dt.date):
        return end.isoformat()
    text = str(end).strip()
    return text or None


def _finite(value: Any) -> bool:
    """Whether a row value is a finite number (bools are not numbers here)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _period_ends(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], bool]:
    """The rows' stated ends by ticker, and whether they span more than three months."""
    ends: dict[str, str] = {}
    for r in rows:
        end = _iso(r.get("period_end"))
        if end is not None:
            ends[str(r.get("ticker"))] = end
    try:
        dates = [dt.date.fromisoformat(e) for e in ends.values()]
    except ValueError:
        dates = []
    differ = len(dates) >= 2 and (max(dates) - min(dates)).days > _DISCLOSE_DAYS
    return ends, differ


def aggregate_screen_rows(fn: str, screen: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate a screen step's rows.

    Args:
        fn: ``"sum"``, ``"median"``, ``"mean"`` or ``"count"``.
        screen: The screen step's ``StepResult.to_dict()``.

    Returns:
        ``{"fn", "status", "value", "unit", "n", "members", "fact_ids", "middle",
        "universe", "universe_members", "exclusions", "period_ends", "period_ends_differ",
        "error"}`` as in the spec's Requirement 7. ``value`` and ``unit`` are None unless
        the status is OK; ``error`` names the reason for an ERROR.
    """
    rows = [r for r in (screen.get("rows") or []) if isinstance(r, Mapping)]
    disclosure = screen.get("disclosure")
    if not isinstance(disclosure, Mapping):
        disclosure = None
    period_ends, differ = _period_ends(rows)

    out: dict[str, Any] = {
        "fn": fn,
        "status": "OK",
        "value": None,
        "unit": None,
        "n": len(rows),
        "members": [str(r.get("ticker")) for r in rows],
        "fact_ids": [r.get("fact_id") for r in rows],
        "middle": [],
        "universe": disclosure.get("universe") if disclosure else None,
        "universe_members": disclosure.get("members") if disclosure else None,
        "exclusions": list(disclosure.get("exclusions") or []) if disclosure else [],
        "period_ends": period_ends,
        "period_ends_differ": differ,
        "error": None,
    }

    def fail(status: str, error: str | None) -> dict[str, Any]:
        out.update(status=status, value=None, unit=None, middle=[], error=error)
        return out

    op = screen.get("op")
    if op != "screen":
        return fail("ERROR", f"aggregate needs a screen step, got op {op!r}")
    if fn not in AGGREGATE_FNS:
        return fail("ERROR", f"unknown aggregate fn {fn!r}; use one of {list(AGGREGATE_FNS)}")
    status = screen.get("status")
    if status == "NOT_FOUND":
        return fail("NOT_FOUND", None)
    if status != "OK":
        return fail(
            "ERROR",
            f"screen step {screen.get('step_id')!r} did not resolve OK (status {status!r})",
        )
    if screen.get("row_cap"):
        return fail(
            "ERROR",
            "the screen's rows were trimmed by a row_cap, so an aggregate would be partial",
        )
    if not rows:
        return fail("NOT_FOUND", None)
    bad = [str(r.get("ticker")) for r in rows if not _finite(r.get("value"))]
    if bad:
        return fail("ERROR", f"row(s) without a finite value: {', '.join(bad)}")

    unit: str | None = None
    if fn != "count":
        units = {r.get("unit") for r in rows}
        if len(units) != 1 or None in units:
            named = sorted(str(u) for u in units)
            return fail("ERROR", f"rows do not share one unit: {', '.join(named)}")
        unit = str(units.pop())
        if fn == "sum" and (unit.lower() in _UNSUMMABLE_UNITS or is_per_share_money(unit)):
            return fail("ERROR", f"a sum of unit {unit!r} means nothing; use median or mean")

    values = [float(r["value"]) for r in rows]
    n = len(values)
    value: float | int
    if fn == "sum":
        value = math.fsum(values)
    elif fn == "mean":
        value = math.fsum(values) / n
    elif fn == "count":
        value = n
    else:
        ordered = sorted(rows, key=lambda r: (float(r["value"]), str(r.get("ticker"))))
        m = n // 2
        if n % 2:
            middle_rows = [ordered[m]]
            value = float(ordered[m]["value"])
        else:
            middle_rows = [ordered[m - 1], ordered[m]]
            value = (float(ordered[m - 1]["value"]) + float(ordered[m]["value"])) / 2
        out["middle"] = [str(r.get("ticker")) for r in middle_rows]

    out["value"] = value
    out["unit"] = unit
    return out


def aggregate_disclosure_text(aggregate: Mapping[str, Any]) -> str:
    """The sentence naming what an aggregate covers.

    Args:
        aggregate: As ``aggregate_screen_rows`` returns it.

    Returns:
        ``"<Label> of <n> companies: <T1>, ..., <Tn>."`` for an OK aggregate (a median adds
        its middle row(s)); ``""`` otherwise.
    """
    if aggregate.get("status") != "OK":
        return ""
    fn = str(aggregate.get("fn"))
    label = _LABELS.get(fn)
    if label is None:
        return ""
    members = [str(m) for m in aggregate.get("members") or []]
    n = aggregate.get("n")
    if not isinstance(n, int) or isinstance(n, bool):
        n = len(members)
    companies = "company" if n == 1 else "companies"
    text = f"{label} of {n} {companies}: {', '.join(members)}"
    if fn == "median":
        middle = [str(m) for m in aggregate.get("middle") or []]
        if len(middle) == 1:
            text += f" (middle: {middle[0]})"
        elif len(middle) == 2:
            text += f" (middle: {middle[0]} and {middle[1]}, averaged)"
    return text + "."


def append_aggregate_disclosures(summary: str, steps: Sequence[Mapping[str, Any]]) -> str:
    """Append each aggregate step's sentence to an answer summary.

    Args:
        summary: The answer summary.
        steps: The plan's step results as ``StepResult.to_dict()`` dicts.

    Returns:
        The summary with each new sentence appended in step order, one space apart; the
        summary unchanged when there is none to add.
    """
    sentences: list[str] = []
    for step in steps:
        aggregate = step.get("aggregate")
        if step.get("op") != "aggregate" or not isinstance(aggregate, Mapping):
            continue
        text = aggregate_disclosure_text(aggregate)
        if text and text not in summary and text not in sentences:
            sentences.append(text)
    if not sentences:
        return summary
    base = summary.rstrip()
    return (f"{base} " if base else "") + " ".join(sentences)
