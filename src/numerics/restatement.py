# Which filing answers a period: the filing's own column, the latest restatement of it (S1),
# and year-over-year pairs on one filing's basis. Pure functions over mv_financial_facts rows,
# standard library only, so the product (src.retriever.xbrl.restatement re-exports them) and
# set B's key check (src.eval.set_b.pg_store) share one definition.
# Spec: docs/specs/restatement-s1.md (requirements 1, 2 and 5); docs/specs/set-b-restatement.md.

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from typing import Any

# The cover-page share count, kept in a filing's own column although it is dated after the
# period end (docs/specs/shares-outstanding-reading.md).
COVER_SHARES_LOCAL_NAME = "EntityCommonStockSharesOutstanding"
# The keys that identify a group: one measure at one period (requirement 2).
_GROUP_KEYS = ("concept", "unit", "segment_axis", "segment_member", "period_start", "period_end")
# Inclusive day window between a current and a prior period end, and the largest difference
# in duration length, of a year-over-year pair (requirement 5).
_PRIOR_MIN_DAYS = 350
_PRIOR_MAX_DAYS = 380
_LENGTH_TOLERANCE_DAYS = 14
_ONE_DAY = dt.timedelta(days=1)


def _day(value: Any) -> dt.date | None:
    """A date, datetime or ISO string as a ``date``; ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _local(qname: Any) -> str | None:
    """The local name of a QName (after the last ``:``); ``None`` stays ``None``."""
    if qname is None:
        return None
    return str(qname).rsplit(":", 1)[-1]


def _measure(row: Mapping[str, Any]) -> tuple:
    """A group without its period: concept and members by local name, and the unit."""
    return (_local(row["concept"]), row["unit"], _local(row["segment_axis"]),
            _local(row["segment_member"]))


def _group(row: Mapping[str, Any]) -> tuple | None:
    """A row's group, or ``None`` when it lacks a group key."""
    if any(k not in row for k in _GROUP_KEYS):
        return None
    return (*_measure(row), _day(row["period_start"]), _day(row["period_end"]))


def _is_cover_shares(row: Mapping[str, Any]) -> bool:
    return _local(row.get("concept")) == COVER_SHARES_LOCAL_NAME


def _in_own_column(row: Mapping[str, Any]) -> bool:
    """Whether a valued row ends on its filing's DEI period end (or is its cover share count)."""
    period_end = _day(row.get("period_end"))
    dpe = _day(row.get("document_period_end"))
    if period_end is None or dpe is None:
        return False
    if period_end == dpe + _ONE_DAY:
        return True
    if _is_cover_shares(row):
        filed = _day(row.get("filed_date"))
        return filed is not None and dpe < period_end - _ONE_DAY <= filed
    return False


def _filing_order(row: Mapping[str, Any]) -> tuple[dt.date, str]:
    """(filed date, accession): the order in which filings supersede one another."""
    return (_day(row.get("filed_date")) or dt.date.min, str(row.get("accession_no")))


def current_column(rows: Sequence[Mapping[str, Any]], fy: int, fiscal_period: str = "FY",
                   as_originally_reported: bool = False) -> list[dict]:
    """The rows of the filing labelled ``fy`` that report its own period (requirement 1).

    Args:
        rows: ``mv_financial_facts`` rows of one filer and measure, from any filings.
        fy: The requested fiscal-year label.
        fiscal_period: The requested fiscal period (``FY``, ``Q1``...).
        as_originally_reported: Read the earliest-filed original filing labelled ``fy``
            instead of the canonical one.

    Returns:
        The own filing's valued rows ending exactly on its DEI period end (plus its cover-page
        share count); empty when there are none.
    """
    own = [r for r in rows
           if r.get("fiscal_year") == fy and r.get("fiscal_period") == fiscal_period]
    if as_originally_reported:
        originals = [r for r in own if r.get("is_amendment") is False]
        if not originals:
            return []
        accessions = {min(originals, key=_filing_order)["accession_no"]}
    else:
        accessions = {r.get("accession_no") for r in own if r.get("is_canonical_filing")}
    return [dict(r) for r in own
            if r.get("accession_no") in accessions and r.get("value") is not None
            and _in_own_column(r)]


def _is_candidate(row: Mapping[str, Any]) -> bool:
    """Whether a row may restate a period: valued, canonical, dated and labelled."""
    return (row.get("value") is not None and row.get("is_canonical_filing") is True
            and row.get("filed_date") is not None and row.get("fiscal_year") is not None
            and row.get("accession_no") is not None)


def latest_restatement(current: Sequence[Mapping[str, Any]],
                       rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Each group of ``current`` as the latest later-labelled filing reports it (requirement 2).

    Args:
        current: The rows that answer as filed, e.g. ``current_column``'s result.
        rows: Candidate rows of the same filer and measure, from any filings.

    Returns:
        Per group, the rows of the latest-filed canonical filing with a greater label that
        reports the same period; the group's own rows when there is none.
    """
    out: list[dict] = []
    groups: dict[tuple, list[Mapping[str, Any]]] = {}
    for r in current:
        g = _group(r)
        if g is None or r.get("fiscal_year") is None:
            out.append(dict(r))
        else:
            groups.setdefault(g, []).append(r)
    if not groups:
        return out
    by_group: dict[tuple, list[Mapping[str, Any]]] = {}
    for r in rows:
        if not _is_candidate(r):
            continue
        g = _group(r)
        if g in groups:
            by_group.setdefault(g, []).append(r)
    for g, mine in groups.items():
        label = max(r["fiscal_year"] for r in mine)
        later = [r for r in by_group.get(g, []) if r["fiscal_year"] > label]
        if not later:
            out.extend(dict(r) for r in mine)
            continue
        winner = max(later, key=_filing_order)["accession_no"]
        out.extend(dict(r) for r in later if r["accession_no"] == winner)
    return out


def _length(row: Mapping[str, Any]) -> int | None:
    start, end = _day(row.get("period_start")), _day(row.get("period_end"))
    if start is None or end is None:
        return None
    return (end - start).days


def _comparable(prior: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Whether ``prior`` is the same kind of period as ``current`` (instant or like length)."""
    if current.get("period_start") is None:
        return prior.get("period_start") is None
    a, b = _length(prior), _length(current)
    return a is not None and b is not None and abs(a - b) <= _LENGTH_TOLERANCE_DAYS


def _has_group_keys(row: Mapping[str, Any]) -> bool:
    return all(k in row for k in _GROUP_KEYS)


def _latest(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The row with the latest period end, then the greatest fact id."""
    return max(rows, key=lambda r: (_day(r["period_end"]) or dt.date.min,
                                    r.get("fact_id") or 0))


def _days_before(earlier: Any, later: Any) -> int | None:
    """Days from ``earlier`` to ``later``; ``None`` when either date is missing."""
    a, b = _day(earlier), _day(later)
    if a is None or b is None:
        return None
    return (b - a).days


def yoy_pairs(rows: Sequence[Mapping[str, Any]], fy: int,
              fiscal_period: str = "FY") -> list[dict]:
    """Year-over-year pairs on the current filing's own basis (requirement 5).

    Args:
        rows: ``mv_financial_facts`` rows of one filer and measure, from any filings.
        fy: The fiscal-year label of the current side.
        fiscal_period: The fiscal period of both sides.

    Returns:
        One ``{"current", "prior", "same_filing", "yoy_pct_change"}`` dict per current row
        that has a prior.
    """
    currents = [r for r in current_column(rows, fy, fiscal_period)
                if not _is_cover_shares(r) and _has_group_keys(r)]
    if not currents:
        return []
    fallback = [r for r in current_column(rows, fy - 1, fiscal_period)
                if not _is_cover_shares(r) and _has_group_keys(r)]
    by_accession: dict[Any, list[Mapping[str, Any]]] = {}
    for r in rows:
        if r.get("value") is not None and _has_group_keys(r):
            by_accession.setdefault(r.get("accession_no"), []).append(r)
    pairs: list[dict] = []
    for c in currents:
        measure = _measure(c)
        same: list[Mapping[str, Any]] = []
        for p in by_accession.get(c["accession_no"], []):
            gap = _days_before(p["period_end"], c["period_end"])
            if gap is not None and _PRIOR_MIN_DAYS <= gap <= _PRIOR_MAX_DAYS \
                    and _measure(p) == measure and _comparable(p, c):
                same.append(p)
        if same:
            prior, same_filing = _latest(same), True
        else:
            older = [p for p in fallback if _measure(p) == measure and _comparable(p, c)]
            if not older:
                continue
            prior, same_filing = _latest(older), False
        change = None
        if prior["value"] != 0:
            change = (float(c["value"]) - float(prior["value"])) / abs(float(prior["value"]))
        pairs.append({"current": dict(c), "prior": dict(prior), "same_filing": same_filing,
                      "yoy_pct_change": change})
    return pairs


def _local_name(qname: Any) -> str | None:
    """The local name of a QName, URI-qualified or Clark name (after the last ``:`` or ``}``)."""
    if qname is None:
        return None
    return re.split(r"[:}]", str(qname))[-1]


def _dimensional_group(row: Mapping[str, Any]) -> tuple | None:
    """A row's group with every dimension (dimensional-s1 R2); ``None`` when not groupable."""
    if any(k not in row for k in ("concept", "unit", "period_start", "period_end")):
        return None
    if "segment_axis" not in row and "dimensions" not in row:
        return None
    identity = dimension_identity(row)
    if identity is None:
        return None
    return (_local_name(row["concept"]), row["unit"], identity, _day(row["period_start"]),
            _day(row["period_end"]))


def dimension_identity(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...] | None:
    """The dimensions that identify a row's measure, by local name (dimensional-s1 R1).

    Args:
        row: An ``mv_financial_facts`` or ``mv_financial_facts_multidim`` row.

    Returns:
        The sorted ``(axis, member)`` local-name pairs: every pair of a non-empty
        ``dimensions`` mapping; else ``()`` for an undimensioned row; else ``None`` for a
        flat-view row of a multi-dimension fact (its other dimensions are unknown); else the
        one pair of ``segment_axis`` / ``segment_member``.
    """
    dims = row.get("dimensions")
    if dims:
        return tuple(sorted((str(_local_name(a)), str(_local_name(m))) for a, m in dims.items()))
    if row.get("segment_axis") is None:
        return ()
    if row.get("has_multiple_dimensions"):
        return None
    return ((str(_local_name(row["segment_axis"])), str(_local_name(row.get("segment_member")))),)


def latest_dimensional_restatement(current: Sequence[Mapping[str, Any]],
                                   rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """``latest_restatement`` with every dimension in the group (dimensional-s1 R2).

    Args:
        current: The rows that answer as filed, e.g. ``current_column``'s result.
        rows: Candidate rows of the same filer and measure, from any filings.

    Returns:
        Per group (concept and dimensions by local name, unit, period), the rows of the
        latest-filed canonical filing with a greater label that reports it; the group's own
        rows when there is none.
    """
    out: list[dict] = []
    groups: dict[tuple, list[Mapping[str, Any]]] = {}
    for r in current:
        g = _dimensional_group(r)
        if g is None or r.get("fiscal_year") is None:
            out.append(dict(r))
        else:
            groups.setdefault(g, []).append(r)
    if not groups:
        return out
    by_group: dict[tuple, list[Mapping[str, Any]]] = {}
    for r in rows:
        if not _is_candidate(r):
            continue
        g = _dimensional_group(r)
        if g is not None and g in groups:
            by_group.setdefault(g, []).append(r)
    for g, mine in groups.items():
        label = max(r["fiscal_year"] for r in mine)
        later = [r for r in by_group.get(g, []) if r["fiscal_year"] > label]
        if not later:
            out.extend(dict(r) for r in mine)
            continue
        winner = max(later, key=_filing_order)["accession_no"]
        out.extend(dict(r) for r in later if r["accession_no"] == winner)
    return out


def breakdown_filings(rows: Sequence[Mapping[str, Any]], fy: int, axes: Sequence[str],
                      fiscal_period: str = "FY",
                      as_originally_reported: bool = False) -> list[str]:
    """The filings that may answer a breakdown of the year, latest first (dimensional-s1 R5).

    Args:
        rows: ``mv_financial_facts`` rows of one filer and measure, any filing.
        fy: The requested fiscal-year label.
        axes: Local names of the axes the breakdown splits on.
        fiscal_period: The requested fiscal period.
        as_originally_reported: Only the original filing labelled ``fy``.

    Returns:
        Accessions, latest (filed date, accession) first, of the filings that report the
        year's undimensioned total and a single-dimension member on ``axes`` at one period.
    """
    own = current_column(rows, fy, fiscal_period, as_originally_reported)
    periods = {(_day(r.get("period_start")), _day(r.get("period_end")))
               for r in own if dimension_identity(r) == ()}
    if not periods:
        return []
    candidates = {r.get("accession_no") for r in own}
    if not as_originally_reported:
        candidates |= {r["accession_no"] for r in rows
                       if _is_candidate(r) and r["fiscal_year"] > fy}
    axis_names = {_local_name(a) for a in axes}
    # Per accession and period: whether it has a valued total and a valued member.
    totals: set[tuple] = set()
    members: set[tuple] = set()
    filed: dict[Any, tuple[dt.date, str]] = {}
    for r in rows:
        acc = r.get("accession_no")
        if acc not in candidates or r.get("value") is None:
            continue
        period = (_day(r.get("period_start")), _day(r.get("period_end")))
        if period not in periods:
            continue
        identity = dimension_identity(r)
        if identity == ():
            totals.add((acc, period))
        elif identity is not None and len(identity) == 1 and identity[0][0] in axis_names:
            members.add((acc, period))
        else:
            continue
        filed[acc] = max(filed.get(acc, _filing_order(r)), _filing_order(r))
    qualifying = {acc for acc, period in totals & members}
    return sorted(qualifying, key=lambda a: filed[a], reverse=True)
