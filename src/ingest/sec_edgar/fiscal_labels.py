# A filing's fiscal-year label: the rule shared by the registries and the SEC answer keys.
# Pure: no database, no filesystem. Specs: docs/specs/fiscal-label-alignment.md (annual forms),
# docs/specs/tenq-fiscal-labels.md (a 10-Q placed on the filer's annual calendar).

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any, NamedTuple

from src.observability.trace_context import log_event

logger = logging.getLogger(__name__)

# The forms that take part in the collision rule; every other form passes through.
ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
# Inclusive day window of "the neighbouring year": 52/53-week years in, short years out.
_YEAR_MIN_DAYS = 350
_YEAR_MAX_DAYS = 380
_DAYS_PER_YEAR = 365.25
# The quarterly forms placed on the annual calendar (docs/specs/tenq-fiscal-labels.md).
QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A"})
# A 53-week year is 371 days, so every quarter end lies within this of both its year's ends.
_QUARTER_WINDOW_DAYS = 371
# A quarter of a 365.25-day year.
_DAYS_PER_QUARTER = 91.3125


class _TenqOutcome(NamedTuple):
    """A 10-Q labelled off its DEI (``placed``), or one left on today's rule unplaced."""

    accession: str
    period_end: date
    dei: int | None
    label: int
    placed: bool


class MovedPeriod(NamedTuple):
    """One annual period whose label is not its DEI, because labels must strictly increase."""

    period_end: date
    dei: int
    label: int
    accessions: tuple[str, ...]


def _period_dei(rows: Sequence[Mapping[str, Any]]) -> int | None:
    """The DEI of one period: its earliest filed tagged filing's (smallest accession on a tie)."""
    tagged = [r for r in rows if r["dei_fiscal_year"] is not None]
    if not tagged:
        return None
    first = min(tagged, key=lambda r: (r["filed_date"], r["accession_no"]))
    dei: int = first["dei_fiscal_year"]
    return dei


def _a_year_apart(earlier: date, later: date) -> bool:
    return _YEAR_MIN_DAYS <= (later - earlier).days <= _YEAR_MAX_DAYS


def _label_without_dei(end: date, prior_end: date | None, prior_label: int | None,
                       next_end: date | None, next_dei: int | None) -> int:
    """The starting label of a period with no DEI: prior, then next, then calendar year."""
    if prior_end is not None and prior_label is not None and _a_year_apart(prior_end, end):
        return prior_label + 1
    if next_end is not None and next_dei is not None and _a_year_apart(end, next_end):
        return next_dei - 1
    return end.year


def _label_annual_periods(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, int], list[MovedPeriod]]:
    """Walks the annual filings' distinct period ends in ascending order (requirements 2-5)."""
    by_end: dict[date, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_end.setdefault(row["period_end"], []).append(row)
    ends = sorted(by_end)
    deis = [_period_dei(by_end[end]) for end in ends]

    labels: dict[str, int] = {}
    moved: list[MovedPeriod] = []
    prior_end: date | None = None
    prior_label: int | None = None
    for i, end in enumerate(ends):
        dei = deis[i]
        label = dei
        if label is None:
            has_next = i + 1 < len(ends)
            label = _label_without_dei(end, prior_end, prior_label,
                                       ends[i + 1] if has_next else None,
                                       deis[i + 1] if has_next else None)
        if prior_end is not None and prior_label is not None and label <= prior_label:
            label = prior_label + max(1, round((end - prior_end).days / _DAYS_PER_YEAR))
        accessions = tuple(sorted(r["accession_no"] for r in by_end[end]))
        labels.update(dict.fromkeys(accessions, label))
        if dei is not None and label != dei:
            moved.append(MovedPeriod(end, dei, label, accessions))
        prior_end, prior_label = end, label
    return labels, moved


def place_quarter(period_end: date, annual_periods: Mapping[date, int]) -> tuple[int, int] | None:
    """The fiscal year and quarter of a quarterly period, from the filer's annual periods.

    Spec: docs/specs/tenq-fiscal-labels.md, requirement 1.

    Args:
        period_end: The quarter's last day (inclusive, SEC's date).
        annual_periods: The filer's annual period ends (inclusive) → each year's label.

    Returns:
        ``(fiscal_year, quarter)`` with ``quarter`` in 1-3, or ``None`` when no annual period
        end lies within 371 days of ``period_end`` or the period is not a first, second or
        third quarter.
    """
    prior = [e for e in annual_periods if 0 < (period_end - e).days <= _QUARTER_WINDOW_DAYS]
    later = [e for e in annual_periods if 0 < (e - period_end).days <= _QUARTER_WINDOW_DAYS]
    if prior:
        n = round((period_end - max(prior)).days / _DAYS_PER_QUARTER)
    elif later:
        n = 4 - round((min(later) - period_end).days / _DAYS_PER_QUARTER)
    else:
        return None
    year = annual_periods[min(later)] if later else annual_periods[max(prior)] + 1
    return (year, n) if 1 <= n <= 3 else None


def _label_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, int], list[MovedPeriod], list[_TenqOutcome]]:
    """The rule, plus each 10-Q labelled off its DEI and each one it could not place."""
    labels, moved = _label_annual_periods([r for r in rows if r["form_type"] in ANNUAL_FORMS])
    annual_periods = {r["period_end"]: labels[r["accession_no"]]
                      for r in rows if r["form_type"] in ANNUAL_FORMS}
    tenqs: list[_TenqOutcome] = []
    for row in rows:
        if row["form_type"] in ANNUAL_FORMS:
            continue
        dei = row["dei_fiscal_year"]
        fallback = row["period_end"].year if dei is None else dei
        if row["form_type"] not in QUARTERLY_FORMS:
            labels[row["accession_no"]] = fallback
            continue
        placed = place_quarter(row["period_end"], annual_periods)
        if placed is None:
            labels[row["accession_no"]] = fallback
            tenqs.append(_TenqOutcome(row["accession_no"], row["period_end"], dei, fallback,
                                      placed=False))
            continue
        labels[row["accession_no"]] = placed[0]
        if dei is not None and dei != placed[0]:
            tenqs.append(_TenqOutcome(row["accession_no"], row["period_end"], dei, placed[0],
                                      placed=True))
    return labels, moved, tenqs


def label_filings(
    filings: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, int], list[MovedPeriod]]:
    """The rule itself, also returning the periods it moved off their DEI.

    ``assign_fiscal_labels`` is this rule plus its warnings and decision events. A caller that runs
    the rule once per answer key takes this one, so a filer is not reported once per key.

    Args:
        filings: As for ``assign_fiscal_labels``.

    Returns:
        Accession → label, for every filing given, and the moved periods in date order.
    """
    labels, moved, _tenqs = _label_rows(list(filings))
    return labels, moved


def assign_fiscal_labels(filings: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """The fiscal-year label of each of one filer's filings (requirements 1-6 of the spec).

    The rule cannot tell a repeated backward slip from a forward slip followed by correct
    tags, so each period moved off its DEI is logged as a WARNING. A 10-Q takes the year of
    the annual period that contains it (docs/specs/tenq-fiscal-labels.md, requirement 2); one
    whose placed year differs from its DEI is logged as a WARNING too, and one that cannot be
    placed keeps its DEI (else its period end's calendar year), emitted as a ``decision``
    event so no such fallback is silent.

    Args:
        filings: One filer's filings, in any order. Each has ``accession_no`` (str),
            ``form_type`` (str), ``period_end`` (date), ``filed_date`` (date) and
            ``dei_fiscal_year`` (int | None, its DEI ``DocumentFiscalYearFocus``).

    Returns:
        Accession → label, for every filing given.
    """
    labels, moved, tenqs = _label_rows(list(filings))
    for period in moved:
        logger.warning("Fiscal label moved off DEI: period ending %s tags %d, labelled %d (%s)",
                       period.period_end, period.dei, period.label,
                       ", ".join(period.accessions))
    for q in tenqs:
        if q.placed:
            logger.warning("10-Q fiscal year placed off DEI: %s, period ending %s, tags %d, "
                           "labelled %d", q.accession, q.period_end, q.dei, q.label)
        else:
            log_event("decision", decision="tenq_unplaced_dei_fallback", source="fiscal_labels",
                      accession_no=q.accession, period_end=q.period_end.isoformat(),
                      dei_fiscal_year=q.dei, fiscal_year=q.label)
    return labels
