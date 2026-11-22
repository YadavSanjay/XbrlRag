"""The Postgres ``KeyFactStore`` for set B's answer-key check.

``facts_for(ticker, fiscal_year)`` reads one filer's ``mv_financial_facts`` rows and selects
the year's candidates with ``key_candidates``, restricted to annual durations and instants:
a flat fact follows S1 (the latest canonical filing with a later fiscal-year label that
reports the year's own period, else the year's own filing, via the product's
``current_column`` / ``latest_restatement``; docs/specs/set-b-restatement.md), and a lone
dimensional fact follows S1 too: a member of the year's own column (the canonical FY filing,
10-K or 20-F) answers from the latest later-labelled canonical filing that reports the same
concept, unit, period and full dimension set, else as filed
(``latest_dimensional_restatement``; a member only a later filing reports is never added).
A multi-axis fact is included too, identified by every axis of
``mv_financial_facts_multidim.dimensions`` and labelled from its ``dimension_labels``.

``breakdown_facts_for(ticker, fiscal_year)`` gives a breakdown's members instead
(``breakdown_candidates``): per concept family and axis kind (segment, product,
geography), the single-dimension members of the latest filing that splits the year
(``breakdown_filings``'s first), at the year's own period. The store cannot see the
filing's arcs, so a parent member of that filing is a candidate too. Both reads come from
one query per filer-year; every filing labelled the year or later is read. Nil facts are
left out. Read-only; each filer-year is read once and cached.
Spec: docs/specs/set-b-dimensional-s1.md.

**Member labels.** A single-axis fact's label is the one the main suite's breakdown
templates put in ``segment_name`` / ``geo_name`` (``configs/eval/sql_derived_templates.yaml``
aq-3a/3b/4a): ``COALESCE(segment_label, <member local name>)``, cleaned the way
``src/eval/suites/sql_derived.py::clean_member_label`` cleans every expected row entity
(each ``[Member]`` marker dropped). A multi-axis fact joins its cleaned labels, ordered by
axis, with ``" | "``. A flat fact has None.

**Dates.** The fact store keeps Arelle's date convention: an XBRL ``endDate`` or
``instant`` is stored as the next midnight, one day late, while start dates are stored as
written. ``period_end`` is moved back one day here, exactly as
``src/eval/sec_check/pg_store.py::record_from_row`` does.

Spec: docs/specs/set-b-tooling.md (requirements 5 and 11; not spec-tested).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.eval.set_b.base import KeyFactStore
from src.logging_config import get_logger
from src.numerics.restatement import (
    breakdown_filings,
    current_column,
    dimension_identity,
    latest_dimensional_restatement,
    latest_restatement,
)
from src.numerics.stated_numbers import display_unit

logger = get_logger(__name__)

# Statement and connect timeouts for the read-only session (as in sec_check).
_STATEMENT_TIMEOUT_MS = 120_000
_CONNECT_TIMEOUT_S = 10

# The main suite's label cleaning (src/eval/suites/sql_derived.py::_MEMBER_MARKER),
# repeated here because that module imports the retrieval pipeline (quarantine).
_MEMBER_MARKER = re.compile(r"\s*\[Member\]")

# Joins a multi-axis fact's member labels.
MULTI_AXIS_SEPARATOR = " | "

# Every row of one filer that ``key_candidates`` or ``breakdown_candidates`` may select
# (set-b-restatement requirement 2; set-b-dimensional-s1 requirement 4), read through a dict
# cursor. The pre-filters never change the result: every candidate is valued and annual or
# instant (a restating row reports the same period, so the same duration), and every rule
# reads only the year's own filing and later-labelled ones (fiscal_year >= the year). The
# multidim ``dimensions`` give a multi-axis fact its full identity. The DEI period end comes
# from mv_dei_metadata (one row per accession), as the product's lookup reads it.
_FILER_ROWS_SQL = """
SELECT m.fact_id, m.accession_no, m.fiscal_year, m.fiscal_period, m.filed_date,
       m.is_amendment, m.is_canonical_filing,
       (SELECT d.document_period_end FROM mv_dei_metadata d
         WHERE d.accession_no = m.accession_no LIMIT 1) AS document_period_end,
       m.concept, m.concept_family, m.unit, m.value,
       m.period_type, m.period_start, m.period_end, m.duration_kind, m.is_current_period,
       m.segment_axis, m.segment_member, m.segment_label,
       COALESCE(m.has_multiple_dimensions, FALSE) AS has_multiple_dimensions,
       md.dimensions, md.dimension_labels
  FROM mv_financial_facts m
  LEFT JOIN mv_financial_facts_multidim md
         ON md.fact_id = m.fact_id AND COALESCE(m.has_multiple_dimensions, FALSE)
 WHERE m.ticker = %(ticker)s
   AND m.value IS NOT NULL AND m.period_end IS NOT NULL
   AND (m.duration_kind = 'annual' OR m.period_type = 'instant')
   AND m.fiscal_year >= %(fiscal_year)s
 ORDER BY m.fact_id
"""


def clean_member_label(label: str) -> str:
    """Drops every ``[Member]`` marker, as the main suite does for its row entities.

    Args:
        label: A member label, e.g. ``"Data Center [Member]"``.

    Returns:
        The cleaned label, e.g. ``"Data Center"``.
    """
    return _MEMBER_MARKER.sub("", str(label)).strip()


def member_label(segment_label: str | None, segment_member: str | None,
                 multi_axis_labels: dict[str, str] | None = None) -> str | None:
    """The label set B matches a member hint against and writes as a breakdown entity.

    Args:
        segment_label: ``mv_financial_facts.segment_label`` (first axis), or None.
        segment_member: ``mv_financial_facts.segment_member`` (``prefix:LocalName``).
        multi_axis_labels: ``mv_financial_facts_multidim.dimension_labels`` (axis ->
            label) for a multi-axis fact; None otherwise.

    Returns:
        The cleaned label; for a multi-axis fact every axis's label, ordered by axis;
        None for a flat fact.
    """
    if multi_axis_labels:
        return MULTI_AXIS_SEPARATOR.join(
            clean_member_label(multi_axis_labels[axis]) for axis in sorted(multi_axis_labels))
    if segment_label is not None:
        return clean_member_label(segment_label)
    if segment_member is not None:
        return clean_member_label(re.sub(r"^.*:", "", segment_member))
    return None


def _iso(value: dt.date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def candidate_from_row(row: Sequence[Any]) -> dict:
    """Builds a candidate dict (spec requirement 5) from a fact row tuple.

    Args:
        row: ``(fact_id, concept, concept_family, unit, value, period_start, period_end,
            dimensional, segment_label, segment_member, multi_axis, dimension_labels)``.

    Returns:
        The candidate, with ISO dates (the stored end moved back one day), a float value,
        and the unit in the form set B writes (``display_unit``: the stored ``shares*USD``
        becomes ``USD/shares``; the currency is kept and other units are unchanged).
    """
    (fact_id, concept, family, unit, value, start, end, dimensional, seg_label,
     seg_member, multi_axis, dim_labels) = row
    if isinstance(end, str):
        end = dt.date.fromisoformat(end)
    end = end - dt.timedelta(days=1)
    dimensional = bool(dimensional)
    label = None
    if dimensional:
        label = member_label(seg_label, seg_member,
                             dict(dim_labels) if multi_axis and dim_labels else None)
    return {
        "fact_id": int(fact_id),
        "concept": str(concept),
        "concept_family": family,
        "unit": display_unit(str(unit)),
        "value": float(value),
        "period_start": _iso(start),
        "period_end": _iso(end) or "",
        "dimensional": dimensional,
        "member_label": label,
    }


def key_candidates(rows: Sequence[dict[str, Any]], fiscal_year: int) -> list[dict]:
    """The filer-year's lone candidate facts: flat and dimensional ones on S1.

    A flat fact is the latest restatement of the year's own column
    (``latest_restatement``); a dimensional one the latest restatement with every
    dimension in the identity (``latest_dimensional_restatement``), so a member only a
    later filing reports is never added.

    Args:
        rows: Every ``mv_financial_facts`` row of one filer (all filings and periods),
            with ``dimensions`` for a multi-axis fact.
        fiscal_year: The fiscal year.

    Returns:
        Candidate dicts (set-b-tooling requirement 5), sorted by fact id.

    Spec: docs/specs/set-b-restatement.md (requirement 1);
    docs/specs/set-b-dimensional-s1.md (requirement 1).
    """
    flat_rows = [r for r in rows if r.get("segment_axis") is None]
    selected: dict[int, dict[str, Any]] = {}
    for r in latest_restatement(current_column(flat_rows, fiscal_year, "FY"), flat_rows):
        if _annual_or_instant(r):
            selected[int(r["fact_id"])] = r
    dim_rows = [r for r in rows if r.get("segment_axis") is not None and _annual_or_instant(r)]
    for r in latest_dimensional_restatement(current_column(dim_rows, fiscal_year, "FY"),
                                            dim_rows):
        selected[int(r["fact_id"])] = r
    return [_candidate_from_mapping(selected[i]) for i in sorted(selected)]


# The breakdown axis kinds and their axis local names (the product's BREAKDOWN_AXES in
# src/retriever/xbrl/xbrl_factstore_breakdown.py, repeated: the set-B CLI must not import
# the retriever).
_BREAKDOWN_AXES: dict[str, tuple[str, ...]] = {
    "segment": ("StatementBusinessSegmentsAxis",),
    "product": ("ProductOrServiceAxis",),
    "geography": ("StatementGeographicalAxis", "GeographicalAreasAxis"),
}


def _day(value: Any) -> dt.date | None:
    """A date, datetime or ISO string as a ``date``; None stays None."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _period(row: Mapping[str, Any]) -> tuple[dt.date | None, dt.date | None]:
    return (_day(row.get("period_start")), _day(row.get("period_end")))


def breakdown_candidates(rows: Sequence[dict[str, Any]], fiscal_year: int) -> list[dict]:
    """The filer-year's breakdown members: the latest filing that splits the year.

    Per concept family and axis kind, the first (latest-filed) accession of
    ``breakdown_filings`` gives every row it holds that is a single-dimension member on the
    kind's axes, at the period of an undimensioned row of the year's own column. Flat rows
    and cells are never candidates.

    Args:
        rows: Every ``mv_financial_facts`` row of one filer (all filings and periods),
            with ``dimensions`` for a multi-axis fact.
        fiscal_year: The fiscal year.

    Returns:
        Candidate dicts (set-b-tooling requirement 5), each once, sorted by fact id.

    Spec: docs/specs/set-b-dimensional-s1.md (requirement 2).
    """
    groups: dict[Any, list[Mapping[str, Any]]] = {}
    for r in rows:
        if (r.get("concept_family") is not None and r.get("period_type") == "duration"
                and r.get("duration_kind") == "annual" and r.get("value") is not None):
            groups.setdefault(r["concept_family"], []).append(r)
    selected: dict[int, Mapping[str, Any]] = {}
    for group in groups.values():
        periods = {_period(r) for r in current_column(group, fiscal_year, "FY")
                   if dimension_identity(r) == ()}
        if not periods:
            continue
        for axes in _BREAKDOWN_AXES.values():
            filings = breakdown_filings(group, fiscal_year, axes)
            if not filings:
                continue
            for member in group:
                if member.get("accession_no") != filings[0] or _period(member) not in periods:
                    continue
                identity = dimension_identity(member)
                if identity is not None and len(identity) == 1 and identity[0][0] in axes:
                    selected[int(member["fact_id"])] = member
    return [_candidate_from_mapping(selected[i]) for i in sorted(selected)]


def _annual_or_instant(row: Mapping[str, Any]) -> bool:
    return row.get("duration_kind") == "annual" or row.get("period_type") == "instant"


def _candidate_from_mapping(row: Mapping[str, Any]) -> dict:
    """``candidate_from_row`` over a store row given as a mapping."""
    multi_axis = bool(row.get("has_multiple_dimensions"))
    return candidate_from_row((
        row["fact_id"], row["concept"], row.get("concept_family"), row["unit"], row["value"],
        row.get("period_start"), row["period_end"], row.get("segment_axis") is not None,
        row.get("segment_label"), row.get("segment_member"), multi_axis,
        row.get("dimension_labels"),
    ))


class PostgresKeyFactStore(KeyFactStore):
    """Reads candidate facts from the local ``xbrl_db``, read-only."""

    def __init__(self, conn: Any = None) -> None:
        """Builds the store; connects lazily via ``src.db.conn`` unless given a connection.

        Args:
            conn: An open psycopg2 connection; the store opens its own when None.
        """
        self._conn = conn
        self._owns_conn = conn is None
        self._facts: dict[tuple[str, int], list[dict]] = {}
        self._breakdown: dict[tuple[str, int], list[dict]] = {}

    def __enter__(self) -> PostgresKeyFactStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Closes the connection the store opened, if any."""
        if self._conn is not None and self._owns_conn:
            self._conn.close()
        self._conn = None

    def _connection(self) -> Any:
        if self._conn is None:
            from src.db.conn import connect

            self._conn = connect(
                connect_timeout=_CONNECT_TIMEOUT_S,
                options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}",
            )
            self._conn.set_session(readonly=True, autocommit=True)
        return self._conn

    def facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        """Returns the filer-year's annual and FY-end facts, flat and dimensional (S1).

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.

        Returns:
            Candidate dicts with the keys in requirement 5 of the spec (copies).
        """
        key = (ticker, int(fiscal_year))
        if key not in self._facts:
            self._facts[key] = self._read_candidates(ticker, key[1])
            logger.info("set_b: %s FY%d: %d candidate facts", ticker, key[1],
                        len(self._facts[key]))
        return [dict(c) for c in self._facts[key]]

    def breakdown_facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        """Returns the filer-year's breakdown members (``breakdown_candidates``).

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.

        Returns:
            Candidate dicts with the keys in requirement 5 of the spec (copies).

        Spec: docs/specs/set-b-dimensional-s1.md (requirement 3).
        """
        key = (ticker, int(fiscal_year))
        if key not in self._breakdown:
            # One read serves both: ``_read_candidates`` (via ``facts_for``, which a
            # subclass may override for its logging) fills the breakdown cache too.
            self.facts_for(ticker, key[1])
        if key not in self._breakdown:
            self._read_candidates(ticker, key[1])
        # Counts only: H-X inherits this method and never logs the filer.
        logger.debug("one filer-year breakdown read: %d breakdown candidates",
                     len(self._breakdown[key]))
        return [dict(c) for c in self._breakdown[key]]

    def _read_candidates(self, ticker: str, fiscal_year: int) -> list[dict]:
        """Reads the filer's rows and selects the year's candidates.

        The lone candidates (``key_candidates``) are returned; the breakdown candidates
        (``breakdown_candidates``) of the same read are cached for
        ``breakdown_facts_for``.

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.

        Returns:
            The lone candidate dicts, sorted by fact id.

        Spec: docs/specs/set-b-restatement.md (requirement 2);
        docs/specs/set-b-dimensional-s1.md (requirement 4).
        """
        # Lazy, like the connection: set B's pure functions import without a DB driver.
        # psycopg2 ships no type stubs and types-psycopg2 is not a dependency.
        import psycopg2.extras  # type: ignore[import-untyped]

        params = {"ticker": ticker, "fiscal_year": int(fiscal_year)}
        with self._connection().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_FILER_ROWS_SQL, params)
            rows = [dict(r) for r in cur.fetchall()]
        self._breakdown[(ticker, int(fiscal_year))] = breakdown_candidates(rows,
                                                                           int(fiscal_year))
        return key_candidates(rows, int(fiscal_year))

