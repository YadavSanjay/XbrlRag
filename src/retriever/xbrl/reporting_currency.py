# The unit a single-filer lookup answers in (docs/specs/lookup-reporting-currency.md).
# A 20-F filer that reports in another currency also tags a convenience translation (TSMC: TWD
# and USD) at the latest period. The lookup's S1 selection keeps only the year's own column, so
# both units sit at one period end and the coverage rule (choose_lookup_unit,
# docs/specs/reporting-currency-coverage.md) cannot tell them apart. The reporting currency is
# therefore decided over every row of the measure the lookup read, by the same rule the screen
# and the answer keys use; the candidates are then kept in that unit.
#
# choose_lookup_unit lives here (re-exported from xbrl_factstore_lookup, its old home) so the
# lookup, the screen and the key builder share one definition without an import cycle.

from __future__ import annotations

from collections.abc import Sequence


def choose_lookup_unit(
    observations: Sequence[tuple[str | None, str | None]],
    requested_unit: str | None = None,
) -> str | None:
    """The single unit a lookup should resolve in.

    Args:
        observations: ``(unit, period_end)`` pairs for one concept, period ends as ISO
            dates.
        requested_unit: An ISO 4217 code the question asked for, if any.

    Returns:
        The requested unit when the latest period carries it; otherwise the latest
        period's only unit, or the latest-period unit tagged at the most distinct
        period ends (the reporting currency, not a convenience translation). None when
        no single unit qualifies.
    """
    dated = [(unit, end) for unit, end in observations if end]
    if not dated:
        return None
    latest = max(end for _, end in dated)  # ISO dates order lexically
    latest_units = {unit for unit, end in dated if end == latest and unit}
    if requested_unit is not None:
        return requested_unit if requested_unit in latest_units else None
    if not latest_units:  # the latest period carries no unit: no single unit qualifies
        return None
    if len(latest_units) == 1:
        return next(iter(latest_units))
    # Several units at the latest period: the reporting currency is the one the filing
    # tags across the most periods. A 20-F convenience translation covers fewer: TSMC's
    # USD is tagged for the latest period, and on its balance sheet also for the prior
    # year-end, while TWD covers every comparative
    # (docs/specs/reporting-currency-coverage.md).
    coverage = {u: len({end for v, end in dated if v == u}) for u in latest_units}
    best = max(coverage.values())
    top = [u for u, n in coverage.items() if n == best]
    return top[0] if len(top) == 1 else None


def answer_unit(
    candidate_units: Sequence[str | None],
    observations: Sequence[tuple[str | None, str | None]],
) -> str | None:
    """The single unit a lookup with no requested unit answers in.

    Args:
        candidate_units: The shown unit (``display_unit``) of each candidate that may answer,
            after the S1 column selection; None for a unitless candidate.
        observations: ``(shown unit, period end)`` of every stored row of the measure the
            lookup read (every filing, every period, every unit), period ends as ISO dates.

    Returns:
        The candidates' only unit when they carry exactly one; otherwise the filer's
        reporting currency, ``choose_lookup_unit(observations)`` with no requested unit,
        when a candidate carries it. None when the candidates carry no unit or no single
        unit qualifies.
    """
    units = {u for u in candidate_units if u}
    if not units:
        return None
    if len(units) == 1:
        return next(iter(units))
    reporting = choose_lookup_unit(observations)
    return reporting if reporting in units else None
