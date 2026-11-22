"""Fixtures for period-year-length-answers (docs/specs/period-year-length-answers.md).

Pure functions; nothing is faked. The stored periods are the ones period-disclosure C3 pinned
(SEC companyfacts start/end; the store's end is the period's last day + 1), and AAPL's first
quarters from its 10-Q cover periods. Each period is ``(stored start, stored end)``.
Reached through the session-scoped ``kit`` fixture, so hypothesis tests can use it.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

# fact_id -> (period_start, stored period_end)
PERIODS: dict[int, tuple[str, str]] = {
    14262: ("2023-10-01", "2024-09-29"),  # AAPL revenue FY2024, 364 days
    12096: ("2022-09-25", "2023-10-01"),  # AAPL revenue FY2023, 371 days (53 weeks)
    10919: ("2021-09-26", "2022-09-25"),  # AAPL revenue FY2022, 364 days
    105742: ("2025-07-01", "2026-07-01"),  # MSFT revenue FY2026, 365 days
    104052: ("2024-07-01", "2025-07-01"),  # MSFT revenue FY2025, 365 days
    9001: ("2022-09-25", "2023-01-01"),  # AAPL revenue Q1 FY2023, 98 days (14 weeks)
    9002: ("2021-09-26", "2021-12-26"),  # AAPL revenue Q1 FY2022, 91 days
}

AAPL_CIK = 320193
MSFT_CIK = 789019


def lookup_input(
    step_id: str,
    fact_id: int,
    fy: Any,
    *,
    cik: Any = AAPL_CIK,
    family: str = "Revenue",
    fiscal_period: Any = "FY",
    ytd: Any = False,
    op: str = "lookup",
    status: str = "OK",
    instant: bool = False,
) -> dict[str, Any]:
    """One input step as Requirement 2 describes it, with the fact's stored period."""
    start, end = PERIODS[fact_id]
    return {
        "step_id": step_id, "op": op, "status": status, "cik": cik,
        "concept_family": family, "fiscal_period": fiscal_period, "ytd": ytd, "fy": fy,
        "fact_id": fact_id, "period_start": None if instant else start, "period_end": end,
    }


def period_map() -> dict[int, dict[str, str]]:
    """``periods`` for ``screen_year_lengths``: fact id -> stored start and end."""
    return {fid: {"period_start": s, "period_end": e} for fid, (s, e) in PERIODS.items()}


@pytest.fixture(scope="session")
def kit() -> types.SimpleNamespace:
    """The helpers above, for tests (hypothesis tests take session fixtures only)."""
    return types.SimpleNamespace(
        PERIODS=PERIODS, AAPL_CIK=AAPL_CIK, MSFT_CIK=MSFT_CIK,
        lookup_input=lookup_input, period_map=period_map,
    )
