# Key-free checks (v2 plan section 3.5): metamorphic consistency and perturbation checks
# over answers and plans that already exist. Pure functions: no pipeline, LLM or database.
# Spec: docs/specs/keyfree-checks.md.
from __future__ import annotations

from typing import Any


def _verdict(check: str, verdict: str, reason: str, **extra: Any) -> dict:
    """Builds a verdict dict with the common keys first, then the check's own keys."""
    out: dict[str, Any] = {"check": check, "verdict": verdict, "reason": reason}
    out.update(extra)
    return out


def _unit_policy(amounts: list[dict]) -> tuple[str, str] | None:
    """Applies the unit policy (requirement 3) to the compared amounts.

    Args:
        amounts: The compared amounts, each `{"value", "unit"}`.

    Returns:
        `(verdict, reason)` when the policy decides the check, else None (all present,
        one unit).
    """
    if any(a.get("value") is None for a in amounts):
        return "not_applicable", "missing_value"
    if any(a.get("unit") is None for a in amounts):
        return "not_applicable", "missing_unit"
    if len({a["unit"] for a in amounts}) > 1:
        return "inconsistent", "unit_mismatch"
    return None


def check_breakdown_sum(rows: list[dict], total: dict, rel_tol: float = 1e-3) -> dict:
    """Check that a breakdown's rows sum to the same filer-period's lookup total.

    Args:
        rows: The breakdown's amounts, each `{"value", "unit"}`.
        total: The lookup total, `{"value", "unit"}`.
        rel_tol: Tolerance relative to the total's magnitude.

    Returns:
        A verdict dict (`check`, `verdict`, `reason`, `observed`, `expected`, `tolerance`).
    """
    name = "breakdown_sum"
    if len(rows) < 2:
        return _verdict(name, "not_applicable", "too_few_rows",
                        observed=None, expected=None, tolerance=None)
    decided = _unit_policy([*rows, total])
    if decided is not None:
        return _verdict(name, *decided, observed=None, expected=None, tolerance=None)
    observed = float(sum(float(r["value"]) for r in rows))
    expected = float(total["value"])
    tolerance = rel_tol * abs(expected)
    ok = abs(observed - expected) <= tolerance
    return _verdict(name, "consistent" if ok else "inconsistent", "match" if ok else "mismatch",
                    observed=observed, expected=expected, tolerance=tolerance)


def check_growth(growth: dict, current: dict, prior: dict, abs_tol: float = 5e-4) -> dict:
    """Check that a YoY growth compute equals (current - prior) / |prior| of two lookups.

    Args:
        growth: The compute step's result as a fraction, `{"value", "unit"}` (unit ignored).
        current: The current-period lookup amount.
        prior: The prior-period lookup amount.
        abs_tol: Absolute tolerance on the fraction.

    Returns:
        A verdict dict (`check`, `verdict`, `reason`, `observed`, `expected`, `tolerance`).
    """
    name = "growth"
    decided = _unit_policy([current, prior])
    if decided is not None:
        return _verdict(name, *decided, observed=None, expected=None, tolerance=None)
    stated = growth.get("value")
    prior_value = float(prior["value"])
    if prior_value <= 0:
        # S4: growth from a non-positive base is not meaningful; declining is correct.
        if stated is None:
            return _verdict(name, "consistent", "not_meaningful",
                            observed=None, expected=None, tolerance=abs_tol)
        return _verdict(name, "inconsistent", "not_meaningful",
                        observed=float(stated), expected=None, tolerance=abs_tol)
    if stated is None:
        return _verdict(name, "not_applicable", "missing_value",
                        observed=None, expected=None, tolerance=None)
    expected = (float(current["value"]) - prior_value) / abs(prior_value)
    observed = float(stated)
    ok = abs(observed - expected) <= abs_tol
    return _verdict(name, "consistent" if ok else "inconsistent", "match" if ok else "mismatch",
                    observed=observed, expected=expected, tolerance=abs_tol)


def _ties(a: float, b: float, rel_tol: float) -> bool:
    """Whether two lookup values tie under a relative tolerance."""
    return abs(a - b) <= rel_tol * max(abs(a), abs(b))


def check_screen_order(
    screen_rows: list[dict],
    lookups: dict[str, dict],
    order: str = "desc",
    rel_tol: float = 1e-9,
) -> dict:
    """Check that a screen's ranking agrees with the individual lookups' order.

    Args:
        screen_rows: The screen's ranked rows, each with `ticker` and `unit`.
        lookups: Ticker to the individual lookup amount for the same metric and period.
        order: `"desc"` or `"asc"`.
        rel_tol: Relative tolerance under which two lookup values tie.

    Returns:
        A verdict dict (`check`, `verdict`, `reason`, `order`, `compared`, `values`,
        `skipped`, `inversions`, `tolerance`).

    Raises:
        ValueError: `order` is not `"asc"` or `"desc"`.
    """
    if order not in ("asc", "desc"):
        raise ValueError(f"order must be 'asc' or 'desc', got {order!r}")
    compared: list[str] = []
    values: list[float] = []
    skipped: list[Any] = []
    row_mismatch = False
    lookup_units: set[str] = set()
    for row in screen_rows:
        ticker = row.get("ticker")
        look = lookups.get(ticker) if isinstance(ticker, str) else None
        if (not isinstance(ticker, str) or look is None or look.get("value") is None
                or look.get("unit") is None or row.get("unit") is None):
            skipped.append(ticker)
            continue
        compared.append(ticker)
        values.append(float(look["value"]))
        lookup_units.add(look["unit"])
        if row["unit"] != look["unit"]:
            row_mismatch = True

    def result(verdict: str, reason: str, inversions: list[list[str]]) -> dict:
        return _verdict("screen_order", verdict, reason, order=order, compared=compared,
                        values=values, skipped=skipped, inversions=inversions,
                        tolerance=rel_tol)

    if row_mismatch or len(lookup_units) > 1:
        return result("inconsistent", "unit_mismatch", [])
    if len(compared) < 2:
        return result("not_applicable", "too_few_rows", [])
    inversions: list[list[str]] = []
    for i in range(len(compared)):
        for j in range(i + 1, len(compared)):
            a, b = values[i], values[j]
            if _ties(a, b, rel_tol):
                continue
            if (a < b) if order == "desc" else (a > b):
                inversions.append([compared[i], compared[j]])
    if inversions:
        return result("inconsistent", "misordered", inversions)
    return result("consistent", "match", inversions)


def check_q4(q4: dict, fy: dict, nine_months: dict, rel_tol: float = 1e-3) -> dict:
    """Check that Q4 equals the full year minus the nine-month year-to-date figure.

    Args:
        q4: The fourth-quarter amount.
        fy: The full-year amount.
        nine_months: The nine-month year-to-date amount.
        rel_tol: Tolerance relative to the full year's magnitude.

    Returns:
        A verdict dict (`check`, `verdict`, `reason`, `observed`, `expected`, `tolerance`).
    """
    name = "q4"
    decided = _unit_policy([q4, fy, nine_months])
    if decided is not None:
        return _verdict(name, *decided, observed=None, expected=None, tolerance=None)
    fy_value = float(fy["value"])
    observed = float(q4["value"])
    expected = fy_value - float(nine_months["value"])
    tolerance = rel_tol * abs(fy_value)
    ok = abs(observed - expected) <= tolerance
    return _verdict(name, "consistent" if ok else "inconsistent", "match" if ok else "mismatch",
                    observed=observed, expected=expected, tolerance=tolerance)
