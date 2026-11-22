"""Populate XbrlRag Layer-0 tables from local data files.

Layer-0 tables are XbrlRag-owned INSERT targets that the Arelle xbrlDB plugin
never writes.  They feed every downstream materialized view (mv_financial_facts,
mv_concept_coverage, etc.).

Tables handled by this module:

* ``filing_registry``  — canonical filing tracker (from manifest.yaml files)
* ``fiscal_calendar``  — fiscal period → exact date bounds (from manifest.yaml)
* ``concept_mapping``          — concept-family → QName preference list (from CSV)
* ``geography_normalization``  — member QName → ISO code / geo_type (from CSV)

Connection parameters are read from environment variables (loaded from .env):

    XBRL_DB_HOST      default: localhost
    XBRL_DB_PORT      default: 5432
    XBRL_DB_NAME      default: xbrl_db
    XBRL_DB_USER      default: postgres
    XBRL_DB_PASSWORD  default: postgres

Usage (batch — all three tables)::

    python -m src.ingest.sec_edgar.ingest

Usage (concept_mapping only)::

    python -m src.ingest.sec_edgar.ingest \\
        --concept-mapping-csv data/storage/datasets/hifikpi/concept_mapping_depth0.csv

Schema source: scripts/ddl/layer_0/
"""

from __future__ import annotations

import collections.abc
import csv
import dataclasses
import json
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import yaml
from dotenv import load_dotenv

from src.eval.sec_reference.fsds import load_tables
from src.ingest.sec_edgar.fiscal_labels import (
    ANNUAL_FORMS,
    QUARTERLY_FORMS,
    assign_fiscal_labels,
    place_quarter,
)
from src.ingest.sec_edgar.ingest import extension_classifications as ec
from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import (
    is_accession_number,
    pick_accession_row,
)
from src.ingest.sec_edgar.report_period import report_period_bounds
from src.ingest.sec_edgar.storage import default_local_root, local_root
from src.ingest.sec_edgar.xbrl.extension_classifier import (
    classify_extensions,
    extension_mapping_rows,
)
from src.ingest.util.generate_concept_mapping import (
    committed_mapping_rows,
    load_family_coverage,
)
from src.observability.trace_context import log_event

logger = logging.getLogger(__name__)

# Annual form bases: no quarter, fiscal_period = 'FY'
_ANNUAL_BASE_FORMS = frozenset({"10-K", "20-F"})
_QUARTERLY_BASE_FORMS = frozenset({"10-Q"})

_ROLES = frozenset({"primary", "substitute", "component"})

# Values in the concept_mapping CSV that map to Python True/False
_BOOL_TRUE = frozenset({"true", "1", "yes", "t"})
_BOOL_FALSE = frozenset({"false", "0", "no", "f", ""})

# ---------------------------------------------------------------------------
# concept_mapping qname format note
# ---------------------------------------------------------------------------
# concept_mapping.qname stores the SHORT prefix format: "us-gaap:Revenues"
# xbrlDB qname table stores the FULL URI format:        "http://fasb.org/us-gaap/2025:Revenues"
# The materialized view join (qname = q.namespace||':'||q.local_name) will NOT
# match until concept_mapping is re-seeded in full-URI format.  Until then,
# this module uses the prefix-to-namespace-base map below to do the matching
# in Python for the per-filer override detection.
# TODO: regenerate concept_mapping CSV in full-URI format and re-seed.

# Maps the short taxonomy prefix used in concept_mapping.qname to the
# namespace base URI used in xbrlDB qname.namespace.
# The base URI may contain a version year suffix (e.g. /2025) which varies
# per filing; startswith() matching handles that.
_PREFIX_TO_NS_BASE: dict[str, tuple[str, ...]] = {
    "us-gaap": ("http://fasb.org/us-gaap/",),
    "dei": ("http://xbrl.sec.gov/dei/",),
    "srt": ("http://fasb.org/srt/",),
    "ifrs-full": ("http://xbrl.ifrs.org/taxonomy/", "https://xbrl.ifrs.org/taxonomy/"),
    "country": ("http://xbrl.sec.gov/country/",),
    "exch": ("http://xbrl.sec.gov/exch/",),
    "ecd": ("http://xbrl.sec.gov/ecd/",),
    "stpr": ("http://xbrl.sec.gov/stpr/",),
}


def _parse_prefixed_qname(qname: str) -> tuple[str, str, tuple[str, ...]] | None:
    """Parse 'us-gaap:Revenues' → (prefix, local_name, ns_bases) or None.

    Returns None for unknown prefixes (e.g. filer extension qnames that
    are not in _PREFIX_TO_NS_BASE — those are already filer-specific and
    don't need override detection against global ranks).
    """
    if ":" not in qname:
        return None
    prefix, local = qname.split(":", 1)
    ns_bases = _PREFIX_TO_NS_BASE.get(prefix)
    if not ns_bases:
        return None
    return prefix, local, ns_bases


# Valid EDGAR accession number: 10-digit CIK, 2-digit year, 6-digit sequence
# e.g. "0001045810-25-000023".  Anything else is a default/placeholder value
# written by Arelle when it cannot derive the accession number from the file URL.
_EDGAR_ACCNO_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")

# The taxonomies whose short-form concept_mapping rows get namespace-expanded copies,
# and the namespace bases each is copied to (docs/specs/ns-expand-rank-sync.md).
# Narrower than _PREFIX_TO_NS_BASE: country/exch/ecd/stpr rows get no copies.
NS_EXPANDED_SOURCE = "ns_expanded"
# The prefix of a filer-extension QName in concept_mapping (`ext:<LocalName>`, always
# cik-scoped); it expands to every extension namespace with that local name
# (docs/specs/concept-family-coverage.md).
EXTENSION_PREFIX = "ext"
# Namespaces of standard taxonomies (never a filer extension).
_STANDARD_NS_BASES: tuple[str, ...] = (
    "http://fasb.org/",
    "http://xbrl.sec.gov/",
    "http://www.xbrl.org/",
    "http://xbrl.ifrs.org/",
    "https://xbrl.ifrs.org/",
)
_NS_EXPAND_BASES: dict[str, tuple[str, ...]] = {
    "us-gaap": ("http://fasb.org/us-gaap/",),
    "srt": ("http://fasb.org/srt/",),
    "dei": ("http://xbrl.sec.gov/dei/",),
    "ifrs-full": ("http://xbrl.ifrs.org/taxonomy/", "https://xbrl.ifrs.org/taxonomy/"),
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ManifestRecord:
    """Parsed content of a single manifest.yaml file."""

    ticker: str
    cik: str  # zero-padded to 10 digits
    accession_no: str
    form_type: str  # resolved form e.g. '10-K', '10-K/A', '20-F'
    filing_type: str  # requested base form e.g. '10-K', '20-F'
    is_amendment: bool
    fiscal_year: int
    quarter: int | None
    filed_date: date
    period_start: date | None
    period_end: date
    fye_month: int | None
    fye_day: int | None
    primary_document: str | None = None  # primary iXBRL .htm filename

    fiscal_period: str = field(init=False)  # 'FY' | 'Q1' | 'Q2' | 'Q3'

    def __post_init__(self) -> None:
        if self.filing_type in _QUARTERLY_BASE_FORMS and self.quarter:
            self.fiscal_period = f"Q{self.quarter}"
        else:
            self.fiscal_period = "FY"

    @property
    def base_form(self) -> str:
        return self.form_type[:-2] if self.form_type.endswith("/A") else self.form_type


@dataclass
class GeographyNormalizationRecord:
    """One row from a geography_normalization CSV file."""

    member_qname: str  # full namespaceURI:localName format
    iso_code: str | None  # None for unspecified (typed-dimension placeholders)
    geo_name: str
    geo_type: str  # 'country' | 'region' | 'segment'


@dataclass
class ConceptMappingRecord:
    """One row from a concept_mapping CSV file."""

    concept_family: str
    qname: str
    preference_rank: int
    cik_override: str | None  # None = applies to all filers
    is_deprecated: bool
    source: str  # 'hifi_kpi' | 'reviewed_override' | 'family_coverage' | 'ifrs' | ...
    # docs/specs/concept-family-coverage.md: 'primary' | 'substitute' | 'component'
    # (None: the role the rank implies), and the stated substitution of a substitute.
    concept_role: str | None = None
    substitution_code: str | None = None
    substitution_note: str | None = None


def apply_fiscal_labels(
    records: collections.abc.Iterable[ManifestRecord],
    dei_fiscal_years: collections.abc.Mapping[str, int | None],
) -> list[ManifestRecord]:
    """Copies of ``records`` with each filing's fiscal-year label and, for a 10-Q, quarter.

    Specs: docs/specs/fiscal-label-alignment.md, requirement 7, and
    docs/specs/tenq-fiscal-labels.md, requirement 3: a 10-Q or 10-Q/A record's ``quarter``
    (and so ``fiscal_period``) is the one ``place_quarter`` gives against the filer's
    labelled annual records in the run; a record it cannot place keeps its manifest
    quarter, emitted as a ``decision`` event. Filers are labelled independently; nothing but
    ``fiscal_year`` and ``quarter`` differs, and the inputs are not changed.

    Args:
        records: The parsed manifests, of any number of filers.
        dei_fiscal_years: Accession → DEI ``DocumentFiscalYearFocus``; a filing missing
            here counts as having no DEI.

    Returns:
        One new record per input record, in input order.
    """
    rows = list(records)
    by_cik: dict[str, list[ManifestRecord]] = {}
    for r in rows:
        by_cik.setdefault(r.cik, []).append(r)
    labels: dict[tuple[str, str], int] = {}
    quarters: dict[tuple[str, str], int] = {}
    for cik, group in by_cik.items():
        assigned = assign_fiscal_labels(
            {
                "accession_no": r.accession_no,
                "form_type": r.form_type,
                "period_end": r.period_end,
                "filed_date": r.filed_date,
                "dei_fiscal_year": dei_fiscal_years.get(r.accession_no),
            }
            for r in group
        )
        labels.update({(cik, accession): y for accession, y in assigned.items()})
        annual_periods = {
            r.period_end: assigned[r.accession_no] for r in group if r.form_type in ANNUAL_FORMS
        }
        for r in group:
            if r.form_type not in QUARTERLY_FORMS:
                continue
            placed = place_quarter(r.period_end, annual_periods)
            if placed is not None:
                quarters[(cik, r.accession_no)] = placed[1]
            else:
                log_event("decision", decision="tenq_unplaced_manifest_quarter",
                          source="registries", accession_no=r.accession_no,
                          period_end=r.period_end.isoformat(), quarter=r.quarter)
    return [
        dataclasses.replace(
            r,
            fiscal_year=labels[(r.cik, r.accession_no)],
            quarter=quarters.get((r.cik, r.accession_no), r.quarter),
        )
        for r in rows
    ]


def stale_fiscal_calendar_keys(
    existing: collections.abc.Iterable[tuple[str, int, str]],
    records: collections.abc.Iterable[ManifestRecord],
) -> set[tuple[str, int, str]]:
    """The ``fiscal_calendar`` rows a relabelling leaves behind (requirement 8 of the spec).

    Args:
        existing: The table's current ``(cik, fiscal_year, fiscal_period)`` keys.
        records: The labelled records of this run.

    Returns:
        The existing keys of a filer in ``records`` that no record produces.
    """
    produced = {(r.cik, r.fiscal_year, r.fiscal_period) for r in records}
    ciks = {cik for cik, _year, _period in produced}
    return {key for key in existing if key[0] in ciks and key not in produced}


def has_primary_statements(concept_families: collections.abc.Iterable[str | None]) -> bool:
    """Whether a filing's facts include the primary financial statements.

    True iff the families include ``TotalAssets`` and at least one of ``Revenue`` or
    ``NetIncome`` (docs/specs/annual-amendment-handling.md).

    Args:
        concept_families: The concept family of each of the filing's facts; ``None``
            for a fact whose concept maps to no family.

    Returns:
        True when the balance sheet and an income-statement total are present.
    """
    present = {f for f in concept_families if f is not None}
    return "TotalAssets" in present and bool({"Revenue", "NetIncome"} & present)


def resolve_supersession(
    group: collections.abc.Sequence[ManifestRecord],
    full_statement: collections.abc.Mapping[str, bool],
) -> dict[str, str | None]:
    """Choose the canonical filing of one (cik, fiscal year, fiscal period).

    An amendment becomes canonical only when it carries the primary statements; a
    Part III-only amendment never displaces the original
    (docs/specs/annual-amendment-handling.md).

    Args:
        group: The period's filings.
        full_statement: Accession → whether that filing carries the primary
            statements; a missing amendment counts as False.

    Returns:
        Accession → new ``superseded_by``: ``None`` for the canonical filing, the
        canonical accession for every other.
    """
    if not group:
        return {}
    eligible = [
        r
        for r in group
        if not r.is_amendment or full_statement.get(r.accession_no, False)
    ]
    canonical = max(eligible or group, key=lambda r: (r.filed_date, r.accession_no))
    return {
        r.accession_no: (None if r is canonical else canonical.accession_no)
        for r in group
    }


def filer_ranking(
    global_ranking: collections.abc.Sequence[tuple[str, int]],
    reported: collections.abc.Collection[str],
) -> list[tuple[str, int]] | None:
    """One filer's ranking of a concept family (docs/specs/concept-rank-integrity.md).

    Args:
        global_ranking: The family's generic ``(qname, preference_rank)`` pairs.
        reported: The QNames the filer reports undimensioned.

    Returns:
        The filer's full ``(qname, rank)`` ranking, or None when the global ranking
        already puts the filer's first reported eligible QName first.
    """
    ordered = sorted(global_ranking, key=lambda qr: (qr[1], qr[0]))
    eligible = [q for q, r in ordered if _rank_is_eligible(r)]
    winner = next((q for q in eligible if q in reported), None)
    if winner is None or winner == eligible[0]:
        return None
    rest = [q for q in eligible if q != winner]
    ranking = [(winner, 1)] + [(q, rank) for rank, q in enumerate(rest, start=2)]
    return ranking + [(q, r) for q, r in ordered if not _rank_is_eligible(r)]


# Rank bands of concept_mapping (docs/specs/concept-rank-integrity.md): components sit
# in [50, 100) and are never promoted; IFRS rows (100+) stay eligible.
_COMPONENT_BAND = range(50, 100)


# Per-filer rankings reviewed by hand (configs/concept_mapping/
# concept_family_overrides.yaml); the facts-count override pass never touches them.
REVIEWED_OVERRIDE_SOURCE = "reviewed_override"
# cik-scoped members of configs/concept_mapping/concept_family_coverage.yaml
# (docs/specs/concept-family-coverage.md); like a reviewed ranking, never touched by the
# facts-count override pass.
FAMILY_COVERAGE_SOURCE = "family_coverage"
_PROTECTED_SOURCES = (FAMILY_COVERAGE_SOURCE, REVIEWED_OVERRIDE_SOURCE)


def _rank_is_eligible(rank: int) -> bool:
    return rank not in _COMPONENT_BAND


def consistent_concepts(
    years_reported: collections.abc.Mapping[str, collections.abc.Collection[int]],
    eligible: collections.abc.Collection[str],
) -> set[str]:
    """The eligible QNames a filer reports in the most fiscal years.

    See docs/specs/filer-rank-coverage.md.

    Args:
        years_reported: QName → the fiscal years in which the filer reports it.
        eligible: The QNames that may become the filer's rank 1.

    Returns:
        Every eligible QName tied at the largest number of years; empty when none
        has any year.
    """
    counts = {q: len(set(years_reported.get(q, ()))) for q in eligible}
    best = max(counts.values(), default=0)
    return {q for q, n in counts.items() if n == best} if best else set()


def facts_count_ranking(
    global_ranking: collections.abc.Sequence[tuple[str, int]],
    years_reported: collections.abc.Mapping[str, collections.abc.Collection[int]],
    fallback: collections.abc.Collection[str],
) -> list[tuple[str, int]] | None:
    """One filer's facts-count ranking of a family, a fallback-only QName never first.

    See docs/specs/screen-candidate-order.md, requirement 3: among the eligible QNames
    (rank outside the component band) that are not fallback-only, those the filer reports
    in the most fiscal years tie, and the first of them in global rank order wins
    (docs/specs/filer-rank-coverage.md). A fallback-only QName keeps its global place
    behind the winner. With an empty ``fallback`` this is the facts-count rule unchanged.

    Args:
        global_ranking: The family's generic ``(qname, preference_rank)`` pairs.
        years_reported: QName -> the fiscal years in which the filer reports it.
        fallback: The family's fallback-only QNames (the coverage file's ``fallback``).

    Returns:
        The filer's full ``(qname, rank)`` ranking, or None when the global ranking stands
        (nothing reported, or the winner is already the global first eligible QName).
    """
    choosable = [
        q for q, r in global_ranking if _rank_is_eligible(r) and q not in fallback
    ]
    return filer_ranking(global_ranking, consistent_concepts(years_reported, choosable))


class NsExpandedDriftError(ValueError):
    """An ``ns_expanded`` concept_mapping row disagrees with its base row, or has none.

    See docs/specs/ns-expand-rank-sync.md.
    """


def expand_concept_mapping(
    rows: collections.abc.Iterable[ConceptMappingRecord],
    known_qnames: collections.abc.Iterable[tuple[str, str]],
) -> list[ConceptMappingRecord]:
    """Derive the ``ns_expanded`` rows from the base rows (docs/specs/ns-expand-rank-sync.md).

    Args:
        rows: concept_mapping rows. ``ns_expanded`` rows and full-namespace QNames are
            ignored; every other row is a base row.
        known_qnames: ``(namespace, local_name)`` pairs of the ``qname`` table.

    Returns:
        One ``ns_expanded`` row per base row and matching known QName, carrying the base
        row's family, rank, cik_override and is_deprecated, sorted by
        ``(concept_family, qname, cik_override or "")``.
    """
    by_local: dict[str, list[str]] = {}
    for namespace, local_name in sorted(set(known_qnames)):
        by_local.setdefault(local_name, []).append(namespace)
    out: list[ConceptMappingRecord] = []
    for row in rows:
        parsed = _expandable_base(row)
        if parsed is None:
            continue
        prefix, local = parsed
        out.extend(
            dataclasses.replace(
                row, qname=f"{namespace}:{local}", source=NS_EXPANDED_SOURCE
            )
            for namespace in by_local.get(local, ())
            if _namespace_matches(prefix, namespace)
        )
    return sorted(out, key=_mapping_key)


def _namespace_matches(prefix: str, namespace: str) -> bool:
    """Whether ``namespace`` is one of the taxonomy ``prefix`` stands for.

    ``ext`` stands for any filer extension namespace: one of no standard taxonomy. Its
    copies stay cik-scoped, so another filer's same-named extension never matches a
    fact (docs/specs/concept-family-coverage.md).
    """
    if prefix == EXTENSION_PREFIX:
        return not namespace.startswith(_STANDARD_NS_BASES)
    return namespace.startswith(_NS_EXPAND_BASES[prefix])


def ns_expanded_drift(
    rows: collections.abc.Iterable[ConceptMappingRecord],
) -> list[tuple[ConceptMappingRecord, ConceptMappingRecord | None]]:
    """The ``ns_expanded`` rows out of step with their base row (docs/specs/ns-expand-rank-sync.md).

    Args:
        rows: Every concept_mapping row.

    Returns:
        ``(expanded, base)`` for each ``ns_expanded`` row whose base row differs in
        preference_rank or is_deprecated, and ``(expanded, None)`` for each with no base
        row, sorted by the expanded row's ``(concept_family, qname, cik_override or "")``.
    """
    all_rows = list(rows)
    bases: dict[tuple[str, str, str, str | None], ConceptMappingRecord] = {}
    for row in all_rows:
        parsed = _expandable_base(row)
        if parsed is not None:
            bases[(row.concept_family, parsed[0], parsed[1], row.cik_override)] = row
    drift: list[tuple[ConceptMappingRecord, ConceptMappingRecord | None]] = []
    for row in all_rows:
        if row.source != NS_EXPANDED_SOURCE:
            continue
        owner = _base_key_of_expanded(row)
        base = bases.get(owner) if owner is not None else None
        if base is None:
            drift.append((row, None))
        elif _synced_fields(base) != _synced_fields(row):
            drift.append((row, base))
    return sorted(drift, key=lambda pair: _mapping_key(pair[0]))


def assert_ns_expanded_in_sync(
    rows: collections.abc.Iterable[ConceptMappingRecord],
) -> int:
    """Fail loud when any ``ns_expanded`` row drifted (docs/specs/ns-expand-rank-sync.md).

    Args:
        rows: Every concept_mapping row.

    Returns:
        The number of ``ns_expanded`` rows checked.

    Raises:
        NsExpandedDriftError: ``ns_expanded_drift(rows)`` is not empty; the message names
            each drifted row's family and QName.
    """
    all_rows = list(rows)
    drift = ns_expanded_drift(all_rows)
    if drift:
        lines = []
        for expanded, base in drift:
            where = (
                f"{expanded.concept_family} {expanded.qname} "
                f"cik_override={expanded.cik_override or '-'}"
            )
            if base is None:
                lines.append(f"  {where}: no base row (orphan)")
            else:
                lines.append(
                    f"  {where}: rank {expanded.preference_rank}, deprecated "
                    f"{expanded.is_deprecated}; base {base.qname} has rank "
                    f"{base.preference_rank}, deprecated {base.is_deprecated}"
                )
        raise NsExpandedDriftError(
            f"{len(drift)} ns_expanded concept_mapping row(s) out of step with their "
            "base rows (docs/specs/ns-expand-rank-sync.md):\n" + "\n".join(lines)
        )
    return sum(1 for r in all_rows if r.source == NS_EXPANDED_SOURCE)


def _synced_fields(row: ConceptMappingRecord) -> tuple[Any, ...]:
    """The fields an ``ns_expanded`` copy carries from its base row."""
    return (
        row.preference_rank,
        row.is_deprecated,
        row.concept_role,
        row.substitution_code,
        row.substitution_note,
    )


def _mapping_key(row: ConceptMappingRecord) -> tuple[str, str, str]:
    """concept_mapping's unique key: ``(concept_family, qname, cik_override or "")``."""
    return (row.concept_family, row.qname, row.cik_override or "")


def _expandable_base(row: ConceptMappingRecord) -> tuple[str, str] | None:
    """``(prefix, local_name)`` of a base row that gets namespace copies, else None.

    ``ns_expanded`` rows, full-namespace QNames and prefixes outside
    ``_NS_EXPAND_BASES`` get none.
    """
    if row.source == NS_EXPANDED_SOURCE or row.qname.startswith(("http://", "https://")):
        return None
    prefix, sep, local = row.qname.partition(":")
    if not sep or not local:
        return None
    if prefix == EXTENSION_PREFIX:
        # A filer extension is a member only for its filer.
        return (prefix, local) if row.cik_override else None
    if prefix not in _NS_EXPAND_BASES:
        return None
    return prefix, local


def _base_key_of_expanded(
    row: ConceptMappingRecord,
) -> tuple[str, str, str, str | None] | None:
    """``(family, prefix, local_name, cik_override)`` of the base row a copy derives from.

    The prefix is the taxonomy whose namespace base the copy's namespace starts with;
    None when no taxonomy matches.
    """
    namespace, sep, local = row.qname.rpartition(":")
    if not sep:
        return None
    for prefix, ns_bases in _NS_EXPAND_BASES.items():
        if namespace.startswith(ns_bases):
            return (row.concept_family, prefix, local, row.cik_override)
    if row.cik_override and not namespace.startswith(_STANDARD_NS_BASES):
        return (row.concept_family, EXTENSION_PREFIX, local, row.cik_override)
    return None


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class Layer0Populator:
    """Populate XbrlRag Layer-0 tables from local data files.

    Parameters
    ----------
    sec_edgar_root:
        Root directory containing the ``<TICKER>/<FY>/<FORM>/manifest.yaml``
        tree written by :class:`SECFilingDownloader`.
    db_conn_params:
        psycopg2 connection keyword arguments
        (``host``, ``port``, ``dbname``, ``user``, ``password``).
    """

    DEFAULT_SEC_ROOT = default_local_root("sec_edgar")

    def __init__(
        self,
        sec_edgar_root: str | Path = DEFAULT_SEC_ROOT,
        db_conn_params: dict | None = None,
    ) -> None:
        self.sec_edgar_root = Path(sec_edgar_root).expanduser().resolve()
        self._db_conn_params = db_conn_params or {}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        sec_edgar_root: str | Path | None = None,
        dotenv_path: str | Path | None = None,
    ) -> Layer0Populator:
        """Construct from environment variables loaded from .env.

        Reads:
            XBRL_DB_HOST, XBRL_DB_PORT, XBRL_DB_NAME,
            XBRL_DB_USER, XBRL_DB_PASSWORD, SEC_EDGAR_ROOT
        """
        load_dotenv(dotenv_path)

        conn_params = {
            "host": os.environ.get("XBRL_DB_HOST", "localhost"),
            "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user": os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        root = sec_edgar_root or local_root("sec_edgar")
        return cls(sec_edgar_root=root, db_conn_params=conn_params)

    # ------------------------------------------------------------------
    # Public API — manifests (filing_registry + fiscal_calendar)
    # ------------------------------------------------------------------

    def populate_registries(self, dry_run: bool = False) -> dict:
        """Walk manifest.yaml files and upsert filing_registry + fiscal_calendar.

        Both tables carry each filing's fiscal-year label, not the manifest's calendar
        year (docs/specs/fiscal-label-alignment.md). A manifest whose facts are not
        loaded yet gets its calendar year until the next registries run. ``dry_run``
        opens no connection, so it parses the manifests without labelling them.

        Returns a stats dict with keys:
            manifests_found, manifests_ok, manifests_skipped,
            filing_registry_upserted, fiscal_calendar_upserted,
            fiscal_calendar_stale_deleted, superseded_updated, errors
        """
        stats: dict = {
            "manifests_found": 0,
            "manifests_ok": 0,
            "manifests_skipped": 0,
            "filing_registry_upserted": 0,
            "fiscal_calendar_upserted": 0,
            "fiscal_calendar_stale_deleted": 0,
            "superseded_updated": 0,
            "errors": [],
        }

        records: list[ManifestRecord] = []
        for path in self._find_manifests():
            stats["manifests_found"] += 1
            try:
                records.append(self._parse_manifest(path))
                stats["manifests_ok"] += 1
            except Exception as exc:
                msg = f"Skipped {path}: {exc}"
                logger.warning(msg)
                stats["manifests_skipped"] += 1
                stats["errors"].append(msg)

        logger.info(
            "Parsed %d manifests (%d ok, %d skipped).",
            stats["manifests_found"],
            stats["manifests_ok"],
            stats["manifests_skipped"],
        )

        if dry_run:
            logger.info("dry_run=True -- no database writes.")
            self._log_manifest_summary(records)
            return stats

        with psycopg2.connect(**self._db_conn_params) as conn:
            records = apply_fiscal_labels(
                records, self._dei_fiscal_years(conn, [r.accession_no for r in records])
            )
            stats["filing_registry_upserted"] = self._upsert_filing_registry(
                conn, records
            )
            stats["fiscal_calendar_stale_deleted"] = self._delete_stale_fiscal_calendar(
                conn, records
            )
            stats["fiscal_calendar_upserted"] = self._upsert_fiscal_calendar(
                conn, records
            )
            stats["superseded_updated"] = self._resolve_superseded_by(conn, records)
            conn.commit()

        logger.info(
            "Registries done: filing_registry=%d, fiscal_calendar=%d "
            "(%d stale deleted), superseded=%d.",
            stats["filing_registry_upserted"],
            stats["fiscal_calendar_upserted"],
            stats["fiscal_calendar_stale_deleted"],
            stats["superseded_updated"],
        )
        return stats

    # ------------------------------------------------------------------
    # Public API — concept_mapping
    # ------------------------------------------------------------------

    def populate_concept_mapping(
        self,
        csv_path: str | Path,
        mode: str = "upsert",
        dry_run: bool = False,
    ) -> dict:
        """Load concept_mapping rows from a CSV file into the database.

        In every mode, the write is followed in the same transaction by a
        re-derivation of every ``ns_expanded`` row from the base rows and the drift
        check (docs/specs/ns-expand-rank-sync.md); drift raises
        :class:`NsExpandedDriftError` and nothing is committed. A dry run writes
        nothing and does neither.

        Parameters
        ----------
        csv_path:
            Path to the CSV file.  Must have a header row with columns:
            concept_family, qname, preference_rank, cik_override,
            is_deprecated, source.
            Empty string in cik_override is treated as SQL NULL.
            is_deprecated accepts 'True'/'False' (case-insensitive) or '0'/'1'.
        mode:
            'upsert'      — INSERT ... ON CONFLICT DO UPDATE.  Existing rows
                            for the same (concept_family, qname, cik_override)
                            are refreshed with the CSV values.  Default.
            'insert_new'  — INSERT ... ON CONFLICT DO NOTHING.  Existing rows
                            are preserved unchanged; only new keys are added.
            'replace_source' — DELETE all rows where source matches the source
                            value in the CSV, then INSERT.  Use this for a
                            full regeneration of a single source tier
                            (e.g. reload all 'hifi_kpi' rows after re-mining).

        Returns
        -------
        dict with keys:
            csv_path, rows_parsed, rows_ok, rows_skipped,
            rows_written, mode, dry_run, errors, and ``ns_expanded`` (the
            re-derivation stats) when rows were written
        """
        csv_path = Path(csv_path).expanduser().resolve()
        valid_modes = ("upsert", "insert_new", "replace_source")
        if mode not in valid_modes:
            raise ValueError(f"mode must be one of {valid_modes}; got {mode!r}")

        stats: dict = {
            "csv_path": str(csv_path),
            "rows_parsed": 0,
            "rows_ok": 0,
            "rows_skipped": 0,
            "rows_written": 0,
            "mode": mode,
            "dry_run": dry_run,
            "errors": [],
        }

        records = self._parse_concept_mapping_csv(csv_path, stats)
        logger.info(
            "Concept mapping CSV: %d rows parsed (%d ok, %d skipped) from %s.",
            stats["rows_parsed"],
            stats["rows_ok"],
            stats["rows_skipped"],
            csv_path.name,
        )

        if dry_run:
            logger.info("dry_run=True -- no database writes.")
            self._log_concept_mapping_summary(records)
            return stats

        if not records:
            logger.warning("No valid rows to write; aborting.")
            return stats

        with psycopg2.connect(**self._db_conn_params) as conn:
            stats["rows_written"] = self._write_concept_mapping(conn, records, mode)
            stats["ns_expanded"] = self._sync_ns_expanded(conn)
            conn.commit()

        logger.info(
            "concept_mapping done: %d rows written (mode=%s).",
            stats["rows_written"],
            mode,
        )
        return stats

    # ------------------------------------------------------------------
    # Manifest discovery and parsing
    # ------------------------------------------------------------------

    def _find_manifests(self) -> Iterator[Path]:
        if not self.sec_edgar_root.exists():
            raise FileNotFoundError(f"SEC EDGAR root not found: {self.sec_edgar_root}")
        yield from sorted(self.sec_edgar_root.rglob("manifest.yaml"))

    def _parse_manifest(self, path: Path) -> ManifestRecord:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        def _req(key: str):
            val = raw.get(key)
            if val is None:
                raise ValueError(f"Missing required field '{key}' in {path}")
            return val

        def _date(key: str, required: bool = True) -> date | None:
            val = raw.get(key)
            if val is None:
                if required:
                    raise ValueError(f"Missing required date '{key}' in {path}")
                return None
            if isinstance(val, date):
                return val
            try:
                return date.fromisoformat(str(val))
            except ValueError:
                raise ValueError(f"Cannot parse date '{val}' for '{key}' in {path}")

        cik_padded = str(_req("cik")).strip().lstrip("0").zfill(10)
        form_type = str(_req("form")).upper().strip()
        filing_type = str(_req("filing_type")).upper().strip()

        primary_doc = raw.get("primary_document")
        if primary_doc:
            primary_doc = Path(str(primary_doc)).name  # keep filename only

        # The statement period of the saved instance wins over the manifest's dates, so an
        # old manifest is corrected without a re-download (docs/specs/tenq-fiscal-labels.md,
        # requirement 7).
        period_start = _date("period_start", required=False)
        period_end = _date("period_end")
        if period_end is None:  # _date raises first for a required key; this narrows the type
            raise ValueError(f"Missing required date 'period_end' in {path}")
        instance_name = next(
            (str(f) for f in raw.get("files") or [] if str(f).endswith("_htm.xml")), None
        )
        if instance_name is not None:
            instance_path = path.parent / Path(instance_name).name
            if instance_path.is_file():
                bounds = report_period_bounds(instance_path.read_bytes(), form_type)
                if bounds is not None:
                    period_start, period_end = bounds

        return ManifestRecord(
            ticker=str(_req("ticker")).upper().strip(),
            cik=cik_padded,
            accession_no=str(_req("accession_no")).strip(),
            form_type=form_type,
            filing_type=filing_type,
            is_amendment=bool(raw.get("is_amendment", False)),
            fiscal_year=int(_req("fiscal_year")),
            quarter=int(raw["quarter"]) if raw.get("quarter") is not None else None,
            filed_date=_date("filing_date"),
            period_start=period_start,
            period_end=period_end,
            fye_month=(
                int(raw["fye_month"]) if raw.get("fye_month") is not None else None
            ),
            fye_day=int(raw["fye_day"]) if raw.get("fye_day") is not None else None,
            primary_document=primary_doc,
        )

    # ------------------------------------------------------------------
    # Concept mapping CSV parsing
    # ------------------------------------------------------------------

    def _parse_concept_mapping_csv(
        self, csv_path: Path, stats: dict
    ) -> list[ConceptMappingRecord]:
        """Parse CSV into ConceptMappingRecord list, collecting per-row errors."""
        if not csv_path.exists():
            raise FileNotFoundError(f"Concept mapping CSV not found: {csv_path}")

        records: list[ConceptMappingRecord] = []
        required_cols = {
            "concept_family",
            "qname",
            "preference_rank",
            "cik_override",
            "is_deprecated",
            "source",
        }

        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not required_cols.issubset(set(reader.fieldnames or [])):
                missing = required_cols - set(reader.fieldnames or [])
                raise ValueError(
                    f"CSV is missing required columns: {missing}. "
                    f"Found: {reader.fieldnames}"
                )

            for line_no, row in enumerate(reader, start=2):  # line 1 = header
                stats["rows_parsed"] += 1
                try:
                    rec = self._parse_concept_mapping_row(row, line_no)
                    records.append(rec)
                    stats["rows_ok"] += 1
                except Exception as exc:
                    msg = f"Row {line_no}: {exc}"
                    logger.warning(
                        "concept_mapping CSV %s — skipping: %s", csv_path.name, msg
                    )
                    stats["rows_skipped"] += 1
                    stats["errors"].append(msg)

        return records

    @staticmethod
    def _parse_concept_mapping_row(
        row: dict[str, str], line_no: int
    ) -> ConceptMappingRecord:
        """Convert a raw CSV row dict into a ConceptMappingRecord.

        Rules:
        * Empty string in cik_override -> None (SQL NULL).
        * is_deprecated accepts 'True'/'False', 'true'/'false', '1'/'0'.
        * preference_rank must be a positive integer.
        """
        concept_family = row["concept_family"].strip()
        qname = row["qname"].strip()
        if not concept_family:
            raise ValueError("concept_family is empty")
        if not qname:
            raise ValueError("qname is empty")

        try:
            preference_rank = int(row["preference_rank"].strip())
        except ValueError:
            raise ValueError(
                f"preference_rank is not an integer: {row['preference_rank']!r}"
            )
        if preference_rank < 1:
            raise ValueError(f"preference_rank must be >= 1; got {preference_rank}")

        # Empty string -> NULL
        cik_raw = row["cik_override"].strip()
        cik_override: str | None = cik_raw if cik_raw else None

        # Boolean conversion
        dep_raw = row["is_deprecated"].strip().lower()
        if dep_raw in _BOOL_TRUE:
            is_deprecated = True
        elif dep_raw in _BOOL_FALSE:
            is_deprecated = False
        else:
            raise ValueError(f"Cannot parse is_deprecated: {row['is_deprecated']!r}")

        source = row["source"].strip()
        if not source:
            raise ValueError("source is empty")

        # Optional columns (docs/specs/concept-family-coverage.md); empty -> NULL.
        role = (row.get("concept_role") or "").strip() or None
        if role is not None and role not in _ROLES:
            raise ValueError(f"concept_role must be one of {sorted(_ROLES)}; got {role!r}")
        code = (row.get("substitution_code") or "").strip() or None
        note = (row.get("substitution_note") or "").strip() or None
        if (role == "substitute") != (code is not None and note is not None):
            raise ValueError("a substitution code and note go with role 'substitute' only")

        return ConceptMappingRecord(
            concept_family=concept_family,
            qname=qname,
            preference_rank=preference_rank,
            cik_override=cik_override,
            is_deprecated=is_deprecated,
            source=source,
            concept_role=role,
            substitution_code=code,
            substitution_note=note,
        )

    # ------------------------------------------------------------------
    # Database writes — filing_registry + fiscal_calendar
    # ------------------------------------------------------------------

    # Each filing's DEI fiscal year, read the way mv_dei_metadata reads it: the MVs are
    # not rebuilt yet at registry time.
    _DEI_FISCAL_YEARS_SQL = """
        SELECT a.filing_accession_number, MAX(f.fact_value)
          FROM fact f
          JOIN accession a   ON a.accession_id = f.accession_id
          JOIN element   el  ON el.element_id  = f.element_id
          JOIN qname     q   ON q.qname_id     = el.qname_id
          JOIN context   ctx ON ctx.context_id = f.context_id
         WHERE a.filing_accession_number = ANY(%(accessions)s)
           AND q.local_name = 'DocumentFiscalYearFocus'
           AND q.namespace LIKE 'http://xbrl.sec.gov/dei/%%'
           AND ctx.entity_scheme = 'http://www.sec.gov/CIK'
         GROUP BY a.filing_accession_number
    """

    def _dei_fiscal_years(
        self, conn: psycopg2.extensions.connection, accessions: list[str]
    ) -> dict[str, int | None]:
        """Each given accession's DEI ``DocumentFiscalYearFocus``.

        Args:
            conn: Open connection to xbrl_db.
            accessions: The accession numbers.

        Returns:
            Accession → DEI fiscal year. A filing with no ingested DEI is absent or None.
        """
        with conn.cursor() as cur:
            cur.execute(self._DEI_FISCAL_YEARS_SQL, {"accessions": accessions})
            return {
                accession: None if year is None else int(year)
                for accession, year in cur.fetchall()
            }

    def _delete_stale_fiscal_calendar(
        self, conn: psycopg2.extensions.connection, records: list[ManifestRecord]
    ) -> int:
        """Delete the ``fiscal_calendar`` rows a relabelling leaves behind.

        The table is keyed on the label and upserted, so the row of a filing's old label
        would otherwise stay. Only the filers in ``records`` are touched.

        Args:
            conn: Open connection to xbrl_db.
            records: The labelled records of this run.

        Returns:
            The number of rows deleted.
        """
        with conn.cursor() as cur:
            cur.execute("SELECT cik, fiscal_year, fiscal_period FROM fiscal_calendar")
            stale = stale_fiscal_calendar_keys(cur.fetchall(), records)
            psycopg2.extras.execute_batch(
                cur,
                """
                DELETE FROM fiscal_calendar
                 WHERE cik = %s AND fiscal_year = %s AND fiscal_period = %s
                """,
                sorted(stale),
                page_size=200,
            )
        return len(stale)

    def _upsert_filing_registry(
        self, conn: psycopg2.extensions.connection, records: list[ManifestRecord]
    ) -> int:
        sql = """
            INSERT INTO filing_registry
                (cik, accession_no, form_type, fiscal_year, fiscal_period,
                 filed_date, period_end)
            VALUES
                (%(cik)s, %(accession_no)s, %(form_type)s, %(fiscal_year)s,
                 %(fiscal_period)s, %(filed_date)s, %(period_end)s)
            ON CONFLICT (cik, accession_no) DO UPDATE
                SET form_type     = EXCLUDED.form_type,
                    filed_date    = EXCLUDED.filed_date,
                    period_end    = EXCLUDED.period_end,
                    fiscal_year   = EXCLUDED.fiscal_year,
                    fiscal_period = EXCLUDED.fiscal_period
        """
        rows = [
            {
                "cik": r.cik,
                "accession_no": r.accession_no,
                "form_type": r.form_type,
                "fiscal_year": r.fiscal_year,
                "fiscal_period": r.fiscal_period,
                "filed_date": r.filed_date,
                "period_end": r.period_end,
            }
            for r in records
        ]
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)
        return len(rows)

    def _upsert_fiscal_calendar(
        self, conn: psycopg2.extensions.connection, records: list[ManifestRecord]
    ) -> int:
        sql = """
            INSERT INTO fiscal_calendar
                (cik, ticker, fiscal_year, fiscal_period,
                 period_start, period_end, fye_month, fye_day, source_concept)
            VALUES
                (%(cik)s, %(ticker)s, %(fiscal_year)s, %(fiscal_period)s,
                 %(period_start)s, %(period_end)s,
                 %(fye_month)s, %(fye_day)s, %(source_concept)s)
            ON CONFLICT (cik, fiscal_year, fiscal_period) DO UPDATE
                SET ticker         = EXCLUDED.ticker,
                    period_start   = EXCLUDED.period_start,
                    period_end     = EXCLUDED.period_end,
                    fye_month      = COALESCE(EXCLUDED.fye_month,  fiscal_calendar.fye_month),
                    fye_day        = COALESCE(EXCLUDED.fye_day,    fiscal_calendar.fye_day),
                    source_concept = EXCLUDED.source_concept
        """
        rows = [
            {
                "cik": r.cik,
                "ticker": r.ticker,
                "fiscal_year": r.fiscal_year,
                "fiscal_period": r.fiscal_period,
                "period_start": r.period_start,
                "period_end": r.period_end,
                "fye_month": r.fye_month,
                "fye_day": r.fye_day,
                "source_concept": "manifest",
            }
            for r in records
            if r.period_start is not None
        ]
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)
        return len(rows)

    # The facts' concept families per filing, restricted to the families that decide
    # has_primary_statements. Mirrors mv_financial_facts' concept_mapping join (a
    # filer-specific mapping wins); the MVs are not rebuilt yet at registry time.
    _STATEMENT_FAMILIES_SQL = """
        SELECT DISTINCT a.filing_accession_number, cm.concept_family
          FROM fact f
          JOIN accession a   ON a.accession_id = f.accession_id
          JOIN element   el  ON el.element_id  = f.element_id
          JOIN qname     q   ON q.qname_id     = el.qname_id
          JOIN context   ctx ON ctx.context_id = f.context_id
          JOIN concept_mapping cm
               ON cm.qname = q.namespace || ':' || q.local_name
              AND (cm.cik_override IS NULL
                   OR cm.cik_override = LPAD(ctx.entity_identifier, 10, '0'))
              AND COALESCE(cm.is_deprecated, FALSE) = FALSE
         WHERE a.filing_accession_number = ANY(%(accessions)s)
           AND cm.concept_family IN ('TotalAssets', 'Revenue', 'NetIncome')
    """

    def _amendments_with_statements(
        self, conn: psycopg2.extensions.connection, accessions: list[str]
    ) -> dict[str, bool]:
        """Whether each amendment's ingested facts carry the primary statements.

        Args:
            conn: Open connection to xbrl_db.
            accessions: The amendments' accession numbers.

        Returns:
            Accession → True when ``has_primary_statements`` holds for its facts. An
            amendment with no ingested facts maps to False, so the original stays
            canonical until the amendment is shown to restate it.
        """
        if not accessions:
            return {}
        families: dict[str, list[str]] = {a: [] for a in accessions}
        with conn.cursor() as cur:
            cur.execute(self._STATEMENT_FAMILIES_SQL, {"accessions": accessions})
            for accession_no, family in cur.fetchall():
                families.setdefault(accession_no, []).append(family)
        return {a: has_primary_statements(fams) for a, fams in families.items()}

    def _resolve_superseded_by(
        self, conn: psycopg2.extensions.connection, records: list[ManifestRecord]
    ) -> int:
        """Mark one canonical filing per period (superseded_by = NULL).

        An amendment is canonical only when its ingested facts carry the primary
        statements; a Part III-only 10-K/A never displaces the 10-K it amends
        (docs/specs/annual-amendment-handling.md). Every other filing of the period
        points at the canonical one.
        """
        groups: dict[tuple, list[ManifestRecord]] = {}
        for r in records:
            groups.setdefault((r.cik, r.fiscal_year, r.fiscal_period), []).append(r)

        full_statement = self._amendments_with_statements(
            conn, [r.accession_no for r in records if r.is_amendment]
        )
        updates: list[dict] = []
        for group in groups.values():
            resolved = resolve_supersession(group, full_statement)
            for rec in group:
                updates.append(
                    {
                        "accession_no": rec.accession_no,
                        "cik": rec.cik,
                        "superseded_by": resolved[rec.accession_no],
                    }
                )

        if not updates:
            return 0

        sql = """
            UPDATE filing_registry
               SET superseded_by = %(superseded_by)s
             WHERE cik = %(cik)s
               AND accession_no = %(accession_no)s
               AND (superseded_by IS DISTINCT FROM %(superseded_by)s)
        """
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, updates, page_size=200)
        return len(updates)

    # ------------------------------------------------------------------
    # Database writes — concept_mapping
    # ------------------------------------------------------------------

    def _write_concept_mapping(
        self,
        conn: psycopg2.extensions.connection,
        records: list[ConceptMappingRecord],
        mode: str,
    ) -> int:
        """Write concept_mapping records using the requested mode."""

        if mode == "replace_source":
            return self._replace_source_concept_mapping(conn, records)

        if mode == "upsert":
            sql = """
                INSERT INTO concept_mapping
                    (concept_family, qname, preference_rank,
                     cik_override, is_deprecated, source,
                     concept_role, substitution_code, substitution_note)
                VALUES
                    (%(concept_family)s, %(qname)s, %(preference_rank)s,
                     %(cik_override)s, %(is_deprecated)s, %(source)s,
                     %(concept_role)s, %(substitution_code)s, %(substitution_note)s)
                ON CONFLICT (concept_family, qname, (COALESCE(cik_override, '')))
                DO UPDATE SET
                    preference_rank   = EXCLUDED.preference_rank,
                    is_deprecated     = EXCLUDED.is_deprecated,
                    source            = EXCLUDED.source,
                    concept_role      = EXCLUDED.concept_role,
                    substitution_code = EXCLUDED.substitution_code,
                    substitution_note = EXCLUDED.substitution_note
            """
        else:  # insert_new
            sql = """
                INSERT INTO concept_mapping
                    (concept_family, qname, preference_rank,
                     cik_override, is_deprecated, source,
                     concept_role, substitution_code, substitution_note)
                VALUES
                    (%(concept_family)s, %(qname)s, %(preference_rank)s,
                     %(cik_override)s, %(is_deprecated)s, %(source)s,
                     %(concept_role)s, %(substitution_code)s, %(substitution_note)s)
                ON CONFLICT (concept_family, qname, (COALESCE(cik_override, '')))
                DO NOTHING
            """

        rows = self._records_to_dicts(records)
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=200)
        return len(rows)

    def _replace_source_concept_mapping(
        self,
        conn: psycopg2.extensions.connection,
        records: list[ConceptMappingRecord],
    ) -> int:
        """Delete all rows for each source tier present in *records*, then INSERT.

        When the CSV carries ``reviewed_override`` rows (docs/specs/
        concept-families-wave1.md), every reviewed row is replaced, so a filer entry
        dropped from the override file disappears with the reload. A reviewed
        (family, cik), old or new, also loses its ``auto_override`` rows, which it
        supersedes, and its ``ns_expanded`` copies. The caller then re-derives every
        ``ns_expanded`` row from the new base rows (:meth:`_sync_ns_expanded`).
        """
        sources = {r.source for r in records}
        rows = self._records_to_dicts(records)

        with conn.cursor() as cur:
            for protected in sorted(set(_PROTECTED_SOURCES) & sources):
                cur.execute(
                    "SELECT DISTINCT concept_family, cik_override FROM concept_mapping"
                    " WHERE source = %s",
                    (protected,),
                )
                pairs = {(f, c) for f, c in cur.fetchall()}
                pairs |= {
                    (r.concept_family, r.cik_override)
                    for r in records
                    if r.source == protected
                }
                for family, cik in sorted(pairs):
                    cur.execute(
                        """
                        DELETE FROM concept_mapping
                         WHERE concept_family = %s
                           AND cik_override   = %s
                           AND source IN ('auto_override', 'ns_expanded')
                        """,
                        (family, cik),
                    )
            for src in sorted(sources):
                cur.execute("DELETE FROM concept_mapping WHERE source = %s", (src,))
                deleted = cur.rowcount
                logger.debug(
                    "replace_source: deleted %d rows for source=%r.", deleted, src
                )
            # A hand-listed extension replaces the classifier's row of the same key
            # (the hand list wins, docs/specs/extension-classification.md R6).
            listed = [r for r in records if r.qname.startswith("ext:") and r.cik_override]
            if listed:
                cur.execute(
                    "DELETE FROM concept_mapping WHERE source = %s AND"
                    " (concept_family, qname, cik_override) IN (SELECT * FROM"
                    " unnest(%s::text[], %s::text[], %s::text[]))",
                    (
                        ec.EXTENSION_CLASSIFIER_SOURCE,
                        [r.concept_family for r in listed],
                        [r.qname for r in listed],
                        [r.cik_override for r in listed],
                    ),
                )

            insert_sql = """
                INSERT INTO concept_mapping
                    (concept_family, qname, preference_rank,
                     cik_override, is_deprecated, source,
                     concept_role, substitution_code, substitution_note)
                VALUES
                    (%(concept_family)s, %(qname)s, %(preference_rank)s,
                     %(cik_override)s, %(is_deprecated)s, %(source)s,
                     %(concept_role)s, %(substitution_code)s, %(substitution_note)s)
            """
            psycopg2.extras.execute_batch(cur, insert_sql, rows, page_size=200)

        return len(rows)

    @staticmethod
    def _records_to_dicts(records: list[ConceptMappingRecord]) -> list[dict]:
        return [
            {
                "concept_family": r.concept_family,
                "qname": r.qname,
                "preference_rank": r.preference_rank,
                "cik_override": r.cik_override,  # None -> NULL
                "is_deprecated": r.is_deprecated,
                "source": r.source,
                "concept_role": r.concept_role,
                "substitution_code": r.substitution_code,
                "substitution_note": r.substitution_note,
            }
            for r in records
        ]

    # ------------------------------------------------------------------
    # concept_mapping namespace expansion (docs/specs/ns-expand-rank-sync.md)
    # ------------------------------------------------------------------

    def sync_ns_expanded(self, dry_run: bool = False) -> dict:
        """Re-derive the ``ns_expanded`` concept_mapping rows and check them, in one transaction.

        The standalone form of :meth:`_sync_ns_expanded`, for ``bootstrap_db.py``'s
        ``layer0-populate`` step and for a re-derivation by hand.

        Args:
            dry_run: Only read: report the drift of the rows as they are, and what a
                re-derivation would write.

        Returns:
            Stats: ``ns_expanded_before``, ``drifted_before``, ``ns_expanded_deleted``,
            ``ns_expanded_inserted``, ``ns_expanded_checked`` and ``dry_run``; a dry
            run adds ``ns_expanded_would_insert``.

        Raises:
            NsExpandedDriftError: The re-derived rows still drift, or a derived row
                would collide with a non-expanded row; the transaction rolls back.
        """
        with psycopg2.connect(**self._db_conn_params) as conn:
            if dry_run:
                rows = self._read_concept_mapping_rows(conn)
                derived = expand_concept_mapping(rows, self._known_qnames(conn, rows))
                stats = {
                    "ns_expanded_before": sum(
                        1 for r in rows if r.source == NS_EXPANDED_SOURCE
                    ),
                    "drifted_before": len(ns_expanded_drift(rows)),
                    "ns_expanded_deleted": 0,
                    "ns_expanded_inserted": 0,
                    "ns_expanded_would_insert": len(derived),
                    "ns_expanded_checked": 0,
                    "dry_run": True,
                }
                logger.info("DRY RUN ns_expanded sync: %s", stats)
                return stats
            stats = self._sync_ns_expanded(conn)
            conn.commit()
        stats["dry_run"] = False
        return stats

    # ------------------------------------------------------------------
    # Extension classifier rows (docs/specs/extension-classification.md R7)
    # ------------------------------------------------------------------

    def populate_extension_classifications(
        self,
        fsds_dir: str | Path,
        report_path: str | Path | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Classify the filers' extensions and replace the ``extension_classifier`` rows.

        Input (spec R7 as amended): statement lines and consolidated values from the
        cached FSDS quarters under ``fsds_dir``, calculation arcs from the raw tables
        (``CALCULATION_ARCS_SQL``), for every ingested filing FSDS holds; members and
        the hand list from ``committed_mapping_rows()``. Nothing reads an MV, so the
        step runs before the MV build. In one transaction it deletes every
        ``extension_classifier`` row, inserts the new ones and re-derives the
        ``ns_expanded`` copies (:meth:`_sync_ns_expanded`). Agreements, disagreements
        and unclassified extensions go to the report only; they never change the
        mapping.

        Args:
            fsds_dir: Directory of FSDS quarter folders (each with ``sub``, ``num``,
                ``pre`` and ``tag.tsv``), e.g. ``data/cache/sec_fsds/filtered``.
            report_path: Where to write the JSON report; None writes none.
            dry_run: Read and classify only; write nothing to the database.

        Returns:
            Stats: ``fsds_quarters``, ``accessions_ingested``, ``accessions_in_fsds``,
            ``filings``, ``subjects``, ``classified``, ``rows`` (the mapping rows),
            ``agreements``, ``disagreements``, ``unclassified`` (counts by reason),
            ``rows_deleted``, ``dry_run``, and ``ns_expanded`` when written.

        Raises:
            FileNotFoundError: ``fsds_dir`` holds no FSDS quarter.
            ValueError: No ingested filing is in the FSDS quarters, or a row's rank
                would leave its band.
            NsExpandedDriftError: The re-derived copies drift; nothing is committed.
        """
        fsds_root = Path(fsds_dir).expanduser().resolve()
        quarters = sorted(
            p for p in fsds_root.iterdir() if (p / "pre.tsv").is_file()
        ) if fsds_root.is_dir() else []
        if not quarters:
            raise FileNotFoundError(f"no FSDS quarter folder under {fsds_root}")
        tables = load_tables(quarters)
        committed = committed_mapping_rows()

        with psycopg2.connect(**self._db_conn_params) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT fr.accession_no, fr.cik FROM filing_registry fr"
                    " JOIN accession a ON a.filing_accession_number = fr.accession_no"
                )
                ingested = {acc: cik for acc, cik in cur.fetchall()}
                in_fsds = {r["adsh"] for r in tables["pre"]}
                ciks = {acc: cik for acc, cik in ingested.items() if acc in in_fsds}
                if not ciks:
                    raise ValueError(
                        f"none of {len(ingested)} ingested filings is in the FSDS "
                        f"quarters under {fsds_root}"
                    )
                cur.execute(ec.CALCULATION_ARCS_SQL, (sorted(ciks),))
                columns = [d[0] for d in cur.description]
                arcs = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
                cur.execute(
                    "SELECT concept_family, qname, preference_rank, cik_override,"
                    " substitution_code, substitution_note FROM concept_mapping"
                    " WHERE source NOT IN (%s, %s)",
                    (NS_EXPANDED_SOURCE, ec.EXTENSION_CLASSIFIER_SOURCE),
                )
                columns = [d[0] for d in cur.description]
                stored = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]

            filings = ec.classifier_filings(tables["pre"], tables["num"], arcs, ciks)
            results = classify_extensions(filings, ec.classifier_members(committed))
            mapped = extension_mapping_rows(results, ec.listed_extensions(committed))
            rows = ec.classifier_mapping_rows(mapped["rows"], stored)
            unclassified: dict[str, int] = {}
            for _, _, reason in mapped["unclassified"]:
                unclassified[reason] = unclassified.get(reason, 0) + 1
            stats: dict = {
                "fsds_quarters": len(quarters),
                "accessions_ingested": len(ingested),
                "accessions_in_fsds": len(ciks),
                "filings": len(filings),
                "subjects": len(results),
                "classified": sum(1 for r in results if r["family"] is not None),
                "rows": rows,
                "agreements": len(mapped["agreements"]),
                "disagreements": len(mapped["disagreements"]),
                "unclassified": dict(sorted(unclassified.items())),
                "rows_deleted": 0,
                "dry_run": dry_run,
            }
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM concept_mapping WHERE source = %s",
                        (ec.EXTENSION_CLASSIFIER_SOURCE,),
                    )
                    stats["rows_deleted"] = cur.rowcount
                    if rows:
                        psycopg2.extras.execute_batch(
                            cur,
                            """
                            INSERT INTO concept_mapping
                                (concept_family, qname, preference_rank,
                                 cik_override, is_deprecated, source,
                                 concept_role, substitution_code, substitution_note)
                            VALUES
                                (%(concept_family)s, %(qname)s, %(preference_rank)s,
                                 %(cik_override)s, %(is_deprecated)s, %(source)s,
                                 %(concept_role)s, %(substitution_code)s,
                                 %(substitution_note)s)
                            """,
                            rows,
                        )
                stats["ns_expanded"] = self._sync_ns_expanded(conn)
                conn.commit()

        if report_path is not None:
            report = {
                "spec": "docs/specs/extension-classification.md R7",
                "fsds_dir": str(fsds_root),
                **{k: v for k, v in stats.items() if k != "ns_expanded"},
                "agreement_list": [list(a) for a in mapped["agreements"]],
                "disagreement_list": [list(d) for d in mapped["disagreements"]],
                "unclassified_list": [list(u) for u in mapped["unclassified"]],
                "results": [
                    {k: (list(v) if isinstance(v, tuple) else v) for k, v in r.items()}
                    for r in results
                ],
            }
            out = Path(report_path).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=1), encoding="utf-8")
            stats["report_path"] = str(out)
        logger.info(
            "extension classifier%s: %d filings, %d subjects, %d classified, %d rows, "
            "%d agreements, %d disagreements, unclassified %s.",
            " (DRY RUN)" if dry_run else "",
            stats["filings"], stats["subjects"], stats["classified"], len(rows),
            stats["agreements"], stats["disagreements"], stats["unclassified"],
        )
        return stats

    def _sync_ns_expanded(self, conn: psycopg2.extensions.connection) -> dict:
        """Replace every ``ns_expanded`` row by its derivation from the base rows, then check.

        Runs inside the caller's transaction and does not commit: on
        :class:`NsExpandedDriftError` the caller's ``with`` block rolls the whole write
        back. Reads every concept_mapping row and the matching ``qname`` pairs, deletes
        every ``ns_expanded`` row, inserts :func:`expand_concept_mapping` of the rest,
        re-reads the table and calls :func:`assert_ns_expanded_in_sync`.

        Returns:
            Stats: ``ns_expanded_before``, ``drifted_before``, ``ns_expanded_deleted``,
            ``ns_expanded_inserted`` and ``ns_expanded_checked``.

        Raises:
            NsExpandedDriftError: A derived row would collide with a non-expanded row
                of the same key, or the re-read rows drift.
        """
        roles_synced = self._sync_override_roles(conn)
        if roles_synced:
            logger.info("auto_override roles synced from generic rows: %d", roles_synced)
        rows = self._read_concept_mapping_rows(conn)
        derived = expand_concept_mapping(rows, self._known_qnames(conn, rows))
        taken = {
            _mapping_key(r): r for r in rows if r.source != NS_EXPANDED_SOURCE
        }
        collisions = [r for r in derived if _mapping_key(r) in taken]
        if collisions:
            raise NsExpandedDriftError(
                f"{len(collisions)} derived ns_expanded row(s) collide with an existing "
                "non-expanded concept_mapping row of the same key: "
                + "; ".join(
                    f"{r.concept_family} {r.qname} cik_override={r.cik_override or '-'} "
                    f"(source {taken[_mapping_key(r)].source})"
                    for r in collisions
                )
            )
        stats = {
            "ns_expanded_before": sum(1 for r in rows if r.source == NS_EXPANDED_SOURCE),
            "drifted_before": len(ns_expanded_drift(rows)),
        }
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM concept_mapping WHERE source = %s", (NS_EXPANDED_SOURCE,)
            )
            stats["ns_expanded_deleted"] = cur.rowcount
            if derived:
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO concept_mapping
                        (concept_family, qname, preference_rank,
                         cik_override, is_deprecated, source,
                         concept_role, substitution_code, substitution_note)
                    VALUES
                        (%(concept_family)s, %(qname)s, %(preference_rank)s,
                         %(cik_override)s, %(is_deprecated)s, %(source)s,
                         %(concept_role)s, %(substitution_code)s,
                         %(substitution_note)s)
                    """,
                    self._records_to_dicts(derived),
                    page_size=500,
                )
        stats["ns_expanded_inserted"] = len(derived)
        stats["ns_expanded_checked"] = assert_ns_expanded_in_sync(
            self._read_concept_mapping_rows(conn)
        )
        logger.info(
            "ns_expanded re-derived: %d rows before (%d drifted), %d deleted, "
            "%d inserted, %d checked in sync.",
            stats["ns_expanded_before"],
            stats["drifted_before"],
            stats["ns_expanded_deleted"],
            stats["ns_expanded_inserted"],
            stats["ns_expanded_checked"],
        )
        return stats

    @staticmethod
    def _sync_override_roles(conn: psycopg2.extensions.connection) -> int:
        """Give every ``auto_override`` row its QName's generic role and substitution.

        The facts-count pass re-ranks a filer's family but never changes what a QName is
        to the family (docs/specs/concept-family-coverage.md), so its rows carry the
        generic row's ``concept_role``, ``substitution_code`` and ``substitution_note``.

        Returns:
            The number of rows updated.
        """
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE concept_mapping AS o
                   SET concept_role      = g.concept_role,
                       substitution_code = g.substitution_code,
                       substitution_note = g.substitution_note
                  FROM concept_mapping AS g
                 WHERE o.source = 'auto_override'
                   AND g.cik_override IS NULL
                   AND g.source <> 'ns_expanded'
                   AND g.concept_family = o.concept_family
                   AND g.qname = o.qname
                   AND (o.concept_role, o.substitution_code, o.substitution_note)
                       IS DISTINCT FROM
                       (g.concept_role, g.substitution_code, g.substitution_note)
                """
            )
            return max(cur.rowcount or 0, 0)

    @staticmethod
    def _read_concept_mapping_rows(
        conn: psycopg2.extensions.connection,
    ) -> list[ConceptMappingRecord]:
        """Every concept_mapping row, base and expanded."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT concept_family, qname, preference_rank, cik_override,"
                " is_deprecated, source, concept_role, substitution_code,"
                " substitution_note FROM concept_mapping"
            )
            return [
                ConceptMappingRecord(
                    concept_family=family,
                    qname=qname,
                    preference_rank=int(rank),
                    cik_override=cik,
                    is_deprecated=bool(deprecated),
                    source=source,
                    concept_role=role,
                    substitution_code=code,
                    substitution_note=note,
                )
                for family, qname, rank, cik, deprecated, source, role, code, note
                in cur.fetchall()
            ]

    @staticmethod
    def _known_qnames(
        conn: psycopg2.extensions.connection,
        rows: list[ConceptMappingRecord],
    ) -> list[tuple[str, str]]:
        """The ``(namespace, local_name)`` pairs of ``qname`` for the base rows' local names."""
        locals_ = sorted({p[1] for r in rows if (p := _expandable_base(r)) is not None})
        if not locals_:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT namespace, local_name FROM qname"
                " WHERE namespace IS NOT NULL AND local_name = ANY(%s)",
                (locals_,),
            )
            return [(ns, local) for ns, local in cur.fetchall()]

    # ------------------------------------------------------------------
    # Public API — geography_normalization
    # ------------------------------------------------------------------

    def populate_geography_normalization(
        self,
        csv_path: str | Path,
        mode: str = "upsert",
        dry_run: bool = False,
    ) -> dict:
        """Load geography_normalization rows from a CSV file.

        Parameters
        ----------
        csv_path:
            Path to CSV with columns: member_qname, iso_code, geo_name, geo_type.
            Empty iso_code → SQL NULL.
            Rows whose member_qname starts with 'PENDING:' are SKIPPED with a
            warning — those are unverified placeholder entries.
        mode:
            'upsert'      — ON CONFLICT DO UPDATE (default, idempotent).
            'insert_new'  — ON CONFLICT DO NOTHING.
            'replace_all' — TRUNCATE then INSERT (full reload).
        dry_run:
            Parse and validate without writing.

        Returns
        -------
        dict with keys:
            csv_path, rows_parsed, rows_ok, rows_skipped, rows_pending,
            rows_written, mode, dry_run, errors
        """
        csv_path = Path(csv_path).expanduser().resolve()
        valid_modes = ("upsert", "insert_new", "replace_all")
        if mode not in valid_modes:
            raise ValueError(f"mode must be one of {valid_modes}; got {mode!r}")

        stats: dict = {
            "csv_path": str(csv_path),
            "rows_parsed": 0,
            "rows_ok": 0,
            "rows_skipped": 0,
            "rows_pending": 0,
            "rows_written": 0,
            "mode": mode,
            "dry_run": dry_run,
            "errors": [],
        }

        records = self._parse_geography_csv(csv_path, stats)
        logger.info(
            "Geography CSV: %d rows parsed (%d ok, %d skipped, %d pending) from %s.",
            stats["rows_parsed"],
            stats["rows_ok"],
            stats["rows_skipped"],
            stats["rows_pending"],
            csv_path.name,
        )

        if dry_run:
            logger.info("dry_run=True — no database writes.")
            self._log_geography_summary(records)
            return stats

        if not records:
            logger.warning("No valid rows to write.")
            return stats

        with psycopg2.connect(**self._db_conn_params) as conn:
            stats["rows_written"] = self._write_geography_normalization(
                conn, records, mode
            )
            conn.commit()

        logger.info(
            "geography_normalization done: %d rows written (mode=%s).",
            stats["rows_written"],
            mode,
        )
        return stats

    # ------------------------------------------------------------------
    # Geography CSV parsing
    # ------------------------------------------------------------------

    def _parse_geography_csv(
        self, csv_path: Path, stats: dict
    ) -> list[GeographyNormalizationRecord]:
        if not csv_path.exists():
            raise FileNotFoundError(f"Geography CSV not found: {csv_path}")

        required_cols = {"member_qname", "iso_code", "geo_name", "geo_type"}
        valid_geo_types = {"country", "region", "segment"}
        records: list[GeographyNormalizationRecord] = []

        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not required_cols.issubset(set(reader.fieldnames or [])):
                missing = required_cols - set(reader.fieldnames or [])
                raise ValueError(f"CSV missing columns: {missing}")

            for line_no, row in enumerate(reader, start=2):
                stats["rows_parsed"] += 1
                qname = row["member_qname"].strip()

                # Skip placeholder / unverified entries
                if qname.startswith("PENDING:"):
                    logger.warning(
                        "Line %d: PENDING entry skipped (not yet verified): %s",
                        line_no,
                        qname,
                    )
                    stats["rows_pending"] += 1
                    continue

                if not qname:
                    stats["rows_skipped"] += 1
                    stats["errors"].append(f"Row {line_no}: empty member_qname")
                    continue

                geo_type = row["geo_type"].strip().lower()
                if geo_type not in valid_geo_types:
                    msg = f"Row {line_no}: invalid geo_type {row['geo_type']!r}"
                    logger.warning(msg)
                    stats["rows_skipped"] += 1
                    stats["errors"].append(msg)
                    continue

                geo_name = row["geo_name"].strip()
                if not geo_name:
                    stats["rows_skipped"] += 1
                    stats["errors"].append(f"Row {line_no}: empty geo_name")
                    continue

                iso_raw = row["iso_code"].strip()
                iso_code: str | None = iso_raw if iso_raw else None

                records.append(
                    GeographyNormalizationRecord(
                        member_qname=qname,
                        iso_code=iso_code,
                        geo_name=geo_name,
                        geo_type=geo_type,
                    )
                )
                stats["rows_ok"] += 1

        return records

    # ------------------------------------------------------------------
    # Database writes — geography_normalization
    # ------------------------------------------------------------------

    def _write_geography_normalization(
        self,
        conn: psycopg2.extensions.connection,
        records: list[GeographyNormalizationRecord],
        mode: str,
    ) -> int:
        rows = [
            {
                "member_qname": r.member_qname,
                "iso_code": r.iso_code,
                "geo_name": r.geo_name,
                "geo_type": r.geo_type,
            }
            for r in records
        ]

        if mode == "replace_all":
            with conn.cursor() as cur:
                cur.execute("TRUNCATE geography_normalization")
                logger.debug("TRUNCATE geography_normalization.")
            psycopg2.extras.execute_batch(
                conn.cursor(),
                """INSERT INTO geography_normalization
                       (member_qname, iso_code, geo_name, geo_type)
                   VALUES (%(member_qname)s, %(iso_code)s, %(geo_name)s, %(geo_type)s)""",
                rows,
                page_size=500,
            )
            return len(rows)

        if mode == "upsert":
            sql = """
                INSERT INTO geography_normalization
                    (member_qname, iso_code, geo_name, geo_type)
                VALUES
                    (%(member_qname)s, %(iso_code)s, %(geo_name)s, %(geo_type)s)
                ON CONFLICT (member_qname) DO UPDATE SET
                    iso_code  = EXCLUDED.iso_code,
                    geo_name  = EXCLUDED.geo_name,
                    geo_type  = EXCLUDED.geo_type
            """
        else:  # insert_new
            sql = """
                INSERT INTO geography_normalization
                    (member_qname, iso_code, geo_name, geo_type)
                VALUES
                    (%(member_qname)s, %(iso_code)s, %(geo_name)s, %(geo_type)s)
                ON CONFLICT (member_qname) DO NOTHING
            """

        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
        return len(rows)

    def _log_geography_summary(
        self, records: list[GeographyNormalizationRecord]
    ) -> None:
        from collections import Counter

        by_type = Counter(r.geo_type for r in records)
        std = sum(
            1
            for r in records
            if "xbrl.sec.gov/country" in r.member_qname
            or "fasb.org/srt/" in r.member_qname
        )
        ext = len(records) - std
        logger.info(
            "DRY RUN geography_normalization — %d records | "
            "by_type=%s | standard=%d extension=%d",
            len(records),
            dict(by_type),
            std,
            ext,
        )

    # ------------------------------------------------------------------
    # Per-filer concept_mapping override detection
    # ------------------------------------------------------------------

    def populate_concept_mapping_overrides(
        self,
        targets: list[tuple[str, int]] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Write per-filer concept_mapping rankings (docs/specs/concept-rank-integrity.md).

        A filer's rank 1 for a family is the first eligible (non-component) QName, in
        global rank order, that the filer reports undimensioned in any of its target
        annual filings. When that is not the global best, the filer gets a complete
        ranking as ``auto_override`` rows, so no two concepts tie at rank 1. The rank 1
        is the eligible QName reported in the most years (``facts_count_ranking``); a
        fallback-only primary of the coverage file never becomes it
        (docs/specs/screen-candidate-order.md R3). The
        filer's earlier ``auto_override`` rows for the family, and their
        ``ns_expanded`` copies, are replaced every time, also when no override is due.
        A (family, cik) with ``reviewed_override`` rows is skipped entirely: its
        reviewed ranking and their ``ns_expanded`` copies are neither deleted nor
        joined by ``auto_override`` rows (docs/specs/concept-families-wave1.md).

        The views join on the namespace-expanded rows, so a run that writes re-derives
        every ``ns_expanded`` row from the base rows and checks them in the same
        transaction (docs/specs/ns-expand-rank-sync.md); drift raises
        :class:`NsExpandedDriftError` and nothing is committed. Rebuild the MVs after.

        Args:
            targets: ``(cik, fiscal_year)`` pairs. One decision is made per filer
                across all of its years. None discovers every ingested annual filing.
            dry_run: Report the decisions without writing.

        Returns:
            Stats: ``targets_checked``, ``overrides_inserted``, ``already_optimal``,
            ``no_facts_found``, ``reviewed_kept`` (pairs skipped for a reviewed
            ranking), ``dry_run``, ``detail`` (one finding per filer and family it
            decided) and ``errors``.
        """
        stats: dict = {
            "targets_checked": 0,
            "overrides_inserted": 0,
            "already_optimal": 0,
            "no_facts_found": 0,
            "reviewed_kept": 0,
            "dry_run": dry_run,
            "detail": [],
            "errors": [],
        }

        with psycopg2.connect(**self._db_conn_params) as conn:
            if targets is None:
                targets = self._discover_override_targets(conn)
                logger.info(
                    "Discovered %d (cik, fiscal_year) targets from ingested accessions.",
                    len(targets),
                )
            if not targets:
                logger.warning("No targets found; nothing to check.")
                return stats

            concept_map = self._load_global_concept_map(conn)
            reviewed = self._reviewed_override_pairs(conn)
            fallback = load_family_coverage()["fallback"]
            logger.info(
                "Loaded %d concept_families, %d total QName entries from concept_mapping.",
                len(concept_map),
                sum(len(v) for v in concept_map.values()),
            )

            years_by_cik: dict[str, list[int]] = {}
            for cik, fiscal_year in targets:
                years_by_cik.setdefault(str(cik).strip().zfill(10), []).append(
                    int(fiscal_year)
                )

            for cik, years in years_by_cik.items():
                stats["targets_checked"] += len(years)
                logger.info("Checking CIK=%s FY=%s ...", cik, sorted(years))
                try:
                    present = self._undimensioned_concepts(conn, cik, years)
                    for family, ranking in concept_map.items():
                        if (family, cik) in reviewed:
                            # A reviewed ranking is never overwritten by the
                            # facts-count rule (docs/specs/concept-families-wave1.md).
                            stats["reviewed_kept"] += 1
                            continue
                        # The concept the filer uses in the most years, not in any year:
                        # NVIDIA used the global rank-1 revenue concept only in FY2022
                        # (docs/specs/filer-rank-coverage.md).
                        # A fallback-only primary never wins the pass
                        # (docs/specs/screen-candidate-order.md R3).
                        years_reported = {
                            q: self._qname_years(q, present) for q, _ in ranking
                        }
                        family_fallback = fallback.get(family, frozenset())
                        eligible = [
                            q for q, r in ranking
                            if _rank_is_eligible(r) and q not in family_fallback
                        ]
                        reported = consistent_concepts(years_reported, eligible)
                        filer = facts_count_ranking(ranking, years_reported, family_fallback)
                        finding = self._override_finding(
                            family, cik, years, ranking, reported, filer
                        )
                        stats["detail"].append(finding)
                        if finding["action"] == "no_facts":
                            stats["no_facts_found"] += 1
                        elif finding["action"] == "optimal":
                            stats["already_optimal"] += 1
                        else:
                            stats["overrides_inserted"] += 1
                        if not dry_run:
                            self._replace_overrides(conn, family, cik, filer)
                except Exception as exc:
                    msg = f"CIK={cik} FY={sorted(years)}: {exc}"
                    logger.exception(msg)
                    stats["errors"].append(msg)

            if not dry_run:
                stats["ns_expanded"] = self._sync_ns_expanded(conn)
                conn.commit()

        self._log_override_summary(stats, dry_run)
        return stats

    # ------------------------------------------------------------------
    # Override helpers
    # ------------------------------------------------------------------

    def _discover_override_targets(
        self, conn: psycopg2.extensions.connection
    ) -> list[tuple[str, int]]:
        """Return all (cik, fiscal_year) pairs that have a valid ingested accession."""
        sql = """
            SELECT DISTINCT fr.cik, fr.fiscal_year
              FROM filing_registry fr
              JOIN accession a ON a.filing_accession_number = fr.accession_no
             WHERE a.entry_url IS NOT NULL
               AND a.filing_accession_number ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'
               AND (fr.form_type LIKE '10-K%' OR fr.form_type LIKE '20-F%')
               AND fr.superseded_by IS NULL
             ORDER BY fr.cik, fr.fiscal_year
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            return [(r[0], r[1]) for r in cur.fetchall()]

    def _load_global_concept_map(
        self, conn: psycopg2.extensions.connection
    ) -> dict[str, list[tuple[str, int]]]:
        """Return {concept_family: [(qname, rank), ...]} sorted by rank asc.

        Short-form QNames only (``us-gaap:Revenues``). The ``ns_expanded`` copies carry
        the same ranks under full namespace URIs; loading them too would put an
        unreported copy ahead of its short form and force an override for every filer.
        """
        sql = """
            SELECT concept_family, qname, preference_rank
              FROM concept_mapping
             WHERE cik_override IS NULL
               AND is_deprecated = FALSE
               AND qname NOT LIKE 'http%%'
             ORDER BY concept_family, preference_rank, qname
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

        result: dict[str, list[tuple[str, int]]] = {}
        for family, qname, rank in rows:
            result.setdefault(family, []).append((qname, rank))
        return result

    @staticmethod
    def _reviewed_override_pairs(
        conn: psycopg2.extensions.connection,
    ) -> set[tuple[str, str]]:
        """The ``(concept_family, cik)`` pairs with a reviewed ranking or coverage rows."""
        pairs: set[tuple[str, str]] = set()
        with conn.cursor() as cur:
            for protected in _PROTECTED_SOURCES:
                cur.execute(
                    "SELECT DISTINCT concept_family, cik_override FROM concept_mapping"
                    " WHERE source = %s",
                    (protected,),
                )
                pairs |= {(family, cik) for family, cik in cur.fetchall()}
        return pairs

    def _undimensioned_concepts(
        self,
        conn: psycopg2.extensions.connection,
        cik: str,
        fiscal_years: list[int],
    ) -> dict[tuple[str, str], set[int]]:
        """``(local_name, namespace)`` → the fiscal years the filer reports it undimensioned.

        Only the canonical annual filings (10-K / 20-F, superseded_by IS NULL) of the
        given years count, and a year is the filing's ``fiscal_year``. Undimensioned is
        what a total looks like: a concept that appears only on segment or note rows is
        not the filer's reported measure.
        """
        sql = """
            SELECT q.local_name, q.namespace, array_agg(DISTINCT fr.fiscal_year)
              FROM fact f
              JOIN context ctx ON ctx.context_id  = f.context_id
              JOIN accession a  ON a.accession_id  = f.accession_id
              JOIN element el   ON el.element_id   = f.element_id
              JOIN qname q      ON q.qname_id      = el.qname_id
              JOIN filing_registry fr
                ON fr.accession_no = a.filing_accession_number
             WHERE LPAD(ctx.entity_identifier, 10, '0') = %s
               AND fr.fiscal_year = ANY(%s)
               AND (fr.form_type LIKE '10-K%%' OR fr.form_type LIKE '20-F%%')
               AND fr.superseded_by IS NULL
               AND ctx.entity_scheme = 'http://www.sec.gov/CIK'
               AND ctx.specifies_dimensions = FALSE
               AND el.is_numeric     = TRUE
               AND q.namespace IS NOT NULL
             GROUP BY q.local_name, q.namespace
        """
        with conn.cursor() as cur:
            cur.execute(sql, (cik, list(fiscal_years)))
            return {(r[0], r[1]): {int(y) for y in r[2]} for r in cur.fetchall()}

    @staticmethod
    def _qname_years(
        qname: str, present: collections.abc.Mapping[tuple[str, str], set[int]]
    ) -> set[int]:
        """The fiscal years in which a short-form QName is among ``present``.

        Bridges the prefix form (``us-gaap:Revenues``) to the versioned namespace URIs
        the facts carry (``http://fasb.org/us-gaap/2025``) via ``_PREFIX_TO_NS_BASE``.
        """
        parsed = _parse_prefixed_qname(qname)
        if parsed is None:
            return set()
        _, local, ns_bases = parsed
        years: set[int] = set()
        for (lname, ns), fys in present.items():
            if lname == local and any(ns.startswith(base) for base in ns_bases):
                years |= fys
        return years

    @staticmethod
    def _override_finding(
        family: str,
        cik: str,
        years: list[int],
        ranking: list[tuple[str, int]],
        reported: set[str],
        filer: list[tuple[str, int]] | None,
    ) -> dict:
        """One reportable decision for the stats ``detail`` list."""
        if not reported:
            action = "no_facts"
        elif filer is None:
            action = "optimal"
        else:
            action = "override"
        winner = filer[0][0] if filer else None
        return {
            "concept_family": family,
            "cik": cik,
            "fiscal_year": f"{min(years)}-{max(years)}",
            "rank1_qname": ranking[0][0] if ranking else None,
            "winner_qname": winner,
            "winner_rank": dict(ranking).get(winner) if winner else None,
            "ranking": filer,
            "action": action,
        }

    def _replace_overrides(
        self,
        conn: psycopg2.extensions.connection,
        family: str,
        cik: str,
        filer: list[tuple[str, int]] | None,
    ) -> None:
        """Replace the filer's override rows for one family with ``filer`` (or none)."""
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM concept_mapping
                 WHERE concept_family = %s
                   AND cik_override   = %s
                   AND source IN ('auto_override', 'ns_expanded')
                """,
                (family, cik),
            )
            if not filer:
                return
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO concept_mapping
                    (concept_family, qname, preference_rank, cik_override,
                     is_deprecated, source)
                VALUES (%s, %s, %s, %s, FALSE, 'auto_override')
                ON CONFLICT (concept_family, qname, (COALESCE(cik_override, '')))
                DO UPDATE SET
                    preference_rank = EXCLUDED.preference_rank,
                    source          = 'auto_override'
                """,
                [(family, qname, rank, cik) for qname, rank in filer],
            )

    def _log_override_summary(self, stats: dict, dry_run: bool) -> None:
        prefix = "DRY RUN " if dry_run else ""
        logger.info(
            "%sconcept_mapping overrides: targets=%d  overrides=%d  "
            "already_optimal=%d  no_facts=%d  reviewed_kept=%d  errors=%d",
            prefix,
            stats["targets_checked"],
            stats["overrides_inserted"],
            stats["already_optimal"],
            stats["no_facts_found"],
            stats.get("reviewed_kept", 0),
            len(stats["errors"]),
        )
        overrides = [d for d in stats["detail"] if d["action"] == "override"]
        if overrides:
            logger.info("%sOverride decisions:", prefix)
            for d in overrides:
                logger.info(
                    "  %s | CIK=%s FY=%s | rank 1 %r (global rank %s) over %r",
                    d["concept_family"],
                    d["cik"],
                    d["fiscal_year"],
                    d["winner_qname"],
                    d["winner_rank"],
                    d["rank1_qname"],
                )

    # ------------------------------------------------------------------
    # Post-ingest: patch Arelle accession.filing_accession_number
    # ------------------------------------------------------------------

    def patch_accession_numbers(self, dry_run: bool = False) -> dict:
        """Fix filing_accession_number in Arelle's accession table after xbrlDB ingest.

        Problem
        -------
        When Arelle ingests a local .htm (or _htm.xml) file via ``arelleCmdLine``
        with the xbrlDB plugin, it cannot derive the EDGAR accession number from a
        local file path.  It defaults ``filing_accession_number`` to a timestamp or
        integer sequence value (e.g. ``'1779318991'``), breaking the join between
        ``accession`` and ``filing_registry``.

        Solution
        --------
        For each manifest in the storage tree, this method:

        1. Derives the *document stem* from ``primary_document`` in the manifest
           (e.g. ``nvda-20260125.htm`` → stem ``nvda-20260125``).  Arelle may have
           loaded either the ``.htm`` or the ``_htm.xml`` variant; both share the
           same stem.

        2. Queries ``accession.entry_url`` with ``ILIKE '%<stem>%'`` to find the
           matching row.

        3. Validates the match count:
           - **0 rows** → WARNING (filing not yet ingested by Arelle; skip).
           - **>1 rows** → WARNING (ambiguous; skip and report all candidates).
           - **1 row, already correct** → DEBUG log, skip update.
           - **1 row, wrong value** → UPDATE and log at INFO level.

        Parameters
        ----------
        dry_run:
            If True, report what would be updated without writing.

        Returns
        -------
        dict with keys:
            manifests_processed, already_correct, updated,
            no_match, multiple_matches, skipped_no_stem, dry_run, warnings, errors
        """
        stats: dict = {
            "manifests_processed": 0,
            "already_correct": 0,
            "updated": 0,
            "no_match": 0,
            "multiple_matches": 0,
            "skipped_no_stem": 0,
            "dry_run": dry_run,
            "warnings": [],
            "errors": [],
        }

        # (record, local primary-document path): the path tells apart two unpatched
        # rows that share a file name (an amendment reusing its original's name).
        records: list[tuple[ManifestRecord, str | None]] = []
        for path in self._find_manifests():
            try:
                rec = self._parse_manifest(path)
                doc_path = (
                    str(path.parent / rec.primary_document)
                    if rec.primary_document
                    else None
                )
                records.append((rec, doc_path))
                stats["manifests_processed"] += 1
            except Exception as exc:
                msg = f"Could not parse {path}: {exc}"
                logger.error(msg)
                stats["errors"].append(msg)

        if not records:
            logger.warning("No manifests found under %s.", self.sec_edgar_root)
            return stats

        with psycopg2.connect(**self._db_conn_params) as conn:
            for rec, doc_path in records:
                self._patch_one_accession(conn, rec, dry_run, stats, doc_path)
            if not dry_run:
                conn.commit()

        self._log_patch_summary(stats)
        return stats

    def _patch_one_accession(
        self,
        conn: psycopg2.extensions.connection,
        rec: ManifestRecord,
        dry_run: bool,
        stats: dict,
        doc_path: str | None = None,
    ) -> None:
        """Attempt to patch the accession row for a single ManifestRecord.

        Rows are found by file-name stem, which an amendment can share with its
        original; ``pick_accession_row`` ignores rows already patched to another
        filing and tells unpatched ones apart by *doc_path*
        (docs/specs/ingest-accession-identity.md).
        """

        # --- derive document stem -----------------------------------------
        stem = self._doc_stem(rec.primary_document) if rec.primary_document else None
        if not stem:
            logger.warning(
                "%s FY%s %s: no primary_document in manifest; cannot match accession row.",
                rec.ticker,
                rec.fiscal_year,
                rec.fiscal_period,
            )
            stats["skipped_no_stem"] += 1
            stats["warnings"].append(
                f"{rec.ticker} FY{rec.fiscal_year} {rec.fiscal_period}: "
                "no primary_document — skipped"
            )
            return

        # --- find candidate accession rows by entry_url stem match --------
        candidates = self._find_accession_candidates(conn, stem)

        # --- choose this filing's row ---------------------------------------
        picked = pick_accession_row(
            rec.accession_no,
            doc_path,
            [
                (r["accession_id"], r["entry_url"], r["filing_accession_number"])
                for r in candidates
            ],
        )
        unpatched = [
            r for r in candidates if not is_accession_number(r["filing_accession_number"])
        ]
        if picked is not None:
            candidates = [r for r in candidates if r["accession_id"] == picked]
        elif not unpatched:
            # Every stem match belongs to another filing: this one is not ingested.
            candidates = []

        # --- validate match count ------------------------------------------
        if len(candidates) == 0:
            msg = (
                f"{rec.ticker} FY{rec.fiscal_year} {rec.fiscal_period} "
                f"(stem={stem!r}): no accession row found — "
                "filing may not have been ingested by Arelle yet."
            )
            logger.warning(msg)
            stats["no_match"] += 1
            stats["warnings"].append(msg)
            return

        if len(candidates) > 1:
            entries = ", ".join(
                f"id={r['accession_id']} accno={r['filing_accession_number']!r} "
                f"url={r['entry_url']!r}"
                for r in candidates
            )
            msg = (
                f"{rec.ticker} FY{rec.fiscal_year} {rec.fiscal_period} "
                f"(stem={stem!r}): {len(candidates)} accession rows matched — "
                f"ambiguous, manual resolution required. Candidates: [{entries}]"
            )
            logger.warning(msg)
            stats["multiple_matches"] += 1
            stats["warnings"].append(msg)
            return

        # --- single match ---------------------------------------------------
        row = candidates[0]
        current_accno: str = row["filing_accession_number"] or ""

        if current_accno == rec.accession_no:
            logger.debug(
                "%s FY%s %s: accession_id=%d already has correct "
                "filing_accession_number=%r — skipping.",
                rec.ticker,
                rec.fiscal_year,
                rec.fiscal_period,
                row["accession_id"],
                current_accno,
            )
            stats["already_correct"] += 1
            return

        logger.info(
            "%s%s FY%s %s: %s accession_id=%d  %r -> %r  (entry_url=%r)",
            "[DRY RUN] " if dry_run else "",
            rec.ticker,
            rec.fiscal_year,
            rec.fiscal_period,
            "would update" if dry_run else "updating",
            row["accession_id"],
            current_accno,
            rec.accession_no,
            row["entry_url"],
        )

        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE accession
                       SET filing_accession_number = %s
                     WHERE accession_id = %s
                    """,
                    (rec.accession_no, row["accession_id"]),
                )

        stats["updated"] += 1

    @staticmethod
    def _find_accession_candidates(
        conn: psycopg2.extensions.connection, stem: str
    ) -> list[dict]:
        """Return all accession rows whose entry_url contains *stem*."""
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT accession_id, filing_accession_number, entry_url,
                       filing_date, entity_id
                  FROM accession
                 WHERE entry_url ILIKE %s
                """,
                (f"%{stem}%",),
            )
            return list(cur.fetchall())

    @staticmethod
    def _doc_stem(primary_document: str) -> str | None:
        """Extract the date-encoded stem from a primary document filename.

        The stem is the filename without its XBRL/HTML suffix variants:

        - ``nvda-20260125.htm``      -> ``nvda-20260125``
        - ``nvda-20260125_htm.xml``  -> ``nvda-20260125``
        - ``d896993d20f.htm``        -> ``d896993d20f``

        Returns None if *primary_document* is empty or whitespace.
        """
        if not primary_document or not primary_document.strip():
            return None
        name = Path(primary_document).name
        # Strip known XBRL/iXBRL suffix variants, longest first
        for suffix in ("_htm.xml", ".htm", ".xml"):
            if name.lower().endswith(suffix):
                stem = name[: -len(suffix)]
                return stem if stem else None
        return name  # unknown extension — use full name as stem

    def _log_patch_summary(self, stats: dict) -> None:
        logger.info(
            "patch_accession_numbers: processed=%d  updated=%d  "
            "already_correct=%d  no_match=%d  multiple_matches=%d  "
            "skipped_no_stem=%d  errors=%d",
            stats["manifests_processed"],
            stats["updated"],
            stats["already_correct"],
            stats["no_match"],
            stats["multiple_matches"],
            stats["skipped_no_stem"],
            len(stats["errors"]),
        )
        for w in stats["warnings"]:
            logger.warning("  %s", w)

    # ------------------------------------------------------------------
    # Dry-run logging helpers
    # ------------------------------------------------------------------

    def _log_manifest_summary(self, records: list[ManifestRecord]) -> None:
        from collections import Counter

        by_form = Counter(r.form_type for r in records)
        by_period = Counter(r.fiscal_period for r in records)
        tickers = sorted({r.ticker for r in records})
        amendments = [r for r in records if r.is_amendment]
        logger.info(
            "DRY RUN manifests -- %d records | forms: %s | periods: %s | "
            "amendments: %d | tickers: %s",
            len(records),
            dict(by_form),
            dict(by_period),
            len(amendments),
            tickers,
        )
        for r in amendments:
            logger.info(
                "  Amendment: %s %s FY%s %s accession=%s",
                r.ticker,
                r.form_type,
                r.fiscal_year,
                r.fiscal_period,
                r.accession_no,
            )

    def _log_concept_mapping_summary(self, records: list[ConceptMappingRecord]) -> None:
        from collections import Counter

        by_family = Counter(r.concept_family for r in records)
        by_source = Counter(r.source for r in records)
        logger.info(
            "DRY RUN concept_mapping -- %d records | sources: %s | families: %d unique",
            len(records),
            dict(by_source),
            len(by_family),
        )
        logger.info("  Families: %s", sorted(by_family.keys()))
