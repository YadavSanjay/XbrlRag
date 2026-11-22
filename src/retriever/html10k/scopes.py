# What to search, from the resolver's result: never a guess, and only what resolved.
#
# Spec: docs/specs/html-rag-arm.md.

from __future__ import annotations

from src.retriever.extract_year_util import extract_years_in_range
from src.retriever.xbrl.branch_dispatcher import BranchDispatcher
from src.retriever.xbrl.intent_router import IntentRouterResult

# The extractor's own range for a year written in a period mention, as in filing_targets.
_MENTION_YEAR_RANGE = [2018, 2035]


def _period_years(result: IntentRouterResult) -> list[int]:
    """The sorted years of the result's periods that are not ambiguous."""
    years: set[int] = set()
    for period in result.periods:
        if period.ambiguous:
            continue
        if period.fiscal_year is not None:
            years.add(period.fiscal_year)
        # The extractor gives one period per year of a range; re-reading the mention keeps
        # every year for a result built otherwise (the same extract_years_in_range reading).
        years.update(extract_years_in_range(period.mention, year_range=_MENTION_YEAR_RANGE))
    return sorted(years)


def search_scopes(
    result: IntentRouterResult, max_filer_searches: int
) -> tuple[list[dict[str, list]], str | None]:
    """Turn a router result into the searches to run.

    Args:
        result: The router's result for one question.
        max_filer_searches: With more filers than this, one search covers them all.

    Returns:
        The scopes, each ``{"ciks": [...], "fiscal_years": [...]}`` where an empty list means no
        filter on that field; and XbrlRag's reason for degrading, or ``None``.
    """
    reasons = BranchDispatcher._unresolved_reasons(result)
    reason = "; ".join(reasons) if reasons else None

    # An ambiguous company is never filtered on, even when it carries a candidate cik.
    filers = {c.cik: c.last_seen_fy for c in result.companies if c.cik and not c.is_ambiguous}
    ciks = sorted(filers)
    years = _period_years(result)

    def years_of(cik: str) -> list[int]:
        if result.periods:
            return list(years)
        last_seen = filers[cik]
        return [] if last_seen is None else [last_seen]

    if not ciks:
        return [{"ciks": [], "fiscal_years": years}], reason
    if len(ciks) > max_filer_searches:
        union = sorted({year for cik in ciks for year in years_of(cik)})
        return [{"ciks": ciks, "fiscal_years": union}], reason
    return [{"ciks": [cik], "fiscal_years": years_of(cik)} for cik in ciks], reason
