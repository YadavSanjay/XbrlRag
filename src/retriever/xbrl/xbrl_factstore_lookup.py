"""Deterministic SQL lookup for a single plan `lookup` step (docs/design/XbrlRag_Architecture.md
§3.1-3.2: fuzzy intent in, exactly one audited fact out).

Resolves one typed `lookup` step from a `plan_generator` plan to *exactly one*
financial fact (served via `mv_financial_facts` / `mv_financial_facts_multidim`),
plus a citation anchor and a resolution confidence. This is the
`step.op == "lookup"` branch of `plan_executor`; `compute` and `narrative` are
handled elsewhere.

Resolution mirrors §3 of the worked example, adapted to the *actual*
mv_financial_facts shape:

  1. concept_family + period_type  — the view already carries `concept_family`
     and `concept_pref_rank`, so the flat path filters on those directly.
     period_type is derived from the family (balance-sheet = instant).
  2. dimension hints -> `(segment_axis LIKE '%:Axis', segment_label LIKE
     '%member%')`. The view's `segment_label` is the human-readable member name,
     so dimension matching happens *inside* the fact query — this is year-correct
     (the row for the requested fiscal_year carries the right, possibly
     year-versioned, member QName) and avoids the dimension_catalog's
     year-versioned-URI mismatch. `"NONE"` => `segment_axis IS NULL` ("the
     absence of dimension is the meaning").
  3. A single 10-K reports current + prior comparative columns that all share the
     filing's `fiscal_year`; they differ by `period_end`. The flat path keeps only the
     year's own column (ending exactly on the DEI period end), as the latest
     later-labelled report restates it (S1, `restatement.py`,
     docs/specs/restatement-s1.md); an empty own column is NOT_FOUND, never an earlier
     column. Candidates are ordered `period_end DESC` and only the latest-period rows
     count toward ambiguity.
  4. Confidence scoring (§3.6); >1 distinct value at the latest period, or low
     confidence, => AMBIGUOUS.

Connection params come from the same `XBRL_DB_*` env vars as `EntityExtractor`.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import datetime
import decimal
import json
import math
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.logging_config import get_logger
from src.observability.trace_context import log_event
from src.retriever.xbrl.defined_measure import (
    DEFINED_FAMILIES,
    DefinedMeasure,
    defined_measure,
    defined_precedence,
)
from src.retriever.xbrl.defined_measure_rows import (
    defined_rules,
    measure_lines,
    read_filing_rows,
)
from src.retriever.xbrl.family_resolution import (
    ROLE_PRIMARY,
    ROLE_SUBSTITUTE,
    candidate_order,
    display_unit,
    family_parts,
    measure_column,
    row_role,
    same_measure,
)
from src.retriever.xbrl.period_disclosure import duration_class
from src.retriever.xbrl.q4_lookup import select_q4
from src.retriever.xbrl.reporting_currency import (
    answer_unit,
    choose_lookup_unit,
)
from src.retriever.xbrl.restatement import current_column, latest_dimensional_restatement

logger = get_logger(__name__)

# A fourth-quarter lookup (docs/specs/q4-lookup.md, Wiring 1).
Q4_INSTANT_NOTE = "Q4 ends on the fiscal year's end; the FY-end balance answers"
Q4_DIMENSIONAL_REASON = "a fourth quarter is derived only for a non-dimensional measure"


def _emit_sql_event(
    cur, sql: str, params: Any, n_rows: int, latency_ms: float, source: str
) -> None:
    """Emit a `sql_query` trace event with the fully-rendered SQL (so the Trace
    Explorer can show the exact XBRL DB queries). No-op without an active trace;
    never raises into the query path."""
    try:
        rendered = cur.mogrify(sql, params)
        rendered = (
            rendered.decode("utf-8", "replace")
            if isinstance(rendered, bytes)
            else str(rendered)
        )
    except Exception:
        rendered = sql
    log_event(
        "sql_query",
        source=source,
        sql=rendered.strip(),
        n_rows=n_rows,
        latency_ms=round(latency_ms, 1),
    )


# --- WS-1 / B5 read-time publish gate ---------------------------------------
# The ingest publish gate (src/ingest/sec_edgar/xbrl/promotion.py) marks a filing
# `is_published = FALSE` in publish_manifest when it fails GATE-A/GATE-B. Every
# factstore read excludes those filings' facts, keyed on the surface's
# (cik, fiscal_year, fiscal_period). A filing with NO manifest row is served
# (backward-compatible: the pre-existing corpus keeps working without a backfill).
# Held-back facts stay physically present — this is a read-time filter, not a delete.
_PUBLISH_MANIFEST_PRESENT: dict[str, bool] = {}


def _publish_manifest_present(conn) -> bool:
    """True iff the publish_manifest table exists in this DB. Cached once present
    (per dbname); re-probed cheaply while absent so a later migration is picked up.
    Never raises into the query path."""
    try:
        key = conn.get_dsn_parameters().get("dbname", "")
    except Exception:
        key = ""
    if _PUBLISH_MANIFEST_PRESENT.get(key):
        return True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.publish_manifest') IS NOT NULL")
            present = bool(cur.fetchone()[0])
    except Exception:
        present = False
    if present:
        _PUBLISH_MANIFEST_PRESENT[key] = True
    return present


# The concept-family-coverage columns (concept_role, substitution_code,
# substitution_note, and `presented` on mv_financial_facts) exist only after the
# maintainer's reload (docs/specs/concept-family-coverage.md, Notes "Reload"). Until
# then a lookup selects NULLs in their place, so the rows carry no role and the answer
# order falls back to the role each rank implies. Cached once present, like the
# publish manifest above.
_COVERAGE_COLUMNS_PRESENT: dict[tuple[str, str], bool] = {}
_COVERAGE_COLUMNS_WARNED: set[tuple[str, str]] = set()


def coverage_columns_present(conn, relation: str) -> bool:
    """Whether ``relation`` has the ``concept_role`` column. Never raises.

    Args:
        conn: An open connection.
        relation: ``mv_financial_facts`` or ``concept_mapping`` (trusted constants).

    Returns:
        True when the reload has added the coverage columns.
    """
    try:
        key = (conn.get_dsn_parameters().get("dbname", ""), relation)
    except Exception:
        key = ("", relation)
    if _COVERAGE_COLUMNS_PRESENT.get(key):
        return True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_attribute"
                " WHERE attrelid = to_regclass(%s) AND attname = 'concept_role'"
                " AND NOT attisdropped)",
                (f"public.{relation}",),
            )
            present = bool(cur.fetchone()[0])
    except Exception:
        present = False
    if present:
        _COVERAGE_COLUMNS_PRESENT[key] = True
    elif key not in _COVERAGE_COLUMNS_WARNED:
        _COVERAGE_COLUMNS_WARNED.add(key)
        logger.warning(
            "%s has no concept-family-coverage columns yet; run the reload "
            "(docs/specs/concept-family-coverage.md)", relation
        )
    return present


def publish_gate_clause(conn, surface_table: str) -> str:
    """Return a SQL fragment excluding facts from filings explicitly held back by
    the publish gate, correlated to `surface_table`'s (cik, fiscal_year,
    fiscal_period). Returns '' when publish_manifest is absent, so the gate is
    inert on un-migrated DBs. `surface_table` is a trusted constant (no user
    input), so f-string interpolation here is safe."""
    if not _publish_manifest_present(conn):
        return ""
    t = surface_table
    return (
        f" AND NOT EXISTS (SELECT 1 FROM publish_manifest pm"
        f" WHERE pm.cik = {t}.cik AND pm.fiscal_year = {t}.fiscal_year"
        f" AND pm.fiscal_period = {t}.fiscal_period AND pm.is_published = FALSE)"
    )


# Balance-sheet concept families are point-in-time ('instant'); everything else
# (income statement, cash flow, ratios) is a 'duration' over the fiscal period.
INSTANT_CONCEPT_FAMILIES = {
    "TotalAssets",
    "CurrentAssets",
    "CashAndEquivalents",
    "ShortTermInvestments",
    "AccountsReceivable",
    "Inventory",
    "PropertyPlantAndEquipment",
    "Goodwill",
    "IntangibleAssets",
    "TotalLiabilities",
    "CurrentLiabilities",
    "AccountsPayable",
    "DeferredRevenue",
    "Debt",
    "StockholdersEquity",
    "RetainedEarnings",
    "SharesOutstanding",
}

# Fuzzy axis hints (as emitted by the plan generator) -> the *local* axis name.
# mv_financial_facts.segment_axis is normalized to a prefix form
# (e.g. 'srt:ProductOrServiceAxis'), so we match with  segment_axis LIKE '%:Local'.
AXIS_HINT_TO_LOCAL: dict[str, str] = {
    "product": "ProductOrServiceAxis",
    "service": "ProductOrServiceAxis",
    "segment": "StatementBusinessSegmentsAxis",
    "business": "StatementBusinessSegmentsAxis",
    "geography": "StatementGeographicalAxis",
    "geographic": "StatementGeographicalAxis",
    "region": "StatementGeographicalAxis",
    "country": "StatementGeographicalAxis",
    "customer": "MajorCustomersAxis",
    "benchmark": "ConcentrationRiskByBenchmarkAxis",
}

# Member hints that are really the AXIS description, not a member. The plan generator
# emits these when asked to "break down by product or service segment" — it passes the
# breakdown dimension through as if it were the member. Widening the axis search for
# one of these can only produce a spurious match, so the retry declines them.
_AXIS_DESCRIPTION_HINTS = frozenset(
    {
        "product or service segment",
        "product or service",
        "business segment",
        "reportable segment",
        "reportable business segment",
        "operating segment",
        "segment",
        "product",
        "service",
        "geography",
        "geographic region",
        "region",
    }
)

DEFAULT_MIN_CONFIDENCE = 0.70
DEFAULT_CANDIDATE_LIMIT = 25


class LookupStatus(Enum):
    OK = "OK"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"
    UNRESOLVED_DIMENSION = "UNRESOLVED_DIMENSION"
    ERROR = "ERROR"


@dataclass
class Citation:
    fact_id: int | None = None
    accession_no: str | None = None
    html_anchor: str | None = None
    page_num: int | None = None
    section_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "accession_no": self.accession_no,
            "html_anchor": self.html_anchor,
            "page_num": self.page_num,
            "section_path": self.section_path,
        }


@dataclass
class FactCandidate:
    fact_id: int
    value: float | None
    unit: str | None
    period_type: str | None
    period_start: str | None
    period_end: str | None
    concept: str | None
    concept_family: str | None
    concept_pref_rank: int | None
    segment_axis: str | None
    segment_member: str | None
    segment_label: str | None
    section_path: str | None
    citation: Citation
    linked_chunk_ids: list[str] = field(default_factory=list)
    # Multidim rows only: {axis_uri: member_label}, needed to judge whether every
    # requested dimension matched EXACTLY (the flat path uses segment_label).
    dimension_labels: dict | None = None
    decimals: int | None = None
    # concept-family-coverage (docs/specs/concept-family-coverage.md): the concept's role
    # in the family for this filer, whether it is a line of a primary statement, and the
    # substitution an answer states when the role is `substitute`.
    concept_role: str | None = None
    presented: bool = False
    substitution_code: str | None = None
    substitution_note: str | None = None

    def order_row(self) -> dict:
        """The keys ``candidate_order`` reads."""
        return {
            "concept_role": self.concept_role,
            "concept_pref_rank": self.concept_pref_rank,
            "period_end": self.period_end,
            "presented": self.presented,
            "value": self.value,
            "decimals": self.decimals,
            "fact_id": self.fact_id,
        }

    def to_dict(self) -> dict:
        d = {
            k: getattr(self, k)
            for k in (
                "fact_id",
                "value",
                "unit",
                "period_type",
                "period_start",
                "period_end",
                "concept",
                "concept_family",
                "concept_pref_rank",
                "concept_role",
                "presented",
                "segment_axis",
                "segment_member",
                "segment_label",
                "section_path",
                "linked_chunk_ids",
            )
        }
        d["citation"] = self.citation.to_dict()
        return d


@dataclass
class FactLookupResult:
    step_id: str | None
    status: LookupStatus
    value: float | None = None
    unit: str | None = None
    citation: Citation | None = None
    confidence: float = 0.0
    candidates: list[FactCandidate] = field(default_factory=list)
    linked_chunk_ids: list[str] = field(default_factory=list)
    resolution_notes: list[str] = field(default_factory=list)
    error: str | None = None
    # Set when the answer is a stated substitute: {"code", "note", "concept"}
    # (docs/specs/concept-family-coverage.md R3).
    substitution: dict | None = None
    # Set when the answer is a stated sum of cells: the summed facts' ids, the first of
    # them the citation (docs/specs/segment-lookup-screens.md, Requirement 1(b)).
    sum_of: list[int] | None = None
    # Debt and DeferredRevenue (docs/specs/s4-lookup-deferred-revenue-debt.md): the S4
    # definition the answer follows, {"concept", "status", "basis", "disclosure",
    # "readings", "parts"}; the synthesizer states its disclosure with the number.
    definition: dict | None = None
    # A fourth-quarter duration (docs/specs/q4-lookup.md): {"status" ("filed" or
    # "derived"), "basis", "fact_ids", "accessions", "period_start", "period_end"
    # (stated), "days", "note"}. A derived Q4 cites its year row and carries the nine
    # months' fact id here; the synthesizer states the note with the number.
    q4: dict | None = None

    @property
    def is_ok(self) -> bool:
        return self.status == LookupStatus.OK

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "step_id": self.step_id,
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "citation": self.citation.to_dict() if self.citation else None,
            "confidence": round(self.confidence, 3),
            "n_candidates": len(self.candidates),
            "linked_chunk_ids": self.linked_chunk_ids,
            "resolution_notes": self.resolution_notes,
            "error": self.error,
            "substitution": self.substitution,
        }
        if self.sum_of is not None:
            d["sum_of"] = list(self.sum_of)
        if self.definition is not None:
            d["definition"] = self.definition
        if self.q4 is not None:
            d["q4"] = self.q4
        return d


@dataclass
class _DimSpec:
    """One parsed dimension request from a plan step's `dimensions` object."""

    axis_key: str
    local_axis: str | None  # local axis name, e.g. 'ProductOrServiceAxis'
    member_hint: str  # normalized lowercase member text
    member_qname: str | None  # set when the plan supplied a final member QName


class XbrlFactstoreLookup:
    """Resolves a single `lookup` plan step to one financial fact.

    Example:
        >>> lookup = XbrlFactstoreLookup.from_env()
        >>> step = {"id": "dc26", "op": "lookup", "cik": "0001045810",
        ...         "ticker": "NVDA", "fy": 2026, "concept_family": "Revenue",
        ...         "dimensions": {"product": "data center"},
        ...         "section_hint": "Disaggregated Revenue"}
        >>> result = lookup.lookup(step)
        >>> result.value, result.confidence
    """

    def __init__(
        self,
        db_conn_params: dict | None = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ):
        self._db_conn_params = db_conn_params or {}
        self.min_confidence = min_confidence
        self.candidate_limit = candidate_limit

    @classmethod
    def from_env(
        cls, dotenv_path: str | None = None, **kwargs
    ) -> XbrlFactstoreLookup:
        load_dotenv(dotenv_path)
        from src.db.conn import conn_params

        return cls(db_conn_params=conn_params(), **kwargs)

    def _connect(self):
        return psycopg2.connect(**self._db_conn_params)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, step: dict) -> FactLookupResult:
        """Resolve one `lookup` step to a `FactLookupResult`."""
        step_id = step.get("id")
        if step.get("op") != "lookup":
            return FactLookupResult(
                step_id,
                LookupStatus.ERROR,
                error=f"not a lookup step: op={step.get('op')!r}",
            )

        cik = step.get("cik")
        fy = step.get("fy")
        concept_family = step.get("concept_family")
        if not cik or fy is None or not concept_family:
            return FactLookupResult(
                step_id,
                LookupStatus.ERROR,
                error=f"lookup step missing required field(s) cik/fy/concept_family: {step}",
            )

        period_type = step.get("period_type") or self._derive_period_type(
            concept_family
        )
        # Period selection (10-Q support). Default 'FY' preserves the annual
        # behavior that predates quarterly ingest. `ytd` picks the cumulative
        # year-to-date figure for Q2/Q3 instead of the discrete quarter.
        fiscal_period = (step.get("fiscal_period") or "FY").upper()
        ytd = bool(step.get("ytd", False))
        # A Q4 duration has no stored row labelled Q4: it is read by `_lookup_q4` below,
        # never through a duration_kind pin (docs/specs/q4-lookup.md, Wiring 2).
        duration_kind = (
            self._derive_duration_kind(fiscal_period, ytd)
            if period_type == "duration" and fiscal_period != "Q4"
            else None
        )
        # Point-in-time selection. Default (neither set) = the year's own column as the
        # latest periodic report states it (S1, docs/specs/restatement-s1.md R3). `as_of`
        # (ISO date) = the value as it stood in the latest filing available on that date;
        # `as_originally_reported` = the own column of the original (non-amendment) filing,
        # ignoring later restatements.
        as_of = step.get("as_of")
        as_originally_reported = bool(step.get("as_originally_reported", False))
        # The flat path selects by filing itself unless `as_of` pins an accession.
        flat_pit: str | None = None
        notes: list[str] = []

        # (shown unit, period end) of every row the flat query read for the measure
        # (docs/specs/lookup-reporting-currency.md R2); None: decide over the candidates.
        observations: list[tuple[str | None, str | None]] | None = None

        dim_specs, dim_error = self._parse_dimensions(
            step.get("dimensions", "NONE"), notes
        )
        if dim_error is not None:
            return FactLookupResult(
                step_id,
                dim_error,
                resolution_notes=notes,
                error="; ".join(notes) or "dimension parse failed",
            )

        if fiscal_period == "Q4":
            # docs/specs/q4-lookup.md, Wiring 1. An instant at the end of Q4 is the
            # FY-end balance (every FY rule applies); a duration is the filed fourth
            # quarter or FY minus the nine months, for a non-dimensional measure only.
            if period_type == "instant":
                result = self.lookup({**step, "fiscal_period": "FY"})
                result.resolution_notes.append(Q4_INSTANT_NOTE)
                return result
            if dim_specs:
                notes.append(Q4_DIMENSIONAL_REASON)
                return FactLookupResult(
                    step_id, LookupStatus.NOT_FOUND, resolution_notes=notes,
                    error=Q4_DIMENSIONAL_REASON,
                )
            return self._lookup_q4(
                step, cik, fy, concept_family, as_of, as_originally_reported, notes
            )

        try:
            with self._connect() as conn:
                pit_accession = None
                if as_of or as_originally_reported:
                    pit_accession = self._resolve_pit_accession(
                        conn,
                        cik,
                        fy,
                        fiscal_period,
                        as_of,
                        as_originally_reported,
                        notes,
                    )
                    if pit_accession is None:
                        return FactLookupResult(
                            step_id,
                            LookupStatus.NOT_FOUND,
                            resolution_notes=notes,
                            error="no filing matches the requested point-in-time",
                        )
                    if as_of:
                        flat_pit = pit_accession
                if len(dim_specs) >= 2:
                    rows = self._query_multidim(
                        conn,
                        cik,
                        fy,
                        concept_family,
                        period_type,
                        fiscal_period,
                        duration_kind,
                        dim_specs,
                        flat_pit,
                        s1=as_of is None,
                        original=as_originally_reported,
                    )
                else:
                    # A cell (a fact with a second, non-neutral dimension) the flat view
                    # files under the requested member is never that member's own fact:
                    # set it aside and keep searching (segment-lookup-screens R1).
                    cells: list[dict] = []
                    read: list[tuple[str | None, str | None]] = []
                    rows = self._set_aside_cells(
                        self._query_flat(
                            conn,
                            cik,
                            fy,
                            concept_family,
                            period_type,
                            fiscal_period,
                            duration_kind,
                            dim_specs,
                            flat_pit,
                            original=as_originally_reported,
                            observations=read,
                        ),
                        cells,
                    )
                    # The rows this query read decide the reporting currency; rows a
                    # retry below finds fall back to their own candidates.
                    if rows:
                        observations = read
                    if not rows:
                        rows = self._retry_across_axes(
                            conn,
                            cik,
                            fy,
                            concept_family,
                            period_type,
                            fiscal_period,
                            duration_kind,
                            dim_specs,
                            flat_pit,
                            notes,
                            cells=cells,
                            original=as_originally_reported,
                        )
                    if not rows and len(dim_specs) == 1:
                        rows = self._retry_with_neutral_dimension(
                            conn,
                            cik,
                            fy,
                            concept_family,
                            period_type,
                            fiscal_period,
                            duration_kind,
                            dim_specs[0],
                            flat_pit,
                            notes,
                            s1=as_of is None,
                            original=as_originally_reported,
                        )
                    if not rows and len(dim_specs) == 1 and not dim_specs[0].member_qname:
                        summed = self._sum_of_cells(
                            conn,
                            step,
                            cik,
                            fy,
                            concept_family,
                            period_type,
                            fiscal_period,
                            duration_kind,
                            dim_specs[0],
                            pit_accession,
                            notes,
                        )
                        if summed is not None:
                            return summed
                    if not rows and cells:
                        seen = {str(c.get("fact_id")): c for c in cells}
                        notes.append(
                            f"member {dim_specs[0].member_hint!r} found only in cells with "
                            "another dimension, never the member's own total: "
                            + ", ".join(
                                f"fact {fid} ({c.get('segment_label')})"
                                for fid, c in seen.items()
                            )
                        )
                catalog_labels = None
                if rows and len(dim_specs) == 1 and not dim_specs[0].member_qname:
                    catalog_labels = self._catalog_labels(conn, cik, rows)
                # A family that is the sum of others, with no fact of its own: fetch
                # the parts, so the refusal can name them (D45).
                part_rows: dict[str, list[dict]] = {}
                if not rows and not dim_specs:
                    for part in family_parts(concept_family):
                        part_rows[part] = self._query_flat(
                            conn, cik, fy, part, period_type, fiscal_period,
                            duration_kind, dim_specs, flat_pit,
                            original=as_originally_reported,
                        )
        except Exception as e:  # pragma: no cover - exercised only with a broken DB
            logger.error(f"factstore lookup failed for step {step_id!r}: {e}")
            return FactLookupResult(step_id, LookupStatus.ERROR, error=str(e))

        result = self._build_result(
            step_id, step, cik, fy, concept_family, period_type, dim_specs, rows, notes,
            catalog_labels=catalog_labels, observations=observations,
        )
        if part_rows and result.status == LookupStatus.NOT_FOUND:
            self._name_parts(result, step, cik, fy, concept_family, period_type, part_rows)
        if not dim_specs and concept_family in DEFINED_FAMILIES:
            result = self._apply_defined_measure(result, step, cik, concept_family)
        return result

    def _lookup_q4(
        self,
        step: dict,
        cik: str,
        fy: int,
        concept_family: str,
        as_of: str | None,
        original: bool,
        notes: list[str],
    ) -> FactLookupResult:
        """Answers a non-dimensional duration lookup of the fourth quarter.

        docs/specs/q4-lookup.md, Wiring 1: the measure's stored rows are read as the flat
        S1 query reads them (every filing and period, no ``duration_kind`` pin), ``as_of``
        keeps the rows filed on or before it, the unit is decided as for any lookup, and
        ``select_q4`` answers the filed fourth quarter or FY minus the nine months.
        ``ytd`` is ignored (a Q4 year-to-date is the year).

        Args:
            step: The lookup step (``fiscal_period`` ``Q4``).
            cik: The filer.
            fy: The fiscal-year label.
            concept_family: The measure's family (a duration).
            as_of: Keep only rows filed on or before this ISO date, or None.
            original: Answer as originally reported.
            notes: The resolution notes so far; extended.

        Returns:
            ``OK`` with the value, its shown unit, the citation of the first fact the
            answer rests on and the ``q4`` record; ``NOT_FOUND`` with the reason.
        """
        step_id = step.get("id")
        try:
            with self._connect() as conn:
                rows = self._query_flat(
                    conn, cik, fy, concept_family, "duration", "FY", None, [],
                    original=original, s1_select=False,
                )
        except Exception as e:  # pragma: no cover - exercised only with a broken DB
            logger.error(f"factstore Q4 lookup failed for step {step_id!r}: {e}")
            return FactLookupResult(step_id, LookupStatus.ERROR, error=str(e))
        if as_of:
            cutoff = datetime.date.fromisoformat(str(as_of)[:10])
            rows = [
                r for r in rows
                if r.get("filed_date") is not None
                and datetime.date.fromisoformat(str(r["filed_date"])[:10]) <= cutoff
            ]
            notes.append(f"point-in-time (as_of={as_of}): rows filed on or before it")

        unit = self._q4_unit(rows, fy, step.get("unit"), original)
        out = select_q4(rows, fy, unit=unit, as_originally_reported=original)
        log_event(
            "decision", decision="q4_lookup", step_id=step_id,
            concept_family=concept_family, status=out["status"], basis=out["basis"],
            fact_ids=list(out["fact_ids"] or []), unit=unit, reason=out["reason"],
        )
        if out["status"] == "refused":
            notes.append(f"Q4 not answered: {out['reason']}")
            return FactLookupResult(
                step_id, LookupStatus.NOT_FOUND, resolution_notes=notes, error=out["reason"]
            )
        by_id = {r.get("fact_id"): r for r in rows}
        candidates = [
            self._row_to_candidate(by_id[fid]) for fid in out["fact_ids"] if fid in by_id
        ]
        notes.append(out["note"])
        cited = candidates[0]
        return FactLookupResult(
            step_id=step_id,
            status=LookupStatus.OK,
            value=float(out["value"]),
            unit=out["unit"],
            citation=cited.citation,
            confidence=1.0,
            candidates=candidates,
            linked_chunk_ids=cited.linked_chunk_ids,
            resolution_notes=notes,
            q4={
                "status": out["status"],
                "basis": out["basis"],
                "fact_ids": list(out["fact_ids"]),
                "accessions": list(out["accessions"]),
                "period_start": out["period_start"],
                "period_end": out["period_end"],
                "days": out["days"],
                "note": out["note"],
            },
        )

    @staticmethod
    def _q4_unit(
        rows: Sequence[dict], fy: int, requested: str | None, original: bool
    ) -> str | None:
        """The unit a Q4 lookup answers in, decided as a flat lookup decides it.

        Args:
            rows: The measure's stored rows (every filing and period).
            fy: The fiscal-year label.
            requested: The step's ``unit``, or None.
            original: Answer as originally reported.

        Returns:
            The requested unit in the shown form of the year's units (a currency code
            names its per-share unit when only that is filed); else ``answer_unit`` over
            the year's own annual candidates and every row read; None when no single
            unit qualifies (``select_q4`` then refuses when the year carries several).
        """
        flat = [r for r in rows if r.get("segment_axis") is None]
        own = [
            r for r in current_column(flat, fy, "FY", as_originally_reported=original)
            if r.get("period_start") is not None
            and duration_class(r["period_start"], r["period_end"]) == "annual"
        ]
        keys = [display_unit(r.get("unit")) if r.get("unit") else None for r in own]
        if requested is not None:
            return _requested_unit_key(requested, {k for k in keys if k})
        return answer_unit(keys, [_unit_observation(r) for r in flat])

    def _apply_defined_measure(
        self, result: FactLookupResult, step: dict, cik: str, concept_family: str
    ) -> FactLookupResult:
        """Answers a flat Debt or DeferredRevenue lookup by its S4 definition.

        docs/specs/s4-lookup-deferred-revenue-debt.md, requirement 10 and Wiring: the
        filing and column are the ones S1 chose for the candidates (the first row
        ``candidate_order`` keeps); the measure is built from that filing's balance-sheet
        lines and note facts at that date. ``defined`` answers the measure, citing every
        part; ``candidate_order`` keeps today's answer with the disclosure; ``refuse`` is
        NOT_FOUND with the reason.

        Args:
            result: The flat lookup's result (today's answer).
            step: The lookup step.
            cik: The filer.
            concept_family: ``Debt`` or ``DeferredRevenue``.

        Returns:
            The result to return.
        """
        # The measure is built in the unit the lookup chose (`_restrict_to_one_unit`: the
        # requested unit, else the reporting currency; lookup-reporting-currency). When
        # that choice failed, the candidates still carry several units, or none in the
        # requested one: there is no unit to build in, so the refusal stands.
        units = {u for c in result.candidates if c.unit and (u := display_unit(c.unit))}
        requested = step.get("unit")
        if len(units) > 1 or (
            requested is not None and _requested_unit_key(requested, units) not in units
        ):
            return result
        column = self._defined_column(result)
        if column is None:
            return result
        accession_no, period_end, unit = column
        try:
            with self._connect() as conn:
                filings = read_filing_rows(
                    conn, self._fetch, [(accession_no, period_end, cik)], unit
                )
        except Exception as e:  # pragma: no cover - exercised only with a broken DB
            logger.error(f"defined measure read failed for step {result.step_id!r}: {e}")
            return result
        filing = filings.get((accession_no, period_end))
        if filing is None:
            return result
        rule = defined_rules()[concept_family]
        measure = defined_measure(
            concept_family, measure_lines(filing, rule), filing.notes, rule, unit
        )
        if measure is None:
            return result
        precedence = defined_precedence(measure)
        log_event(
            "decision", decision="s4_defined_measure", step_id=result.step_id,
            concept_family=concept_family, status=measure["status"],
            basis=measure["basis"], fact_ids=measure["fact_ids"], precedence=precedence,
            accession_no=accession_no, period_end=str(period_end),
            balance_sheet_role=filing.role,
        )
        definition = _definition_record(measure)
        notes = [*result.resolution_notes, measure["disclosure"]]
        if precedence == "candidate_order":
            return dataclasses.replace(result, resolution_notes=notes, definition=definition)
        if precedence == "refuse":
            return dataclasses.replace(
                result, status=LookupStatus.NOT_FOUND, value=None, citation=None,
                confidence=0.0, resolution_notes=notes, error=measure["reason"],
                substitution=None, definition=definition,
            )
        first = filing.facts.get(measure["fact_ids"][0], {})
        citation = Citation(
            fact_id=measure["fact_ids"][0],
            accession_no=accession_no,
            html_anchor=first.get("html_anchor"),
            page_num=first.get("page_num"),
            section_path=first.get("section_path"),
        )
        return dataclasses.replace(
            result, status=LookupStatus.OK, value=measure["value"],
            unit=display_unit(unit), citation=citation, confidence=1.0,
            resolution_notes=notes, error=None, substitution=None,
            sum_of=list(measure["fact_ids"]), definition=definition,
        )

    @staticmethod
    def _defined_column(result: FactLookupResult) -> tuple[str, Any, str] | None:
        """The filing, stored period end and unit S1 chose: ``measure_column``'s candidate.

        The screen names its picks' column by the same rule
        (docs/specs/s4-screen-defined-members.md R1).

        Returns:
            ``(accession_no, period_end as a date, unit)``, or None without a candidate.
        """
        candidates = result.candidates
        column = measure_column([c.order_row() for c in candidates])
        if column is None:
            return None
        best = candidates[column]
        if best.citation.accession_no is None or best.period_end is None or not best.unit:
            return None
        period_end = datetime.date.fromisoformat(str(best.period_end)[:10])
        return best.citation.accession_no, period_end, best.unit

    def _name_parts(
        self,
        result: FactLookupResult,
        step: dict,
        cik: str,
        fy: int,
        concept_family: str,
        period_type: str,
        part_rows: dict[str, list[dict]],
    ) -> None:
        """Name a family's parts in its refusal when every part answers on its own.

        The lookup never adds the parts (D45, ifrs-family-meaning U1): the result stays
        ``NOT_FOUND``, and its note and error give each part's value, unit and fact id,
        so a planner can add them through the audited compute step.

        Args:
            result: The family's ``NOT_FOUND`` result; its notes and error are extended.
            step: The lookup step.
            cik: The filer's CIK.
            fy: The fiscal year.
            concept_family: The looked-up family.
            period_type: The family's period type (the parts share it).
            part_rows: Part family -> its candidate rows.
        """
        found: list[str] = []
        for part, rows in part_rows.items():
            part_step = {**step, "concept_family": part}
            r = self._build_result(
                result.step_id, part_step, cik, fy, part, period_type, [], rows, [],
            )
            if r.status != LookupStatus.OK or r.citation is None or r.value is None:
                return  # a part is missing: nothing to name
            shown = f"{r.value:,.0f}" if float(r.value).is_integer() else f"{r.value:,}"
            found.append(f"{part} {shown} {r.unit} (fact_id {r.citation.fact_id})")
        note = (
            f"{concept_family} is not reported as a total for cik={cik}, fy={fy}; its "
            f"parts are reported separately: {', '.join(found)}. Add them in a compute "
            f"step for the total."
        )
        result.resolution_notes.append(note)
        result.error = note

    # ------------------------------------------------------------------
    # Result assembly + confidence
    # ------------------------------------------------------------------

    @staticmethod
    def _restrict_to_one_unit(
        candidates: list[FactCandidate],
        requested_unit: str | None,
        notes: list[str],
        observations: Sequence[tuple[str | None, str | None]] | None = None,
    ) -> tuple[list[FactCandidate], LookupStatus | None]:
        """Keep the candidates in the single unit the lookup resolves in.

        Values in different units are never compared: a 20-F tags revenue in its
        reporting currency and again as a convenience translation, and judging those as
        two answers made every such lookup AMBIGUOUS. Returns the kept candidates and a
        status to stop with (None to continue).

        Units are compared in their shown form (``display_unit``), so ``shares*USD``,
        ``USD*shares`` and ``USD/shares`` are one unit, and a requested currency code
        also matches a per-share unit in that currency (ifrs-family-meaning R5).

        With no requested unit the answer is in ``answer_unit``: the filer's reporting
        currency decided over *observations*, the ``(shown unit, period end)`` of every
        row of the measure the query read, before the S1 column selection
        (docs/specs/lookup-reporting-currency.md). Without them (paths that are not the
        flat query) the candidates' own units and periods decide, as before.
        """
        keys = [display_unit(c.unit) if c.unit else None for c in candidates]
        units = {k for k in keys if k}
        if not units or (len(units) == 1 and requested_unit is None):
            return candidates, None  # one unit (or unitless): nothing to choose
        own = [(k, c.period_end) for k, c in zip(keys, candidates, strict=True)]
        if requested_unit is not None:
            chosen = choose_lookup_unit(own, _requested_unit_key(requested_unit, units))
        else:
            chosen = answer_unit(keys, own if observations is None else observations)
        if chosen is None:
            if requested_unit is not None:
                notes.append(
                    f"no fact in the requested unit {requested_unit} "
                    f"(available: {sorted(units)})"
                )
                return candidates, LookupStatus.NOT_FOUND
            notes.append(f"several units at the latest period and no reporting currency "
                         f"among them: {sorted(units)}")
            return candidates, LookupStatus.AMBIGUOUS
        dropped = sorted(units - {chosen})
        if dropped:
            reason = "requested" if requested_unit is not None else "reporting currency"
            notes.append(f"unit {chosen} ({reason}); set aside {dropped}")
        return [c for k, c in zip(keys, candidates, strict=True) if k == chosen], None

    def _build_result(
        self,
        step_id,
        step,
        cik,
        fy,
        concept_family,
        period_type,
        dim_specs,
        rows,
        notes,
        catalog_labels=None,
        observations: Sequence[tuple[str | None, str | None]] | None = None,
    ):
        candidates = [self._row_to_candidate(r) for r in rows]
        if not candidates:
            notes.append(
                f"no fact for cik={cik}, fy={fy}, concept_family={concept_family}, "
                f"period_type={period_type}, dimensions={step.get('dimensions')!r}"
            )
            status = (
                LookupStatus.UNRESOLVED_DIMENSION
                if dim_specs
                else LookupStatus.NOT_FOUND
            )
            return FactLookupResult(
                step_id, status, candidates=[], resolution_notes=notes
            )

        # A hint is matched by containment, so "license support" also finds "Cloud
        # Services And License Support". When a member names the hint exactly, that is
        # the member asked for: keep only it (docs/specs/segment-label-match.md).
        if len(dim_specs) == 1 and not dim_specs[0].member_qname:
            want = member_label_key(dim_specs[0].member_hint)
            exact = [
                c for c in candidates
                if c.segment_label and member_label_key(c.segment_label) == want
            ]
            if exact and len(exact) < len(candidates):
                aside = sorted(
                    {c.segment_label for c in candidates if c.segment_label} - {
                        c.segment_label for c in exact
                    }
                )
                notes.append(f"member {want!r} matched exactly; set aside {aside}")
                candidates = exact

            # The filer reports a member by exactly this name, but not here: a fact
            # found only by containment is another member (e.g. a combined segment)
            # standing in for it (docs/specs/identity-and-stand-in.md).
            matched = sorted(_candidate_member_labels(candidates))
            if catalog_labels is not None and refuse_stand_in(
                dim_specs[0].member_hint, matched, catalog_labels
            ):
                notes.append(
                    f"member {dim_specs[0].member_hint!r} is reported by this filer but "
                    f"has no fact here; set aside stand-in members {matched}"
                )
                return FactLookupResult(
                    step_id, LookupStatus.UNRESOLVED_DIMENSION, candidates=[],
                    resolution_notes=notes,
                )

        candidates, unit_status = self._restrict_to_one_unit(
            candidates, step.get("unit"), notes, observations
        )
        if unit_status is not None:
            return FactLookupResult(
                step_id, unit_status, candidates=candidates, resolution_notes=notes,
                error=notes[-1],
            )

        # The answer is the first row `candidate_order` keeps: a primary before a stated
        # substitute, the latest period, a line presented on a primary statement, then
        # rank and precision; component rows never answer but stay candidates for the
        # checks below (docs/specs/concept-family-coverage.md R2, R3). Only the rows at
        # the answer's period (the current-FY column) count toward ambiguity; earlier
        # rows are prior comparatives reported in the same filing.
        order = candidate_order([c.order_row() for c in candidates])
        if not order:
            notes.append(
                f"only component concepts of {concept_family} for cik={cik}, fy={fy}; "
                "a component never answers for its family"
            )
            status = (
                LookupStatus.UNRESOLVED_DIMENSION if dim_specs else LookupStatus.NOT_FOUND
            )
            return FactLookupResult(
                step_id, status, candidates=candidates, resolution_notes=notes
            )
        # The answer period is the first row's, nil rows included: a filed nil is the
        # filer's own column for that period, so an earlier column never stands in for
        # it. Only a valued row answers; a period holding only nils is NOT_FOUND
        # (docs/specs/sign-and-nil.md R1).
        answer_period = candidates[order[0]].period_end
        valued = [
            i for i in order
            if candidates[i].period_end == answer_period and candidates[i].value is not None
        ]
        if not valued:
            nil_ids = [
                str(candidates[i].citation.fact_id) for i in order
                if candidates[i].period_end == answer_period
            ]
            notes.append(
                f"only nil facts (xsi:nil, nothing reported) for {concept_family} at "
                f"period {answer_period}: fact_ids {', '.join(nil_ids)}"
            )
            return FactLookupResult(
                step_id, LookupStatus.NOT_FOUND, candidates=candidates,
                resolution_notes=notes,
            )
        best = candidates[valued[0]]
        current = [c for c in candidates if c.period_end == best.period_end]
        # A number tagged again at lower precision ("$20.8 billion" next to 20,832
        # million) is one fact, not a rival value: when every fact of the winning
        # concept is the most precise one rounded to its own decimals, answer with that
        # fact and count them all as its value (docs/specs/rounded-duplicates.md).
        mine = [
            candidates[i] for i in order
            if candidates[i].period_end == best.period_end
            and candidates[i].concept == best.concept and candidates[i].value is not None
        ]
        win = most_precise_consistent([(c.value, c.decimals) for c in mine])
        if win is not None:
            best = mine[win]
            current = [
                dataclasses.replace(c, value=best.value)
                if c.concept == best.concept and c.value is not None else c
                for c in current
            ]

        confidence = self._score_confidence(
            best, current, dim_specs, step.get("section_hint"), notes, concept_family,
            cik=cik,
        )

        # Ambiguity is judged WITHIN the winning concept, not across the whole
        # concept_family expansion. A family expands to several QNames that are
        # genuinely different facts and legitimately carry different values —
        # AAPL FY2024 "commercial paper" + "Level 2" matches 14 rows spanning
        # AvailableForSaleDebtSecuritiesAmortizedCostBasis (1,066,000,000),
        # MarketableSecuritiesCurrent (1,038,000,000) and
        # CashAndCashEquivalentsAtCarryingValue (28,000,000). Those are not competing
        # answers to one question; concept_mapping.preference_rank already says which
        # QName the family means, and ordering puts it first. Comparing across them
        # flagged AMBIGUOUS on a correctly-resolved fact, and an AMBIGUOUS step is
        # excluded from the audited values a caller may use.
        same_concept = [c for c in current if c.concept == best.concept]
        distinct_values = {
            round(c.value, 6) for c in same_concept if c.value is not None
        }
        if len(distinct_values) > 1:
            notes.append(
                f"{len(distinct_values)} distinct values for concept {best.concept!r} "
                f"at latest period {best.period_end}: {sorted(distinct_values)}"
            )
            status = LookupStatus.AMBIGUOUS
        elif confidence < self.min_confidence:
            notes.append(
                f"confidence {confidence:.2f} < threshold {self.min_confidence:.2f}"
            )
            status = LookupStatus.AMBIGUOUS
        else:
            status = LookupStatus.OK

        substitution = None
        if _role_of(best) == ROLE_SUBSTITUTE:
            substitution = {
                "code": best.substitution_code,
                "note": best.substitution_note,
                "concept": best.concept,
            }
            notes.append(f"stated substitute {best.concept!r}: {best.substitution_note}")

        return FactLookupResult(
            step_id=step_id,
            status=status,
            value=best.value,
            unit=display_unit(best.unit),
            citation=best.citation,
            confidence=confidence,
            candidates=candidates,
            linked_chunk_ids=best.linked_chunk_ids,
            resolution_notes=notes,
            substitution=substitution,
        )

    def _score_confidence(
        self, best, current, dim_specs, section_hint, notes, concept_family: str | None = None,
        cik: str | None = None,
    ) -> float:
        """Resolution confidence per design §3.6 (the cross-check signal is left
        to the executor's compute stage, so the max here is 0.9)."""
        confidence = 0.0

        # One value within the winning concept, not across the whole family: a family
        # holds different measures (NetIncomeLoss vs ProfitLoss), and since
        # concept-rank-integrity rank 1 says which one the family means
        # (docs/specs/within-concept-confidence.md).
        if one_value_credit(
            best.concept or "",
            best.concept_pref_rank,
            [(c.concept or "", c.concept_pref_rank, c.value) for c in current],
            components_block_answer(
                concept_family or "", best.concept or "", _role_of(best), bool(dim_specs)
            ),
        ):
            confidence += 0.4  # exactly one value for the winning concept, no rival
        if rank_credit(
            concept_family or "", best.concept or "", best.concept_pref_rank
        ) or member_credit(concept_family or "", cik, best) or order_credit(
            best.order_row(), [c.order_row() for c in current]
        ):
            # The filer's preferred QName, one that is the same measure
            # (docs/specs/revenue-equivalent-concepts.md, concept-family-coverage.md), a
            # decided stated substitute, or the presented line that candidate order puts
            # ahead of a better-ranked notes-only fact; not a fallback.
            confidence += 0.2

        if not dim_specs:
            confidence += 0.2  # no dimension => no fuzzy-match risk
        elif self._dimension_match_exact(best, dim_specs):
            confidence += 0.2  # member matched an exact label

        if (
            section_hint
            and best.section_path
            and self._section_consistent(section_hint, best.section_path)
        ):
            confidence += 0.1

        notes.append(
            f"confidence={confidence:.2f} (pref_rank={best.concept_pref_rank}, n_latest={len(current)})"
        )
        return confidence

    @staticmethod
    def _dimension_match_exact(best: FactCandidate, dim_specs: list[_DimSpec]) -> bool:
        # Multidim path: every requested dimension must equal some member label
        # exactly. Without this branch a 2-dimension lookup could never earn the
        # +0.2 dimension credit, capping its confidence at 0.4-0.6 against a 0.70
        # threshold — a correctly-resolved multi-axis fact was reported AMBIGUOUS by
        # construction, and an AMBIGUOUS step's value is not usable downstream.
        if best.dimension_labels or len(dim_specs) > 1:
            # Every hint contained in a different member label, and the fact has no
            # dimension the lookup did not ask about except a neutral consolidation member.
            # Subsumes exact equality for a fully specified fact; an under-specified match
            # earns nothing (docs/specs/multidim-full-match.md, multidim-recovery.md).
            return dimensions_match_ignoring_neutral(
                best.dimension_labels, [spec.member_hint for spec in dim_specs]
            )
        # Flat path resolves a single dimension into segment_label; treat it as
        # exact when the normalized label equals the requested member hint.
        if len(dim_specs) != 1:
            return False
        if not best.segment_label:
            return False
        return member_label_key(best.segment_label) == member_label_key(dim_specs[0].member_hint)

    # ------------------------------------------------------------------
    # Concept / period helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_period_type(concept_family: str) -> str:
        return "instant" if concept_family in INSTANT_CONCEPT_FAMILIES else "duration"

    def _resolve_pit_accession(
        self, conn, cik, fy, fiscal_period, as_of, original, notes: list[str]
    ) -> str | None:
        """Resolve which filing version a point-in-time lookup should read, from
        filing_registry. `original` -> the earliest-filed non-amendment filing
        (as originally reported). `as_of` -> the latest filing filed on/before that
        date (the value as it stood then, honoring restatements up to that date).
        Returns the accession_no, or None if nothing matches."""
        clauses = ["cik = %(cik)s", "fiscal_year = %(fy)s", "fiscal_period = %(fp)s"]
        params: dict[str, Any] = {"cik": cik, "fy": fy, "fp": fiscal_period}
        if as_of:
            clauses.append("filed_date <= %(as_of)s")
            params["as_of"] = as_of
        if original:
            clauses.append("is_amendment = FALSE")
        order = "filed_date ASC" if original else "filed_date DESC"
        sql = (
            f"SELECT accession_no FROM filing_registry "
            f"WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT 1"
        )
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        acc = row[0] if row else None
        mode = "as_originally_reported" if original else f"as_of={as_of}"
        notes.append(f"point-in-time ({mode}) -> accession {acc}")
        return acc

    @staticmethod
    def _derive_duration_kind(fiscal_period: str, ytd: bool) -> str | None:
        """Map a requested fiscal period to the mv_financial_facts.duration_kind
        that identifies the intended income/cash-flow duration. A 10-Q reports a
        discrete-quarter (~3mo) AND a year-to-date cumulative (~6/9mo) figure at
        the same period_end, so this disambiguates them. 'FY' -> annual keeps the
        pre-10-Q behavior. Returns None when the period is unknown (no filter)."""
        fp = (fiscal_period or "FY").upper()
        if fp == "FY":
            return "annual"
        if fp == "Q1":
            return "quarter"  # Q1 YTD == the quarter itself
        if fp == "Q2":
            return "half" if ytd else "quarter"
        if fp == "Q3":
            return "nine_month" if ytd else "quarter"
        if fp == "Q4":
            return "quarter"
        return None

    # ------------------------------------------------------------------
    # Dimension parsing (pure)
    # ------------------------------------------------------------------

    def _parse_dimensions(
        self, dimensions: Any, notes: list[str]
    ) -> tuple[list[_DimSpec], LookupStatus | None]:
        if dimensions == "NONE" or dimensions is None:
            return [], None
        if not isinstance(dimensions, dict) or not dimensions:
            notes.append(f"invalid 'dimensions' value: {dimensions!r}")
            return [], LookupStatus.UNRESOLVED_DIMENSION

        specs: list[_DimSpec] = []
        for axis_key, member in dimensions.items():
            local_axis = self._local_axis(axis_key)
            member_str = str(member).strip()
            member_qname = member_str if ":" in member_str else None
            specs.append(
                _DimSpec(
                    axis_key=axis_key,
                    local_axis=local_axis,
                    member_hint=_norm_label(member_str),
                    member_qname=member_qname,
                )
            )
        return specs, None

    @staticmethod
    def _local_axis(axis_key: str) -> str | None:
        """Local axis name for a plan axis key (a fuzzy hint or an already-a-qname)."""
        if ":" in axis_key:
            return axis_key.split(":")[-1]
        return AXIS_HINT_TO_LOCAL.get(axis_key.strip().lower())

    # ------------------------------------------------------------------
    # Concept expansion (multidim path only)
    # ------------------------------------------------------------------

    def _expand_concept_family(
        self, conn, cik: str, concept_family: str
    ) -> tuple[list[str], list[int], list[str | None], list[str | None], list[str | None]]:
        """concept_family -> candidate concept QNames + their curated preference ranks.

        (mv_financial_facts_multidim has no concept_family column.) The ranks are
        returned alongside because the confidence scorer credits `pref_rank == 1`;
        indexing by list POSITION instead would report rank 7 for a rank-1 QName that
        merely sits seventh in the expansion, and silently deny that credit.
        """
        coverage = (
            "concept_role, substitution_code, substitution_note"
            if coverage_columns_present(conn, "concept_mapping")
            else "NULL, NULL, NULL"
        )
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT qname, preference_rank, {coverage}
                  FROM concept_mapping
                 WHERE concept_family = %s
                   AND COALESCE(is_deprecated, FALSE) = FALSE
                   AND (cik_override IS NULL OR cik_override = %s)
                 ORDER BY (cik_override = %s) DESC NULLS LAST, preference_rank ASC
                """,
                (concept_family, cik, cik),
            )
            rows = cur.fetchall()
            return (
                [r[0] for r in rows],
                [int(r[1]) for r in rows],
                [r[2] for r in rows],
                [r[3] for r in rows],
                [r[4] for r in rows],
            )

    # ------------------------------------------------------------------
    # SQL queries
    # ------------------------------------------------------------------

    # The fact's XBRL precision; NULL means exact (INF, or no decimals attribute). A
    # filing tags one number again at lower precision ("$20.8 billion"), and the lookup
    # collapses such rounded duplicates (docs/specs/rounded-duplicates.md).
    _DECIMALS_SQL = (
        "(SELECT CASE WHEN f.is_decimals_infinity THEN NULL ELSE f.decimals_value::int END"
        " FROM fact f WHERE f.fact_id = {table}.fact_id) AS decimals"
    )

    _COVERAGE_COLUMNS = "concept_role, substitution_code, substitution_note, presented"
    _NO_COVERAGE_COLUMNS = (
        "NULL::text AS concept_role, NULL::text AS substitution_code,"
        " NULL::text AS substitution_note, FALSE AS presented"
    )
    _FLAT_COLUMNS = f"""
            fact_id, value, unit, period_type, period_start, period_end,
            concept, concept_family, concept_pref_rank,
            {{coverage}},
            segment_axis, segment_member, segment_label, has_multiple_dimensions,
            html_anchor, page_num, section_path, accession_no, linked_chunk_ids,
            {_DECIMALS_SQL.format(table="mv_financial_facts")}
    """

    def _query_flat(
        self,
        conn,
        cik,
        fy,
        concept_family,
        period_type,
        fiscal_period,
        duration_kind,
        dim_specs: list[_DimSpec],
        pit_accession: str | None = None,
        original: bool = False,
        observations: list[tuple[str | None, str | None]] | None = None,
        s1_select: bool = True,
    ) -> list[dict]:
        """Query mv_financial_facts for a 0- or 1-dimension fact.

        With ``pit_accession`` (an ``as_of`` lookup) the rows are that filing's rows of the
        year, as before. Otherwise the query reads every stored row of the filer's measure,
        from every filing of any fiscal period, and the rows that may answer are the year's
        own column as the latest later-labelled report restates it (S1), or, with
        ``original``, the original filing's own column (docs/specs/restatement-s1.md R3).

        Args:
            conn: An open connection.
            cik: The filer.
            fy: The fiscal-year label.
            concept_family: The measure's family.
            period_type: ``instant`` or ``duration``.
            fiscal_period: ``FY`` or a quarter.
            duration_kind: The duration length to keep, or None.
            dim_specs: No dimension, or one.
            pit_accession: The ``as_of`` accession, or None.
            original: Answer as originally reported.
            observations: When given, extended with ``(shown unit, period end)`` of every
                row the query read (before the S1 selection; the pinned filing's rows for
                ``as_of``), the rows that decide the reporting currency
                (docs/specs/lookup-reporting-currency.md R2).
            s1_select: False returns every row read, before the S1 selection and the
                ``candidate_limit`` (a Q4 lookup selects its own rows; q4-lookup).

        Returns:
            The candidate rows, latest period first, at most ``candidate_limit``.
        """
        coverage = (
            self._COVERAGE_COLUMNS
            if coverage_columns_present(conn, "mv_financial_facts")
            else self._NO_COVERAGE_COLUMNS
        )
        params: dict[str, Any] = {
            "cik": cik,
            # Passed although only the as_of query uses it: fakes of this query filter by it.
            "fy": fy,
            "concept_family": concept_family,
            "period_type": period_type,
            "limit": self.candidate_limit,
        }
        if pit_accession:
            sql = [
                "SELECT " + self._FLAT_COLUMNS.format(coverage=coverage)
                + " FROM mv_financial_facts",
                " WHERE cik = %(cik)s AND fiscal_year = %(fy)s",
                "   AND concept_family = %(concept_family)s",
                "   AND accession_no = %(pit_accession)s",
                "   AND fiscal_period = %(fiscal_period)s",
                "   AND period_type = %(period_type)s",
            ]
            params["fiscal_period"] = fiscal_period
            params["pit_accession"] = pit_accession
        else:
            # No year, period or version filter: a later report's comparative (a later
            # 10-K, or a 10-Q's year-end balance) may restate the year (S1).
            sql = [
                "SELECT " + self._FLAT_COLUMNS.format(coverage=coverage)
                + self._S1_COLUMNS + " FROM mv_financial_facts",
                " WHERE cik = %(cik)s",
                "   AND concept_family = %(concept_family)s",
                "   AND period_type = %(period_type)s",
            ]
        # For durations, pin the duration length (discrete quarter vs YTD vs
        # annual) so a 10-Q's overlapping durations don't collide.
        if duration_kind is not None:
            sql.append("   AND duration_kind = %(duration_kind)s")
            params["duration_kind"] = duration_kind

        if not dim_specs:
            sql.append(" AND segment_axis IS NULL")
        else:
            spec = dim_specs[0]
            if spec.local_axis:
                sql.append(" AND segment_axis LIKE %(axis_like)s")
                params["axis_like"] = f"%:{spec.local_axis}"
            else:
                sql.append(" AND segment_axis IS NOT NULL")
            if spec.member_qname:
                sql.append(" AND segment_member = %(member_qname)s")
                params["member_qname"] = spec.member_qname
            else:
                # label_contains_hint in SQL: the hint's key inside the label's compact
                # form, so "data center" finds "Datacenter [Member]"
                # (docs/specs/segment-label-match.md). The key is [a-z0-9] only, so it
                # needs no LIKE escaping.
                sql.append(
                    " AND regexp_replace(replace(replace(lower(segment_label),"
                    " '[member]', ' '), '&', ' and '), '[^a-z0-9]', '', 'g')"
                    " LIKE %(member_like)s"
                )
                params["member_like"] = f"%{member_label_key(spec.member_hint)}%"

        sql.append(publish_gate_clause(conn, "mv_financial_facts"))
        sql.append(
            " ORDER BY period_end DESC NULLS LAST, concept_pref_rank ASC NULLS LAST, fact_id DESC"
        )
        if pit_accession:
            sql.append(" LIMIT %(limit)s")
        rows = self._fetch(conn, "".join(sql), params)
        if observations is not None:
            observations.extend(_unit_observation(r) for r in rows)
        if pit_accession or not s1_select:
            return rows
        return self._select_s1(rows, fy, fiscal_period, original)[: self.candidate_limit]

    # The filing columns the S1 selection reads (docs/specs/restatement-s1.md R1-R2). The
    # DEI period end is read from mv_dei_metadata (one row per accession), the source of the
    # view's own document_period_end, so the lookup also runs on a view built before it.
    _S1_COLUMNS = (
        ", fiscal_year, fiscal_period, filed_date, is_amendment, is_canonical_filing,"
        " (SELECT d.document_period_end FROM mv_dei_metadata d"
        " WHERE d.accession_no = mv_financial_facts.accession_no LIMIT 1)"
        " AS document_period_end"
    )

    @staticmethod
    def _select_s1(
        rows: list[dict], fy: int, fiscal_period: str, original: bool
    ) -> list[dict]:
        """The rows that may answer the year: its own column, restated or as filed.

        A row without ``document_period_end`` (an older fake of this query) is passed
        through unchanged, as ``latest_restatement`` passes a row without its keys.

        Args:
            rows: The filer's stored rows of the measure, any filing.
            fy: The fiscal-year label.
            fiscal_period: ``FY`` or a quarter.
            original: The original filing's own column instead of the latest report's.

        Returns:
            The selected rows in the SQL order (latest period, rank, then fact_id
            descending), then the passed-through rows in their own order.
        """
        dated = [r for r in rows if "document_period_end" in r]
        legacy = [r for r in rows if "document_period_end" not in r]
        if original:
            chosen = current_column(dated, fy, fiscal_period, as_originally_reported=True)
        else:
            # Every dimension in the group, so a later filing's cell (filed under one of
            # its dimensions) never restates a member's own fact (dimensional-s1 R3c).
            chosen = latest_dimensional_restatement(
                current_column(dated, fy, fiscal_period), dated
            )
        chosen.sort(key=lambda r: -(r.get("fact_id") or 0))
        chosen.sort(key=lambda r: (r.get("concept_pref_rank") is None,
                                   r.get("concept_pref_rank") or 0))
        chosen.sort(key=lambda r: (r.get("period_end") is not None,
                                   str(r.get("period_end") or "")), reverse=True)
        return chosen + legacy

    def _retry_across_axes(
        self,
        conn,
        cik,
        fy,
        concept_family,
        period_type,
        fiscal_period,
        duration_kind,
        dim_specs: list[_DimSpec],
        pit_accession: str | None,
        notes: list[str],
        cells: list[dict] | None = None,
        original: bool = False,
    ) -> list[dict]:
        """Second pass for a 1-dimension lookup whose *axis hint* found nothing.

        When ``cells`` is given, the widened search's cells are set aside into it
        (`_set_aside_cells`) before anything is reported as found. ``original`` is
        `_query_flat`'s.

        ``AXIS_HINT_TO_LOCAL`` pins the planner's axis word to exactly one axis and the
        member is then matched only within it. But "segment" in analyst English means
        any breakdown dimension, not ``StatementBusinessSegmentsAxis`` specifically —
        and the taxonomy puts most revenue members elsewhere (59 on
        ProductOrServiceAxis vs 31 on StatementBusinessSegmentsAxis). So a perfectly
        good member hint missed purely on the axis: NVIDIA's "gaming" is a
        ProductOrService member, and the plan asked for it as a business segment.

        Only runs when the hinted axis returned nothing, so it can never override a
        hit on the axis the planner actually asked for. The member hint still has to
        match, and the widening is recorded in ``notes`` so the relaxation is visible
        in the trace rather than silent.
        """
        if len(dim_specs) != 1 or not dim_specs[0].local_axis:
            return []
        spec = dim_specs[0]
        # A hint that is really the axis DESCRIPTION ("product or service segment")
        # rather than a member would match nothing anyway, and widening the search
        # only raises the odds of a spurious hit.
        if not spec.member_hint or spec.member_hint in _AXIS_DESCRIPTION_HINTS:
            return []

        widened = _DimSpec(
            axis_key=spec.axis_key,
            local_axis=None,  # any axis
            member_hint=spec.member_hint,
            member_qname=spec.member_qname,
        )
        rows = self._query_flat(
            conn,
            cik,
            fy,
            concept_family,
            period_type,
            fiscal_period,
            duration_kind,
            [widened],
            pit_accession,
            original=original,
        )
        if cells is not None:
            rows = self._set_aside_cells(rows, cells)
        if rows:
            found = sorted(
                {str(r.get("segment_axis", "")).split(":")[-1] for r in rows}
            )
            notes.append(
                f"member {spec.member_hint!r} not found on hinted axis "
                f"{spec.local_axis}; matched on {found} instead (axis hint relaxed)"
            )
        return rows

    def _retry_with_neutral_dimension(
        self,
        conn,
        cik: str,
        fy: int,
        concept_family: str,
        period_type: str,
        fiscal_period: str,
        duration_kind: str | None,
        spec: _DimSpec,
        pit_accession: str | None,
        notes: list[str],
        s1: bool = False,
        original: bool = False,
    ) -> list[dict]:
        """Third pass for a 1-dimension lookup: the fact may carry a neutral member too.

        Filers tag every segment row with ``ConsolidationItemsAxis =
        OperatingSegmentsMember`` as well ("NVIDIA operating income, Graphics Segment").
        That makes it a 2-dimension fact, which the flat view files under whichever
        dimension came first, so the flat search for the segment misses it. Here the
        multi-dimension view is searched on any axis, and a row is kept only when the hint
        matches its dimensions one-to-one with the neutral member set aside
        (docs/specs/multidim-recovery.md). ``s1`` and ``original`` are `_query_multidim`'s.
        """
        if not spec.member_hint or spec.member_hint in _AXIS_DESCRIPTION_HINTS:
            return []
        widened = _DimSpec(
            axis_key=spec.axis_key,
            local_axis=None,  # any axis
            member_hint=spec.member_hint,
            member_qname=None,
        )
        rows = self._query_multidim(
            conn,
            cik,
            fy,
            concept_family,
            period_type,
            fiscal_period,
            duration_kind,
            [widened],
            pit_accession,
            filter_hints_in_sql=True,
            s1=s1,
            original=original,
        )
        rows = [
            r
            for r in rows
            if dimensions_match_ignoring_neutral(
                r.get("dimension_labels"), [spec.member_hint]
            )
        ]
        if rows:
            notes.append(
                f"member {spec.member_hint!r} found only with a neutral consolidation "
                f"member (ConsolidationItemsAxis = Operating Segments) set aside"
            )
        return rows

    @staticmethod
    def _set_aside_cells(rows: list[dict], cells: list[dict]) -> list[dict]:
        """The flat rows that are single-dimension facts; the others go to ``cells``.

        The flat view files a multi-dimension fact under its first non-default dimension
        only, so a one-dimension lookup that kept it would answer with one cell of a
        two-dimension table as the member's total (segment-lookup-screens R1). A
        cell whose other dimension is neutral comes back through
        `_retry_with_neutral_dimension`.

        Args:
            rows: Rows of `_query_flat`.
            cells: Receives the rows with ``has_multiple_dimensions`` set.

        Returns:
            The other rows, in input order.
        """
        kept: list[dict] = []
        for r in rows:
            (cells if r.get("has_multiple_dimensions") else kept).append(r)
        return kept

    def _sum_of_cells(
        self,
        conn,
        step: dict,
        cik: str,
        fy: int,
        concept_family: str,
        period_type: str,
        fiscal_period: str,
        duration_kind: str | None,
        spec: _DimSpec,
        pit_accession: str | None,
        notes: list[str],
    ) -> FactLookupResult | None:
        """A member reported only in cells over one other axis, answered as their sum.

        segment-lookup-screens Requirement 1(b). The filer's own single-dimension facts
        on the other axis, for the members the cells name, must add up to its
        undimensioned total at the same concept, unit and period: that shows the
        members partition the whole, so the member's cells over them are its total.

        Args:
            conn: An open connection.
            step: The lookup step.
            cik: The filer.
            fy: The fiscal year.
            concept_family: The step's concept family.
            period_type: ``duration`` or ``instant``.
            fiscal_period: ``FY`` or a quarter.
            duration_kind: The duration kind, or None.
            spec: The one requested dimension.
            pit_accession: The point-in-time accession, or None.
            notes: Resolution notes, appended to.

        Returns:
            An OK result with ``sum_of`` set, or None when the member's cells do not
            add up to a stated total (a note then names the cells found).
        """
        if not spec.member_hint or spec.member_hint in _AXIS_DESCRIPTION_HINTS:
            return None
        want = member_label_key(spec.member_hint)
        widened = _DimSpec(
            axis_key=spec.axis_key, local_axis=None, member_hint=spec.member_hint,
            member_qname=None,
        )
        rows = self._query_multidim(
            conn, cik, fy, concept_family, period_type, fiscal_period, duration_kind,
            [widened], pit_accession, filter_hints_in_sql=True, limit=_SUM_CELL_LIMIT,
        )
        # Cells of exactly two dimensions: the member itself and one other axis.
        cells: list[tuple[dict, str, str]] = []
        for r in rows:
            labels = r.get("dimension_labels") or {}
            mine = [a for a, lbl in labels.items() if member_label_key(lbl) == want]
            if len(labels) != 2 or len(mine) != 1 or r.get("value") is None:
                continue
            other = next(a for a in labels if a != mine[0])
            cells.append((r, mine[0], other))
        if not cells:
            return None

        latest = max(str(r.get("period_end")) for r, _, _ in cells)
        groups: dict[tuple, list[tuple[dict, str, str]]] = {}
        for cell in cells:
            r, _, other = cell
            if str(r.get("period_end")) != latest:
                continue
            if step.get("unit") and r.get("unit") != step.get("unit"):
                continue
            key = (r.get("concept"), r.get("unit"), _local_name(other))
            groups.setdefault(key, []).append(cell)

        def group_order(key: tuple) -> tuple:
            rank = groups[key][0][0].get("concept_pref_rank")
            return (rank if rank is not None else 10**9, key[1] != "USD", str(key))

        partitions: dict[str, tuple[list[dict], list[dict]]] = {}
        for key in sorted(groups, key=group_order):
            concept, unit, axis = key
            if axis not in partitions:
                partitions[axis] = self._query_partition(
                    conn, cik, fy, concept_family, period_type, fiscal_period,
                    duration_kind, pit_accession, axis,
                )
            totals, singles = partitions[axis]
            proven = _partition_sum(groups[key], totals, singles, concept, unit, latest)
            if proven is None:
                continue
            summed, total = proven
            return self._sum_result(
                step, spec, concept_family, cik, summed, total, axis, notes
            )

        notes.append(
            f"member {spec.member_hint!r} is reported only in cells with another "
            "dimension, and they do not add up to a stated total over one axis: "
            + ", ".join(
                f"fact {r.get('fact_id')} ({' x '.join(sorted(r['dimension_labels'].values()))})"
                for r, _, _ in cells
            )
        )
        return None

    def _query_partition(
        self,
        conn,
        cik: str,
        fy: int,
        concept_family: str,
        period_type: str,
        fiscal_period: str,
        duration_kind: str | None,
        pit_accession: str | None,
        axis_local: str,
    ) -> tuple[list[dict], list[dict]]:
        """The filer's undimensioned facts and its facts on one axis, for a partition.

        Args:
            conn: An open connection.
            cik: The filer.
            fy: The fiscal year.
            concept_family: The concept family.
            period_type: ``duration`` or ``instant``.
            fiscal_period: ``FY`` or a quarter.
            duration_kind: The duration kind, or None.
            pit_accession: The point-in-time accession, or None.
            axis_local: The axis's local name.

        Returns:
            ``(totals, on_axis)`` rows of ``mv_financial_facts``.
        """
        params: dict[str, Any] = {
            "cik": cik, "fy": fy, "concept_family": concept_family,
            "fiscal_period": fiscal_period, "period_type": period_type,
            "axis_like": f"%:{axis_local}",
        }
        clauses = [
            "SELECT fact_id, value, unit, period_end, concept, segment_axis,"
            " segment_member, segment_label, has_multiple_dimensions"
            " FROM mv_financial_facts"
            " WHERE cik = %(cik)s AND fiscal_year = %(fy)s"
            " AND concept_family = %(concept_family)s"
            " AND fiscal_period = %(fiscal_period)s AND period_type = %(period_type)s"
        ]
        if pit_accession:
            clauses.append(" AND accession_no = %(pit_accession)s")
            params["pit_accession"] = pit_accession
        else:
            clauses.append(" AND is_canonical_filing = TRUE")
        if duration_kind is not None:
            clauses.append(" AND duration_kind = %(duration_kind)s")
            params["duration_kind"] = duration_kind
        clauses.append(publish_gate_clause(conn, "mv_financial_facts"))
        base = "".join(clauses)
        totals = self._fetch(conn, base + " AND segment_axis IS NULL", params)
        on_axis = self._fetch(conn, base + " AND segment_axis LIKE %(axis_like)s", params)
        return totals, on_axis

    def _sum_result(
        self,
        step: dict,
        spec: _DimSpec,
        concept_family: str,
        cik: str,
        summed: list[tuple[dict, str, str]],
        total: float,
        axis: str,
        notes: list[str],
    ) -> FactLookupResult:
        """The result of a proven sum of cells (or a refusal below the threshold)."""
        summed = sorted(summed, key=lambda c: int(c[0]["fact_id"]))
        value = math.fsum(float(r["value"]) for r, _, _ in summed)
        first, member_axis, _ = summed[0]
        best = dataclasses.replace(
            self._row_to_candidate(first),
            value=value,
            dimension_labels={member_axis: first["dimension_labels"][member_axis]},
        )
        ids = [int(r["fact_id"]) for r, _, _ in summed]
        terms = " + ".join(f"{float(r['value']):,.0f}" for r, _, _ in summed)
        notes.append(
            f"member {spec.member_hint!r} is reported only in cells over {axis}, whose "
            f"members' own facts add up to the total {total:,.0f}: answered as the sum "
            f"of its cells {terms} = {value:,.0f} (facts {ids})"
        )
        confidence = self._score_confidence(
            best, [best], [spec], step.get("section_hint"), notes, concept_family, cik=cik
        )
        if confidence < self.min_confidence:
            notes.append(
                f"confidence {confidence:.2f} < threshold {self.min_confidence:.2f}"
            )
            return FactLookupResult(
                step.get("id"), LookupStatus.UNRESOLVED_DIMENSION, resolution_notes=notes
            )
        return FactLookupResult(
            step_id=step.get("id"),
            status=LookupStatus.OK,
            value=value,
            unit=display_unit(best.unit),
            citation=best.citation,
            confidence=confidence,
            candidates=[best],
            resolution_notes=notes,
            sum_of=ids,
        )

    def _query_multidim(
        self,
        conn,
        cik,
        fy,
        concept_family,
        period_type,
        fiscal_period,
        duration_kind,
        dim_specs: list[_DimSpec],
        pit_accession: str | None = None,
        filter_hints_in_sql: bool = False,
        limit: int | None = None,
        s1: bool = False,
        original: bool = False,
    ) -> list[dict]:
        """Query mv_financial_facts_multidim for a 2+ dimension fact, then filter
        rows whose `dimension_labels` satisfy every requested (axis, member).

        With ``s1`` (a lookup without ``as_of``) the query reads every stored row of the
        filer for the family's concepts, from every filing of any fiscal period, with the
        filing columns S1 needs, and the rows that may answer are the year's own column as
        the latest later-labelled report with the same dimensions restates it, or, with
        ``original``, the original filing's own column (docs/specs/dimensional-s1.md R3a).
        The SQL's order and limit, then the hint matching, run on those rows.

        ``filter_hints_in_sql`` also narrows by the hints before the LIMIT, for the
        broad one-hint, any-axis search of the neutral-member retry. ``limit``
        overrides ``candidate_limit`` (the sum-of-cells search needs every cell).

        mv_financial_facts_multidim has no concept_family column, so the concept
        predicate is built from concept_mapping QNames when available, else falls
        back to a `concept ILIKE '%Family%'` pattern (design §3.5 uses the latter
        for ConcentrationRiskPercentage, which concept_mapping does not cover).
        """
        params: dict[str, Any] = {
            "cik": cik,
            "fy": fy,
            "period_type": period_type,
            "fiscal_period": fiscal_period,
            "limit": self.candidate_limit if limit is None else limit,
        }
        row_limit = params["limit"]
        duration_predicate = ""
        if duration_kind is not None:
            duration_predicate = "AND duration_kind = %(duration_kind)s"
            params["duration_kind"] = duration_kind
        version_predicate = ""
        if pit_accession:
            version_predicate = "AND accession_no = %(pit_accession)s"
            params["pit_accession"] = pit_accession
        publish_predicate = publish_gate_clause(conn, "mv_financial_facts_multidim")
        (
            concept_qnames, concept_ranks, concept_roles, sub_codes, sub_notes
        ) = self._expand_concept_family(conn, cik, concept_family)
        # _expand_concept_family already returns QNames in curated preference order
        # (cik override first, then preference_rank), so array_position reproduces that
        # ranking without a join. The flat path breaks ties on concept_pref_rank; the
        # multidim path selected NULL for it and could not, so a fully-specified
        # 2-dimension lookup returned AMBIGUOUS even when the preferred concept was
        # obvious. AAPL FY2024 "commercial paper" + "Level 2" matches 14 facts across
        # AvailableForSale*/MarketableSecurities*/CashAndCashEquivalentsAtCarryingValue;
        # the dimensions do not disambiguate, the concept does.
        rank_expr = "NULL"
        role_exprs = "NULL AS concept_role, NULL AS substitution_code, NULL AS substitution_note"
        rank_order = ""  # Postgres rejects a bare `NULL` term in ORDER BY
        if concept_qnames:
            concept_predicate = "concept = ANY(%(concept_qnames)s)"
            params["concept_qnames"] = concept_qnames
            params["concept_ranks"] = concept_ranks
            params["concept_roles"] = concept_roles
            params["sub_codes"] = sub_codes
            params["sub_notes"] = sub_notes
            # ORDER by curated position; REPORT the real preference_rank.
            position = "array_position(%(concept_qnames)s, concept)"
            rank_expr = f"(%(concept_ranks)s)[{position}]"
            # The role and stated substitution of the same row
            # (docs/specs/concept-family-coverage.md).
            role_exprs = (
                f"(%(concept_roles)s::text[])[{position}] AS concept_role,"
                f" (%(sub_codes)s::text[])[{position}] AS substitution_code,"
                f" (%(sub_notes)s::text[])[{position}] AS substitution_note"
            )
            rank_order = "array_position(%(concept_qnames)s, concept) ASC NULLS LAST,"
        else:
            concept_predicate = "concept ILIKE %(concept_like)s"
            params["concept_like"] = f"%{concept_family}%"
            logger.debug(
                f"multidim: no concept_mapping for {concept_family!r}; using ILIKE fallback"
            )

        # Narrow by the hints in SQL, before the LIMIT: a broad search (one hint on any
        # axis) otherwise fills the limit with rows the Python filter then drops. Space-free
        # containment here is implied by `_hint_in_label`, so no row it keeps is lost.
        # Off for the ordinary path: there it would surface rows the LIMIT used to cut,
        # and the family-wide distinct-value check (Phase 3(a)) then turns resolved
        # lookups AMBIGUOUS (Micron FY2024 "2027 Term Loan A").
        hint_predicates = []
        for i, spec in enumerate(dim_specs):
            if not filter_hints_in_sql or spec.member_qname or not spec.member_hint:
                continue
            params[f"hint_{i}"] = f"%{_like_escape(spec.member_hint.replace(' ', ''))}%"
            hint_predicates.append(
                "AND EXISTS (SELECT 1 FROM jsonb_each_text(dimension_labels) AS d(k, v)"
                " WHERE regexp_replace(lower(d.v), '\\[member\\]|[\\[\\]\\s]', '', 'g')"
                f" LIKE %(hint_{i})s)"
            )
        hint_predicate = "\n               ".join(hint_predicates)

        if s1:
            # No year, period or version filter: a later report's comparative may restate
            # the year (S1). The filing columns come from filing_registry (as the flat view
            # builds them) and mv_dei_metadata, so the view needs no rebuild.
            period_start = "period_start"
            s1_columns = (
                ", fiscal_year, fiscal_period, s1f.filed_date, s1f.is_amendment,"
                " s1f.is_canonical_filing,"
                " (SELECT d.document_period_end FROM mv_dei_metadata d"
                " WHERE d.accession_no = mv_financial_facts_multidim.accession_no LIMIT 1)"
                " AS document_period_end"
            )
            s1_join = (
                "LEFT JOIN LATERAL (SELECT fr.filed_date, fr.is_amendment,"
                " fr.superseded_by IS NULL AS is_canonical_filing FROM filing_registry fr"
                " WHERE fr.accession_no = mv_financial_facts_multidim.accession_no"
                " AND fr.cik = mv_financial_facts_multidim.cik LIMIT 1) s1f ON TRUE"
            )
            year_predicate = ""
            period_predicate = ""
            limit_clause = ""
        else:
            period_start = "NULL"
            s1_columns = ""
            s1_join = ""
            year_predicate = "AND fiscal_year = %(fy)s"
            period_predicate = "AND fiscal_period = %(fiscal_period)s"
            limit_clause = "LIMIT %(limit)s"

        sql = f"""
            SELECT fact_id, value, unit, period_type, {period_start} AS period_start, period_end,
                   concept, NULL AS concept_family,
                   {rank_expr} AS concept_pref_rank,
                   {role_exprs}, FALSE AS presented,
                   NULL AS segment_axis, NULL AS segment_member, NULL AS segment_label,
                   html_anchor, page_num, section_path, accession_no,
                   ARRAY[]::text[] AS linked_chunk_ids,
                   dimension_labels, dimensions,
                   {self._DECIMALS_SQL.format(table="mv_financial_facts_multidim")}
                   {s1_columns}
              FROM mv_financial_facts_multidim
              {s1_join}
             WHERE cik = %(cik)s {year_predicate}
               AND {concept_predicate}
               {period_predicate}
               AND period_type = %(period_type)s
               {duration_predicate}
               {version_predicate}
               {hint_predicate}
               {publish_predicate}
             ORDER BY period_end DESC NULLS LAST,
                      {rank_order}
                      fact_id DESC
             {limit_clause}
        """
        rows = self._fetch(conn, sql, params)
        if s1:
            rows = self._select_multidim_s1(
                rows, fy, fiscal_period, original, concept_qnames, row_limit
            )
        return [
            r
            for r in rows
            if self._multidim_row_matches(r.get("dimension_labels") or {}, dim_specs)
        ]

    @staticmethod
    def _select_multidim_s1(
        rows: list[dict],
        fy: int,
        fiscal_period: str,
        original: bool,
        concept_qnames: list[str],
        limit: int,
    ) -> list[dict]:
        """The multidim rows that may answer the year, in the SQL's order (S1).

        The year's own column is cut to ``limit`` rows in the SQL's order, as the
        year-filtered query cut it, and only then restated: the candidates are today's
        facts, each as the latest report states it, so a restatement never changes which
        facts compete. A row without ``document_period_end`` (another spec's fake of this
        query) is kept only when it is labelled with the requested year and period, as the
        year-filtered query keeps it.

        Args:
            rows: The filer's stored multidim rows of the family, any filing.
            fy: The fiscal-year label.
            fiscal_period: ``FY`` or a quarter.
            original: The original filing's own column instead of the latest report's.
            concept_qnames: The family's concepts in curated order (may be empty).
            limit: The candidate limit.

        Returns:
            The selected rows, latest period first, then curated concept order, then
            fact_id descending; then the kept legacy rows, at most ``limit`` in all.
        """
        position = {q: i for i, q in enumerate(concept_qnames)}

        def in_sql_order(selected: list[dict]) -> list[dict]:
            selected.sort(key=lambda r: -(r.get("fact_id") or 0))
            selected.sort(key=lambda r: position.get(str(r.get("concept")), len(position)))
            selected.sort(key=lambda r: (r.get("period_end") is not None,
                                         str(r.get("period_end") or "")), reverse=True)
            return selected

        dated = [r for r in rows if "document_period_end" in r]
        legacy = [
            r
            for r in rows
            if "document_period_end" not in r
            and r.get("fiscal_year") == fy
            and r.get("fiscal_period") == fiscal_period
        ]
        own = in_sql_order(
            current_column(dated, fy, fiscal_period, as_originally_reported=original)
        )[:limit]
        if not original:
            own = in_sql_order(latest_dimensional_restatement(own, dated))
        return (own + in_sql_order(legacy))[:limit]

    @staticmethod
    def _multidim_row_matches(
        dimension_labels: dict, dim_specs: list[_DimSpec]
    ) -> bool:
        """True iff every requested dimension is satisfied by some (axis, label)
        pair in the multidim row's `dimension_labels` JSONB."""
        # dimension_labels: {axis_full_uri: member_label}
        axis_label_pairs = [
            (_local_name(ax), _norm_label(lbl)) for ax, lbl in dimension_labels.items()
        ]
        for spec in dim_specs:
            satisfied = any(
                (spec.local_axis is None or local_ax == spec.local_axis)
                and _hint_in_label(spec.member_hint, label)
                for local_ax, label in axis_label_pairs
            )
            if not satisfied:
                return False
        return True

    def _catalog_labels(self, conn, cik: str, rows: list[dict]) -> list[str]:
        """The filer's member labels, any year, on the axes the matched rows use."""
        axes = sorted(_axis_local_names(rows))
        if not axes:
            return []
        sql = (
            "SELECT DISTINCT member_label FROM dimension_catalog"
            " WHERE cik = %(cik)s AND member_label IS NOT NULL"
            " AND regexp_replace(axis_qname, '^.*:', '') = ANY(%(axes)s)"
        )
        return [r["member_label"] for r in self._fetch(conn, sql, {"cik": cik, "axes": axes})]

    @staticmethod
    def _fetch(conn, sql: str, params: dict) -> list[dict]:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            t0 = time.perf_counter()
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            _emit_sql_event(
                cur,
                sql,
                params,
                len(rows),
                (time.perf_counter() - t0) * 1000.0,
                "factstore_lookup",
            )
            return rows

    # ------------------------------------------------------------------
    # Row -> candidate
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_candidate(row: dict) -> FactCandidate:
        citation = Citation(
            fact_id=row.get("fact_id"),
            accession_no=row.get("accession_no"),
            html_anchor=row.get("html_anchor"),
            page_num=row.get("page_num"),
            section_path=row.get("section_path"),
        )
        value = row.get("value")
        return FactCandidate(
            fact_id=row.get("fact_id"),
            value=float(value) if value is not None else None,
            unit=row.get("unit"),
            period_type=row.get("period_type"),
            period_start=(
                str(row["period_start"])
                if row.get("period_start") is not None
                else None
            ),
            period_end=(
                str(row["period_end"]) if row.get("period_end") is not None else None
            ),
            concept=row.get("concept"),
            dimension_labels=row.get("dimension_labels"),
            concept_family=row.get("concept_family"),
            concept_pref_rank=row.get("concept_pref_rank"),
            segment_axis=row.get("segment_axis"),
            segment_member=row.get("segment_member"),
            segment_label=row.get("segment_label"),
            section_path=row.get("section_path"),
            citation=citation,
            linked_chunk_ids=list(row.get("linked_chunk_ids") or []),
            decimals=row.get("decimals"),
            concept_role=row.get("concept_role"),
            presented=bool(row.get("presented")),
            substitution_code=row.get("substitution_code"),
            substitution_note=row.get("substitution_note"),
        )

    @staticmethod
    def _section_consistent(section_hint: str, section_path: str) -> bool:
        """True if any significant word of the section hint appears in the path."""
        path_lc = section_path.lower()
        hint_words = [w for w in re.split(r"\W+", section_hint.lower()) if len(w) > 3]
        return any(w in path_lc for w in hint_words)


def _definition_record(measure: DefinedMeasure) -> dict:
    """The ``definition`` a lookup result carries for the synthesizer and the evidence."""
    return {
        "concept": measure["concept"],
        "status": measure["status"],
        "basis": measure["basis"],
        "disclosure": measure["disclosure"],
        "readings": [dict(r) for r in measure["readings"]],
        "parts": [
            {"fact_id": p["fact_id"], "concept": p["concept"], "value": p["value"],
             "section": p["section"], "label": p["label"]}
            for p in measure["parts"]
        ],
    }


def _local_name(qname: str) -> str:
    """Local part of a QName or namespace URI (everything after the last ':')."""
    return qname.split(":")[-1] if qname else qname


def rank_credit(concept_family: str, concept: str, rank: int | None) -> bool:
    """Whether a winning concept earns the preferred-concept confidence credit.

    See docs/specs/revenue-equivalent-concepts.md.

    Args:
        concept_family: The lookup's concept family.
        concept: The winning fact's concept QName.
        rank: Its concept_pref_rank for the filer.

    Returns:
        True for rank 1, or for a concept equivalent to the family's measure outside the
        component band.
    """
    if rank == 1:
        return True
    if rank is None or rank in _COMPONENT_RANKS:
        return False
    return _local_name(concept) in EQUIVALENT_PRIMARY_CONCEPTS.get(concept_family, ())


# Concepts that are the same measure as the family's preferred one, so a filer that
# switches between them keeps the rank credit. Revenue only: a near-miss concept (a
# current portion, a related measure) must stay a fallback
# (docs/specs/revenue-equivalent-concepts.md).
EQUIVALENT_PRIMARY_CONCEPTS: dict[str, frozenset[str]] = {
    "Revenue": frozenset(
        {"RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"}
    ),
}


def _norm_label(label: str) -> str:
    """Normalize a member label/hint for comparison: lowercase, drop a trailing
    '[member]' marker and surrounding brackets/whitespace."""
    if not label:
        return ""
    s = label.lower().replace("[member]", " ")
    s = re.sub(r"[\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def dimensions_fully_matched(
    dimension_labels: collections.abc.Mapping[str, str] | None,
    member_hints: Sequence[str],
) -> bool:
    """Whether the hints pick out a multi-dimension fact's dimensions one-to-one.

    Each hint must be contained in a different member label, and the fact must have
    exactly as many dimensions as there are hints (docs/specs/multidim-full-match.md).

    Args:
        dimension_labels: The fact's ``{axis: member_label}``.
        member_hints: The lookup's member hints.

    Returns:
        True when at least two hints match the fact's labels one-to-one.
    """
    labels = [_norm_label(lbl) for lbl in (dimension_labels or {}).values()]
    hints = [_norm_label(h) for h in member_hints]
    if len(hints) < 2 or len(labels) != len(hints):
        return False

    # Bipartite matching (hints <-> labels); sizes are tiny, so augmenting paths suffice.
    owner: dict[int, int] = {}

    def assign(h: int, seen: set[int]) -> bool:
        for j, label in enumerate(labels):
            if j in seen or hints[h] not in label:
                continue
            seen.add(j)
            if j not in owner or assign(owner[j], seen):
                owner[j] = h
                return True
        return False

    return all(assign(h, set()) for h in range(len(hints)))


def is_neutral_dimension(axis: str, member_label: str) -> bool:
    """Whether a dimension only marks a segment-level figure (docs/specs/multidim-recovery.md).

    Args:
        axis: The axis QName or URI-qualified name.
        member_label: The member's label.

    Returns:
        True for ``ConsolidationItemsAxis = OperatingSegmentsMember``.
    """
    return (
        _local_name(axis) == "ConsolidationItemsAxis"
        and _norm_label(member_label).replace(" ", "") in _NEUTRAL_MEMBER_FORMS
    )


def one_value_credit(
    winner: str,
    winner_rank: int | None,
    current: Sequence[tuple[str, int | None, float | None]],
    components_block: bool,
) -> bool:
    """Whether a lookup earns the "one distinct value" confidence credit.

    See docs/specs/within-concept-confidence.md.

    Args:
        winner: The best candidate's concept.
        winner_rank: Its preference rank.
        current: ``(concept, preference_rank, value)`` of the latest-period candidates.
        components_block: Whether a disagreeing component-band concept blocks the credit.

    Returns:
        True when the winning concept has one value and no blocking rival disagrees.
    """
    win = _local_name(winner)
    mine = {
        round(v, 6) for q, _, v in current if v is not None and _local_name(q) == win
    }
    if len(mine) != 1:
        return False
    (value,) = mine
    for qname, rank, v in current:
        if v is None or _local_name(qname) == win or round(v, 6) == value:
            continue
        if rank == winner_rank:
            return False  # a different concept tied at the winner's rank
        if components_block and rank is not None and rank in _COMPONENT_RANKS:
            return False  # a component the question may be asking for
    return True


# The component band of concept_mapping (docs/specs/concept-rank-integrity.md).
_COMPONENT_RANKS = range(50, 100)

# Families whose components are themselves what a question asks for, and which the
# planner cannot name: "debt current" is planned as Debt, whose rank 1 is LongTermDebt,
# so a disagreeing DebtCurrent must keep the lookup AMBIGUOUS. D&A components are never
# asked for as "D&A" and do not block (docs/specs/within-concept-confidence.md).
COMPONENT_QUESTION_FAMILIES = frozenset({"Debt"})

# Totals that already contain every component of their family: a disagreeing component
# beside them is a part, not a rival reading of the question (total debt contains its
# current part; docs/specs/concept-family-coverage.md, Notes "Debt guard").
COMPONENT_INCLUSIVE_CONCEPTS: dict[str, frozenset[str]] = {
    "Debt": frozenset({"DebtLongtermAndShorttermCombinedAmount"}),
}


def _role_of(candidate: FactCandidate) -> str:
    """The candidate's role, or the one its rank implies when the row names none."""
    return row_role(candidate.order_row())


def components_block_answer(
    concept_family: str, winner: str, role: str, dimensioned: bool = True
) -> bool:
    """Whether a disagreeing component keeps the winner from the one-value credit.

    Only for a family whose components a question may itself ask for (a member hint
    such as Tesla's "debt current" planned as Debt), and only when the winner is a
    primary that does not already contain them. An undimensioned lookup asks for the
    family's total; a stated substitute says what it leaves out; so a component beside
    either never blocks (docs/specs/within-concept-confidence.md,
    concept-family-coverage.md, Notes "Debt guard").

    Args:
        concept_family: The lookup's family.
        winner: The answer's concept.
        role: The answer's role.
        dimensioned: Whether the lookup names a dimension member.

    Returns:
        True when disagreeing components block the credit.
    """
    if (
        not dimensioned
        or concept_family not in COMPONENT_QUESTION_FAMILIES
        or role != ROLE_PRIMARY
    ):
        return False
    return _local_name(winner) not in COMPONENT_INCLUSIVE_CONCEPTS.get(
        concept_family, frozenset()
    )


def member_credit(concept_family: str, cik: str | None, winner: FactCandidate) -> bool:
    """The preferred-concept credit beyond ``rank_credit`` (concept-family-coverage).

    A stated substitute was decided as the filer's nearest line and its answer says so;
    a primary earns it when the committed mapping marks it the family's own measure
    for the filer (``same_measure``).

    Args:
        concept_family: The lookup's family.
        cik: The filer's CIK; None skips the cik-scoped members.
        winner: The answer.

    Returns:
        True when the winner earns the credit.
    """
    role = _role_of(winner)
    if role == ROLE_SUBSTITUTE:
        return winner.concept_role == ROLE_SUBSTITUTE
    if role != ROLE_PRIMARY or not winner.concept:
        return False
    return same_measure(concept_family, cik or "", winner.concept)


def order_credit(
    winner: collections.abc.Mapping[str, Any],
    current: Sequence[collections.abc.Mapping[str, Any]],
) -> bool:
    """Whether a winner earns the preferred-concept credit from its place in candidate order.

    ``candidate_order`` puts a line presented on a primary statement ahead of a notes-only
    fact of better rank, so such a winner is not a fallback. It earns the credit only when
    presentation is what put it first: it is a presented primary, a primary of better rank
    at its period is notes-only, and no other presented primary at its period disagrees
    with its value. A winner first only by rank, precision or fact id among presented
    primaries, or one beside a disagreeing presented primary, earns nothing here.

    Args:
        winner: The answer's ``order_row()``.
        current: ``order_row()`` of every candidate at the answer's period, the winner
            included.

    Returns:
        True when presentation, not a tie-break, decided the answer.
    """
    rank = winner.get("concept_pref_rank")
    if not winner.get("presented") or row_role(winner) != ROLE_PRIMARY or rank is None:
        return False
    value = winner.get("value")
    if value is None:
        return False
    outranked = False
    for row in current:
        if row is winner or row.get("fact_id") == winner.get("fact_id"):
            continue
        if row_role(row) != ROLE_PRIMARY or row.get("period_end") != winner.get("period_end"):
            continue
        other = row.get("value")
        if row.get("presented"):
            if other is not None and round(other, 6) != round(value, 6):
                return False  # two presented primaries disagree
            continue
        r = row.get("concept_pref_rank")
        if r is not None and r < rank:
            outranked = True  # a better-ranked fact the order set aside as notes-only
    return outranked


def most_precise_consistent(facts: Sequence[tuple[float, int | None]]) -> int | None:
    """The most precise fact, when every fact is that fact at its own precision.

    See docs/specs/rounded-duplicates.md.

    Args:
        facts: ``(value, decimals)`` pairs; ``decimals`` None means exact (INF or absent).

    Returns:
        The index of the first most precise fact, or None when the facts are
        inconsistent (or there are none).
    """
    if not facts:
        return None
    # Precision rank: exact (INF / no decimals attribute) beats any finite decimals.
    rank = [float("inf") if d is None else d for _, d in facts]
    top_i = rank.index(max(rank))
    top = decimal.Decimal(repr(facts[top_i][0]))
    for (value, d), r in zip(facts, rank, strict=True):
        v = decimal.Decimal(repr(value))
        if r == rank[top_i] and v != top:
            return None  # two facts at the top precision disagree: a conflict, not rounding
        if d is None:
            if v != top:
                return None
        elif abs(v - top) > decimal.Decimal(5).scaleb(-d - 1):
            return None  # further than half a unit at its own decimals
    return top_i


def member_label_key(text: str) -> str:
    """The comparison form of a member label or hint.

    See docs/specs/segment-label-match.md.

    Args:
        text: A member label (e.g. ``Europe Segment [Member]``) or a planner hint.

    Returns:
        Lowercase alphanumerics with ``&`` read as ``and`` and one trailing generic word
        (segment, segments, member) dropped.
    """
    words = _label_words(text)
    if len(words) > 1 and words[-1] in _GENERIC_TRAILING_WORDS:
        words = words[:-1]
    return "".join(words)


_GENERIC_TRAILING_WORDS = frozenset({"segment", "segments", "member"})


def _label_words(text: str) -> list[str]:
    s = (text or "").lower().replace("[member]", " ").replace("&", " and ")
    return re.findall(r"[a-z0-9]+", s)


def label_contains_hint(label: str, hint: str) -> bool:
    """Whether a one-dimension lookup keeps a fact with this label for this hint.

    See docs/specs/segment-label-match.md.

    Args:
        label: The fact's member label.
        hint: The planner's member hint.

    Returns:
        True when the hint's key is a substring of the label's compact form.
    """
    return member_label_key(hint) in "".join(_label_words(label))


def compact_label(text: str | None) -> str:
    """A member label's compact form: `member_label_key` without dropping a trailing word.

    See docs/specs/segment-lookup-screens.md (Definitions).

    Args:
        text: A member label or a hint.

    Returns:
        Lowercase alphanumerics, ``[member]`` removed and ``&`` read as ``and``.
    """
    return "".join(_label_words(text or ""))


# The sum-of-cells search reads every cell of the member at the fiscal year, not the
# candidate limit (segment-lookup-screens R1(b)); a filer's cells for one member and
# year are far fewer.
_SUM_CELL_LIMIT = 500


def _member_id(qname: str | None, label: str | None) -> str:
    """A member's identity across the flat and multi-dimension views.

    The flat view stores a prefixed QName (``country:US``) and the multi-dimension view
    a URI-qualified one (``http://xbrl.sec.gov/country/2023:US``); their local names
    agree. The label key stands in when no QName is stored.
    """
    if qname:
        return "q:" + re.split(r"[:}]", qname)[-1]
    return "l:" + member_label_key(label or "")


def _partition_sum(
    cells: Sequence[tuple[dict, str, str]],
    totals: Sequence[dict],
    on_axis: Sequence[dict],
    concept: str | None,
    unit: str | None,
    period_end: str,
) -> tuple[list[tuple[dict, str, str]], float] | None:
    """The member's cells over a partition of the whole, when the filer's facts prove it.

    segment-lookup-screens Requirement 1(b): the filer's single-dimension facts on the
    cells' other axis, for the members the cells name, add up to its undimensioned
    total at the same concept, unit and period.

    Args:
        cells: ``(row, member_axis, other_axis)`` of the member's cells, all at one
            concept, unit, period and other axis.
        totals: The filer's undimensioned rows.
        on_axis: The filer's rows on the other axis.
        concept: The cells' concept.
        unit: The cells' unit.
        period_end: The cells' period end, as a string.

    Returns:
        ``(cells summed, total)``, one cell per other-axis member; None when a member has
        two values, a member has no single-dimension fact, or the sum is not the total.
    """

    def same(r: dict) -> bool:
        return (
            r.get("concept") == concept and r.get("unit") == unit
            and str(r.get("period_end")) == period_end and r.get("value") is not None
        )

    total_values = {float(r["value"]) for r in totals if same(r)}
    if len(total_values) != 1:
        return None
    total = next(iter(total_values))

    by_member: dict[str, tuple[dict, str, str]] = {}
    for cell in sorted(cells, key=lambda c: int(c[0]["fact_id"])):
        r, _, other = cell
        member = _member_id(
            (r.get("dimensions") or {}).get(other), r["dimension_labels"].get(other)
        )
        prior = by_member.get(member)
        if prior is not None and float(prior[0]["value"]) != float(r["value"]):
            return None  # two values for one member's cell
        by_member.setdefault(member, cell)

    single: dict[str, set[float]] = {}
    for r in on_axis:
        if same(r) and not r.get("has_multiple_dimensions"):
            member = _member_id(r.get("segment_member"), r.get("segment_label"))
            single.setdefault(member, set()).add(float(r["value"]))
    parts = []
    for member in by_member:
        values = single.get(member, set())
        if len(values) != 1:
            return None
        parts.append(next(iter(values)))
    if not math.isclose(math.fsum(parts), total, rel_tol=1e-9, abs_tol=0.5):
        return None
    return list(by_member.values()), total


def refuse_stand_in(hint: str, matched_labels: list[str], catalog_labels: list[str]) -> bool:
    """Whether a containment-only match is another member standing in for the hint's own.

    See docs/specs/identity-and-stand-in.md.

    Args:
        hint: The one-dimension lookup's member hint.
        matched_labels: Member labels of the facts the lookup kept for the hint.
        catalog_labels: The filer's member labels on the searched axes, any year.

    Returns:
        True when no matched label equals the hint by key but a catalog label does.
    """
    want = member_label_key(hint)
    if any(member_label_key(m) == want for m in matched_labels):
        return False
    return any(member_label_key(c) == want for c in catalog_labels)


def _candidate_member_labels(candidates) -> set[str]:
    """Every member label on the candidates: the flat label and, for multi-dimension
    rows, each dimension's label."""
    labels: set[str] = set()
    for c in candidates:
        if c.segment_label:
            labels.add(c.segment_label)
        labels.update(v for v in (c.dimension_labels or {}).values() if v)
    return labels


def _axis_local_names(rows: list[dict]) -> set[str]:
    """Local names of the axes the rows' facts sit on (``srt:X``, a URI or Clark form)."""
    axes = [r.get("segment_axis") for r in rows]
    for r in rows:
        axes.extend((r.get("dimension_labels") or {}).keys())
    return {re.split(r"[:}]", a)[-1] for a in axes if a}


def dimensions_match_ignoring_neutral(
    dimension_labels: collections.abc.Mapping[str, str] | None,
    member_hints: Sequence[str],
) -> bool:
    """Whether the hints pick out a fact's dimensions one-to-one, neutral ones set aside.

    See docs/specs/multidim-recovery.md (C2).

    Args:
        dimension_labels: The fact's ``{axis: member_label}``.
        member_hints: The lookup's member hints.

    Returns:
        True when every hint matches a different remaining label and none is left over.
    """
    hints = [_norm_label(h) for h in member_hints]
    if not hints:
        return False
    labels = [
        _norm_label(label)
        for axis, label in (dimension_labels or {}).items()
        # A neutral member drops out unless a hint names it.
        if not (
            is_neutral_dimension(axis, label)
            and not any(_hint_in_label(h, _norm_label(label)) for h in hints)
        )
    ]
    if len(labels) != len(hints):
        return False

    # Bipartite matching (hints <-> labels); sizes are tiny, so augmenting paths suffice.
    owner: dict[int, int] = {}

    def assign(h: int, seen: set[int]) -> bool:
        for j, label in enumerate(labels):
            if j in seen or not _hint_in_label(hints[h], label):
                continue
            seen.add(j)
            if j not in owner or assign(owner[j], seen):
                owner[j] = h
                return True
        return False

    return all(assign(h, set()) for h in range(len(hints)))


# Normalised, space-free spellings of srt:OperatingSegmentsMember: its label, or the raw
# local name when the filer's label linkbase has no entry for it (AMD FY2025).
_NEUTRAL_MEMBER_FORMS = frozenset({"operatingsegments", "operatingsegmentsmember"})


def _like_escape(text: str) -> str:
    """Escape LIKE wildcards so a hint matches literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _hint_in_label(hint: str, label: str) -> bool:
    """Containment of a normalised hint in a normalised label, also with spaces removed:
    a label that is a raw local name ("clientandgamingmember") still contains
    "client and gaming"."""
    return hint in label or hint.replace(" ", "") in label.replace(" ", "")


def _unit_observation(row: dict) -> tuple[str | None, str | None]:
    """A store row as ``(shown unit, period end)``, in the form candidates carry."""
    end = row.get("period_end")
    unit = row.get("unit")
    return (display_unit(unit) if unit else None, None if end is None else str(end))


def _requested_unit_key(requested_unit: str | None, units: set[str]) -> str | None:
    """A requested unit in the shown form of the candidates' units.

    Args:
        requested_unit: The step's ``unit`` (an ISO 4217 code, or a per-share unit in
            any spelling), or None.
        units: The candidates' units, each in ``display_unit`` form.

    Returns:
        None when nothing was requested; ``<code>/shares`` for a currency code when the
        candidates carry that per-share unit and not the bare code; otherwise the
        requested unit in shown form.
    """
    if requested_unit is None:
        return None
    shown = display_unit(requested_unit) or requested_unit
    per_share = f"{shown}/shares"
    if shown not in units and per_share in units:
        return per_share
    return shown


if __name__ == "__main__":
    lookup = XbrlFactstoreLookup.from_env()
    examples = [
        {
            "id": "tot26",
            "op": "lookup",
            "cik": "0001045810",
            "ticker": "NVDA",
            "fy": 2026,
            "concept_family": "Revenue",
            "dimensions": "NONE",
            "section_hint": "Income Statement",
        },
        {
            "id": "dc26",
            "op": "lookup",
            "cik": "0001045810",
            "ticker": "NVDA",
            "fy": 2026,
            "concept_family": "Revenue",
            "dimensions": {"product": "data center"},
            "section_hint": "Disaggregated Revenue",
        },
    ]
    for step in examples:
        print(json.dumps(lookup.lookup(step).to_dict(), indent=2, default=str))
