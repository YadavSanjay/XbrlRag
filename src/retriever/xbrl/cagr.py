"""A compound annual growth rate, computed in code (docs/specs/plan-cagr-and-ratios.md).

The `cagr` plan op takes two lookup steps of one filer and one metric; the number of years
is derived from their fiscal years, never written by the LLM, and the value is
(end / start) ** (1 / years) - 1, only when both are positive (S4). The calculator keeps
excluding `Pow`, so this is the one place a growth rate is annualized.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, cast


def _is_finite_real(value: Any) -> bool:
    """Whether ``value`` is an int or float (not a bool) with a finite value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _result(status: str, value: float | None = None, error: str | None = None) -> dict[str, Any]:
    return {"status": status, "value": value, "error": error}


def cagr_value(end: float | None, start: float | None, years: int | None) -> dict[str, Any]:
    """The CAGR from ``start`` to ``end`` over ``years`` years.

    Args:
        end: The later value.
        start: The earlier value.
        years: Whole years between them (``cagr_years``).

    Returns:
        ``{"status": "OK" | "NOT_MEANINGFUL" | "ERROR", "value", "error"}`` as in the spec's
        Requirement 1; ``value`` is None unless OK.
    """
    if not _is_finite_real(end):
        return _result("ERROR", error=f"end {end!r} is not a finite number")
    if not _is_finite_real(start):
        return _result("ERROR", error=f"start {start!r} is not a finite number")
    if isinstance(years, bool) or not isinstance(years, int) or years < 1:
        return _result("ERROR", error=f"years {years!r} must be a whole number of at least 1")
    end, start = cast(float, end), cast(float, start)  # finite reals, checked above
    if start <= 0:
        return _result(
            "NOT_MEANINGFUL",
            error=f"start {start!r} <= 0: a CAGR from a non-positive value is not meaningful",
        )
    if end <= 0:
        return _result(
            "NOT_MEANINGFUL",
            error=f"end {end!r} <= 0: a CAGR to a non-positive value is not meaningful",
        )
    return _result("OK", value=(end / start) ** (1 / years) - 1)


def _period_kind(step: Mapping[str, Any]) -> tuple[str, bool]:
    """A step's period kind: its fiscal period (absent means FY) and its ytd flag."""
    period = step.get("fiscal_period")
    return ("FY" if period is None else str(period).upper(), bool(step.get("ytd") or False))


def cagr_years(start_step: Mapping[str, Any], end_step: Mapping[str, Any]) -> int | None:
    """The years between two lookup plan steps, from their fiscal years.

    Args:
        start_step: The earlier lookup step (``fy``, ``fiscal_period``, ``ytd``).
        end_step: The later lookup step.

    Returns:
        ``end_step["fy"] - start_step["fy"]`` when both are ints, the end's is larger and
        the period kinds match (Requirement 2); otherwise None.
    """
    start_fy, end_fy = start_step.get("fy"), end_step.get("fy")
    for fy in (start_fy, end_fy):
        if isinstance(fy, bool) or not isinstance(fy, int):
            return None
    start_fy, end_fy = cast(int, start_fy), cast(int, end_fy)  # ints, checked above
    if end_fy <= start_fy:
        return None
    if _period_kind(start_step) != _period_kind(end_step):
        return None
    return end_fy - start_fy
