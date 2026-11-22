# What a cross-filer screen derived in code, as evidence of the statements that cite it.
#
# Spec: docs/specs/verifier-derived-disclosures.md. A statement citing a screen (its step id or
# one of its rows) is checked against the screen's rows and against what the screen derived:
# the ranked filers and their count, the universe and its members, each exclusion with its
# reason, and the period ends. ``screen_summary`` gathers those facts from a screen step;
# ``screen_count_values`` lists the counts a statement may state. Pure: no I/O.


from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


def screen_summary(step: Mapping[str, Any]) -> dict[str, Any] | None:
    """The derived facts of one screen step (Requirement 1).

    Args:
        step: One step as ``StepResult.to_dict()`` holds it.

    Returns:
        ``{"step_id", "universe", "fiscal_year", "members", "ranked_count", "ranked",
        "rows_returned", "exclusions", "period_ends", "period_ends_differ"}`` for a screen;
        None for any other step. The step is never mutated and the result shares no list or
        dict with it.
    """
    if step.get("op") != "screen":
        return None
    rows = list(step.get("rows") or [])
    rows_returned = len(rows)
    disclosure = step.get("disclosure")
    if isinstance(disclosure, Mapping):
        ranked = [copy.deepcopy(t) for t in disclosure.get("ranked") or []]
        return {
            "step_id": step.get("step_id"),
            "universe": copy.deepcopy(disclosure.get("universe")),
            "fiscal_year": copy.deepcopy(disclosure.get("fiscal_year")),
            "members": copy.deepcopy(disclosure.get("members")),
            "ranked_count": len(ranked),
            "ranked": ranked,
            "rows_returned": rows_returned,
            "exclusions": _copies(disclosure.get("exclusions")),
            "period_ends": copy.deepcopy(dict(disclosure.get("period_ends") or {})),
            "period_ends_differ": bool(disclosure.get("period_ends_differ")),
        }
    # An older or an error screen: what its rows, row cap and exclusions show.
    ranked = [str(r.get("ticker")) for r in rows if r.get("ticker") is not None]
    row_cap = step.get("row_cap")
    before_cap = row_cap.get("rows_before_cap") if isinstance(row_cap, Mapping) else None
    ranked_count = (before_cap if isinstance(before_cap, int) and not isinstance(before_cap, bool)
                    else rows_returned)
    period_ends = {str(r.get("ticker")): copy.deepcopy(r.get("period_end")) for r in rows
                   if r.get("ticker") is not None and r.get("period_end") is not None}
    return {
        "step_id": step.get("step_id"),
        "universe": None,
        "fiscal_year": None,
        "members": None,
        "ranked_count": ranked_count,
        "ranked": ranked,
        "rows_returned": rows_returned,
        "exclusions": _copies(step.get("excluded")),
        "period_ends": period_ends,
        "period_ends_differ": None,
    }


def screen_count_values(step: Mapping[str, Any]) -> list[int]:
    """The counts a statement citing a screen may state (Requirement 2).

    Args:
        step: One step as ``StepResult.to_dict()`` holds it.

    Returns:
        The sorted distinct ints among the summary's ``ranked_count``, ``members``,
        ``rows_returned`` and number of exclusions; ``[]`` for a step that is not a screen.
    """
    summary = screen_summary(step)
    if summary is None:
        return []
    counts = {summary["ranked_count"], summary["rows_returned"], len(summary["exclusions"])}
    members = summary["members"]
    if isinstance(members, int) and not isinstance(members, bool):
        counts.add(members)
    return sorted(c for c in counts if isinstance(c, int) and not isinstance(c, bool))


def _copies(items: Any) -> list[dict[str, Any]]:
    """Dict copies of a list of mappings, in their order (``[]`` for None)."""
    return [copy.deepcopy(dict(item)) for item in items or []]
