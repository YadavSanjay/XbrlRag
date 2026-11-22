"""A value step's fiscal year and period, as faithfulness-verifier evidence.

docs/specs/verifier-period-evidence.md. What the code knows about a cited step's period is
shown to the verifier, so a true fiscal-year or period claim can be judged SUPPORTED:

- a lookup's period comes from its plan step (``fy``, ``fiscal_period``, ``ytd``) and the
  start and end of the fact it cites (``lookup_period``);
- a compute's from the steps its expression references (``compute_period``);
- an aggregate's from the screen it aggregates (``aggregate_period``).

All three are derived in code, never by an LLM. Pure functions; no I/O. Every result is a new
dict that aliases none of its inputs.
"""

from __future__ import annotations

import copy
import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

from src.retriever.xbrl.screen_disclosure import stated_period_end


def _is_year(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _iso(value: dt.date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return dt.date.fromisoformat(str(value).strip()).isoformat()


def lookup_period(
    plan_step: Mapping[str, Any], period_start: dt.date | str | None,
    period_end: dt.date | str | None,
) -> dict[str, Any] | None:
    """A lookup's fiscal year and period (Requirement 1).

    Args:
        plan_step: The lookup's plan step (``fy``, ``fiscal_period``, ``ytd``).
        period_start: The cited fact's stored start, or None for an instant.
        period_end: The cited fact's stored (next-midnight) end, or None.

    Returns:
        ``{"fiscal_year", "fiscal_period", "ytd", "period_start", "period_end"}`` with the
        stated end, or None when the fiscal year, start and end would all be None.
    """
    fy = plan_step.get("fy")
    fiscal_year = fy if _is_year(fy) else None
    fp = plan_step.get("fiscal_period")
    start = _iso(period_start)
    end = stated_period_end(period_end)
    if fiscal_year is None and start is None and end is None:
        return None
    return {
        "fiscal_year": fiscal_year,
        "fiscal_period": fp if isinstance(fp, str) and fp else "FY",
        "ytd": plan_step.get("ytd") is True,
        "period_start": start,
        "period_end": end,
    }


def _flatten(entry: Mapping[str, Any], out: list[dict[str, Any]], seen: set[Any]) -> None:
    """Appends *entry* (or, for a compute's period, its inputs, recursively) to *out*."""
    nested = entry.get("inputs")
    if isinstance(nested, list):
        for item in nested:
            if isinstance(item, Mapping):
                _flatten(item, out, seen)
        return
    step_id = entry.get("step_id")
    if step_id in seen:
        return
    seen.add(step_id)
    out.append(copy.deepcopy(dict(entry)))


def compute_period(
    inputs: Sequence[tuple[str, Mapping[str, Any] | None]],
) -> dict[str, Any] | None:
    """A compute's input periods (Requirement 2).

    Args:
        inputs: ``(step_id, period)`` per referenced step, in first-reference order.

    Returns:
        ``{"inputs", "fiscal_years"}``, or None when no input has a period.
    """
    entries: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for step_id, period in inputs:
        if period is None:
            continue
        _flatten({"step_id": step_id, **period}, entries, seen)
    if not entries:
        return None
    years = sorted({e["fiscal_year"] for e in entries if _is_year(e.get("fiscal_year"))})
    return {"inputs": entries, "fiscal_years": years}


def aggregate_period(
    screen_step: Mapping[str, Any] | None, aggregate: Mapping[str, Any],
) -> dict[str, Any] | None:
    """An aggregate's fiscal year and period ends, from the screen it aggregates (Requirement 3).

    Args:
        screen_step: The aggregated screen's ``StepResult.to_dict()``.
        aggregate: The aggregate step's ``aggregate`` dict.

    Returns:
        ``{"fiscal_year", "period_ends", "period_ends_differ"}``, or None when
        ``screen_step`` is not a screen.
    """
    if not isinstance(screen_step, Mapping) or screen_step.get("op") != "screen":
        return None
    fiscal_year: int | None = None
    disclosure = screen_step.get("disclosure")
    if isinstance(disclosure, Mapping) and _is_year(disclosure.get("fiscal_year")):
        fiscal_year = disclosure["fiscal_year"]
    else:
        rows = screen_step.get("rows") or []
        years = {r.get("fiscal_year") if isinstance(r, Mapping) else None for r in rows}
        if len(years) == 1:
            (only,) = years
            if _is_year(only):
                fiscal_year = only
    return {
        "fiscal_year": fiscal_year,
        "period_ends": dict(aggregate.get("period_ends") or {}),
        "period_ends_differ": bool(aggregate.get("period_ends_differ")),
    }
