"""Fixtures for the fiscal-year-ended-date spec tests (docs/specs/fiscal-year-ended-date.md).

Home Depot's three annual rows are its public period ends and labels (its 10-K covers;
docs/specs/fiscal-label-alignment.md): fiscal 2023 ended 2024-01-28, fiscal 2024 ended
2025-02-02, fiscal 2025 ended 2026-02-01; its first quarter of fiscal 2025 ended 2025-05-04
(docs/specs/tenq-fiscal-labels.md C2). Everything else is synthetic and built here by hand.
No test reads ``data/``, the database or the clock.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from hypothesis import strategies as st

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def row(fiscal_year: int, fiscal_period: str, period_end: date, **extra) -> dict:
    """One fiscal_calendar row as a RealDictCursor returns it (extra columns allowed)."""
    return {"fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
            "period_end": period_end, **extra}


HD = [
    row(2023, "FY", date(2024, 1, 28), cik="0000354950", period_start=date(2023, 1, 30)),
    row(2024, "FY", date(2025, 2, 2), cik="0000354950", period_start=date(2024, 1, 29)),
    row(2025, "FY", date(2026, 2, 1), cik="0000354950", period_start=date(2025, 2, 3)),
    row(2025, "Q1", date(2025, 5, 4), cik="0000354950", period_start=date(2025, 2, 3)),
]
# The three annual ends and their labels, by hand from the rows above.
HD_LABELS = {date(2024, 1, 28): 2023, date(2025, 2, 2): 2024, date(2026, 2, 1): 2025}


def last_day(year: int, month: int) -> date:
    return date(year, month, _calendar.monthrange(year, month)[1])


def last_saturday(year: int, month: int) -> date:
    d = last_day(year, month)
    return d - timedelta(days=(d.weekday() - 5) % 7)


def nearest_sunday(year: int, month: int) -> date:
    """The Sunday nearest the last day of the month (at most 3 days either side)."""
    d = last_day(year, month)
    back = (d.weekday() - 6) % 7          # days since the last Sunday on or before d
    return d - timedelta(days=back) if back <= 3 else d + timedelta(days=7 - back)


YEAR_END = {"month": last_day, "last_saturday": last_saturday, "nearest_sunday": nearest_sunday}


def build_calendar(kind: str, fye_month: int, first_year: int, years: int,
                   label_offset: int) -> tuple[list[dict], dict[date, int], list[tuple]]:
    """A synthetic filer's calendar.

    Annual ends fall in ``fye_month`` of ``first_year`` .. ``first_year + years - 1`` by the
    ``kind`` rule (a nearest-Sunday end may fall a few days into the next month); year ``i``'s
    label is ``first_year + i + label_offset``, so labels strictly increase.
    Quarters 1-3 of each year after the first end 13, 26 and 39 weeks after the prior year end
    (month-end filers: 3, 6, 9 months), labelled with the year they belong to.

    Returns:
        ``(rows, annual, quarters)``: the rows; annual end → label; ``(end, label, "Qn")``.
    """
    end_of = YEAR_END[kind]
    ends = [end_of(first_year + i, fye_month) for i in range(years)]
    annual = {e: first_year + i + label_offset for i, e in enumerate(ends)}
    rows = [row(label, "FY", e) for e, label in annual.items()]
    quarters = []
    for prior, end in zip(ends, ends[1:], strict=False):
        for n in (1, 2, 3):
            if kind == "month":
                index = prior.year * 12 + prior.month - 1 + 3 * n
                q = last_day(index // 12, index % 12 + 1)
            else:
                q = prior + timedelta(weeks=13 * n)
            quarters.append((q, annual[end], f"Q{n}"))
            rows.append(row(annual[end], f"Q{n}", q))
    return rows, annual, quarters


calendars = st.builds(
    build_calendar,
    kind=st.sampled_from(sorted(YEAR_END)),
    fye_month=st.integers(1, 12),
    first_year=st.integers(2019, 2026),
    years=st.integers(2, 6),
    label_offset=st.sampled_from([-1, 0, 1]),
)


def f1(d: date, comma: bool = True) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}{',' if comma else ''} {d.year}"


def f2(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def f3(d: date) -> str:
    return d.isoformat()


# Words with no cue of requirement 3 and no digits, for padding mentions.
FILLER = st.sampled_from(["please", "report", "show", "the", "for", "value", "net", "income",
                          "of", "company", "what", "was", "stated", "in", "its"])


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The helpers above, for tests (hypothesis tests draw from ``kit`` with ``st.data()``)."""
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
