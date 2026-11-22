"""A ranking among named companies ranks only those companies.

Spec: docs/specs/named-company-screen.md. A question that names two or more companies and
asks which of them leads on a metric must be answered by a screen over exactly those
companies, never by a corpus-wide screen whose leader may not be among them. A screen
step states such a set as a **member list**: ``universe`` is a list of tickers
(``member_list`` is the one reading of it, shared by ``PlanValidator``, the plan check
below and ``XbrlFactstoreScreen.screen``). ``named_company_plan_errors`` is run by the
plan generator beside ``PlanValidator.validate`` and ``group_plan_errors``; its errors feed
the same validate-retry loop, ending in ``plan_validation_failed`` (which escalates one
model tier) when every attempt screens another set.

A screen over a member list discloses its universe as ``NAMED`` (``"named"``), so the
disclosure sentence reads "the named group"; a groups.yaml group may never use that id.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

# The disclosure's universe for a member-list screen (Requirement 6).
NAMED = "named"


def member_list(universe: Any) -> list[str] | None:
    """The tickers a member-list universe names.

    Args:
        universe: A screen step's ``universe`` value.

    Returns:
        The distinct ``strip().upper()`` tickers, sorted, for a list of non-blank strings
        holding at least two of them; None for anything else.
    """
    if not isinstance(universe, list):
        return None
    tickers: set[str] = set()
    for item in universe:
        if not isinstance(item, str) or not item.strip():
            return None
        tickers.add(item.strip().upper())
    if len(tickers) < 2:
        return None
    return sorted(tickers)


def named_companies(companies: Sequence[Any]) -> list[str]:
    """The tickers of the companies the intent resolved.

    Args:
        companies: ``IntentRouterResult.companies`` (``ResolvedCompany`` objects).

    Returns:
        The upper-cased tickers of the resolved companies (truthy ``cik`` and ``ticker``, not
        ``is_ambiguous``), without repeats, in order of first occurrence.
    """
    out: list[str] = []
    for c in companies or ():
        cik = getattr(c, "cik", None)
        ticker = getattr(c, "ticker", None)
        if not cik or not ticker or getattr(c, "is_ambiguous", False):
            continue
        upper = str(ticker).upper()
        if upper not in out:
            out.append(upper)
    return out


def named_company_plan_errors(
    plan: Any, companies: Sequence[str], universe: str | None
) -> list[str]:
    """Plan errors when a screen ranks other than the companies the question names.

    Args:
        plan: The planner's ``{"steps": [...]}``.
        companies: The named tickers (``named_companies``).
        universe: The universe the router resolved (``IntentRouterResult.universe``).

    Returns:
        One error per screen step whose member list is not exactly ``companies``, in step
        order; ``[]`` when ``universe`` is set, fewer than two companies are named, or the
        plan has no step list.
    """
    if universe is not None:
        return []  # a named group or "covered companies": group_plan_errors owns it
    named = sorted({str(c).strip().upper() for c in companies if str(c).strip()})
    if len(named) < 2:
        return []
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return []
    errors: list[str] = []
    for index, step in enumerate(plan["steps"]):
        if not isinstance(step, dict) or step.get("op") != "screen":
            continue
        planned = step.get("universe")
        if member_list(planned) == named:
            continue
        errors.append(
            f"step[{index}] (id={step.get('id')!r}) screens universe {planned!r}, but the "
            f"question ranks only the companies it names; set \"universe\": "
            f"{json.dumps(named)} on every screen step"
        )
    return errors
