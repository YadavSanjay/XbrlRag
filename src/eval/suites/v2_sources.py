# The production seams of the v2 suite build (docs/specs/v2-suite-build.md): the corpus
# inventory read from the fact store, the fact store as a FactStoreSource for the key
# cross-check, and an HTTP transport that refuses every request, so a build can run from the
# SEC cache alone. All database access is read-only, parameterised and time-limited.

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.eval.keys.base import HttpTransport
from src.eval.keys.core import AnswerKey
from src.eval.sec_check.core import taxonomy_key
from src.eval.suites.intents import Lexicon
from src.eval.suites.v2_suite import FactStoreSource
from src.logging_config import get_logger

logger = get_logger(__name__)

# Statement and connect timeouts of the read-only session.
STATEMENT_TIMEOUT_MS = 120_000
CONNECT_TIMEOUT_S = 10

# Original annual reports only: an amendment can be a cover-page-only filing.
ANNUAL_DOCUMENT_TYPES = ("10-K", "20-F", "40-F")

# One row per (filer, fiscal year, canonical metric): the filing's current-period,
# non-dimensional, standard-taxonomy fact with the most preferred concept.
INVENTORY_SQL = """
SELECT DISTINCT ON (ticker, fiscal_year, concept_family)
       ticker, cik, fiscal_year, concept_family, concept, unit, concept_pref_rank
  FROM mv_financial_facts
 WHERE fiscal_period = 'FY'
   AND document_type = ANY(%s)
   AND is_current_period
   AND segment_axis IS NULL
   AND concept_family IS NOT NULL
   AND NOT is_extended
   AND value IS NOT NULL
   AND duration_kind IN ('annual', 'instant')
   AND ticker IS NOT NULL
 ORDER BY ticker, fiscal_year, concept_family, concept_pref_rank, concept, unit
"""

# Every concept the mapping offers per (filer, fiscal year, canonical metric), in rank
# order: the statement witness's candidates (docs/specs/key-statement-witness.md).
CANDIDATES_SQL = """
SELECT DISTINCT ticker, cik, fiscal_year, concept_family, concept, unit, concept_pref_rank
  FROM mv_financial_facts
 WHERE fiscal_period = 'FY'
   AND document_type = ANY(%s)
   AND is_current_period
   AND segment_axis IS NULL
   AND concept_family IS NOT NULL
   AND NOT is_extended
   AND value IS NOT NULL
   AND duration_kind IN ('annual', 'instant')
   AND ticker IS NOT NULL
 ORDER BY ticker, fiscal_year, concept_family, concept_pref_rank, concept, unit
"""

# Every non-dimensional, non-nil fact one filing tagged.
# mv_financial_facts.decimals_value is NULL for an INF (exact) fact as for a nil one, so
# the precision comes from fact.is_decimals_infinity: 'INF' ranks most precise, never
# as missing (docs/specs/sign-and-nil.md, Notes).
FILING_FACTS_SQL = """
SELECT m.concept, m.unit, m.period_start, m.period_end, m.value,
       CASE WHEN f.is_decimals_infinity THEN 'INF'
            ELSE m.decimals_value::int::text END AS decimals_value
  FROM mv_financial_facts m
  JOIN fact f ON f.fact_id = m.fact_id
 WHERE m.cik = %s
   AND m.accession_no = %s
   AND m.segment_axis IS NULL
   AND m.value IS NOT NULL
"""

_PER_SHARE_RE = re.compile(r"^(?:shares\*([A-Z]{3})|([A-Z]{3})\*shares)$")


def connect_readonly() -> Any:
    """Opens a read-only, autocommit ``xbrl_db`` session with a statement timeout.

    Returns:
        An open psycopg2 connection.
    """
    from src.db.conn import connect

    conn = connect(connect_timeout=CONNECT_TIMEOUT_S,
                   options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}")
    conn.set_session(readonly=True, autocommit=True)
    return conn


def companyfacts_concept(concept: str) -> str | None:
    """Maps a fact-store concept (``<namespace URI>:<local name>``) to companyfacts form.

    Args:
        concept: e.g. ``http://fasb.org/us-gaap/2024:Revenues``.

    Returns:
        ``"<taxonomy>:<local name>"`` (e.g. ``us-gaap:Revenues``), or ``None`` for a filer
        extension or any namespace companyfacts does not carry.
    """
    namespace, sep, local = concept.rpartition(":")
    if not sep or not namespace or not local:
        return None
    taxonomy = taxonomy_key(namespace)
    return f"{taxonomy}:{local}" if taxonomy is not None else None


def companyfacts_unit(unit: str) -> str:
    """Maps a fact-store unit string to the unit companyfacts names.

    The fact store writes a per-share unit as its measures joined by ``*``
    (``shares*USD``); companyfacts writes ``USD/shares``. Other units are the same.

    Args:
        unit: The fact store's unit string.

    Returns:
        The companyfacts unit.
    """
    match = _PER_SHARE_RE.match(unit)
    if match is None:
        return unit
    return f"{match.group(1) or match.group(2)}/shares"


def inventory_from_rows(rows: Iterable[Sequence[Any]]) -> dict[str, dict[str, Any]]:
    """Builds the corpus inventory from ``INVENTORY_SQL``-shaped rows.

    Args:
        rows: ``(ticker, cik, fiscal_year, concept_family, concept, unit)`` rows.

    Returns:
        ticker -> ``{"cik", "years": {year: {metric: {"concept", "unit"}}}}``, concepts and
        units in companyfacts form. Rows whose concept companyfacts does not carry are left
        out.

    Raises:
        ValueError: One ticker appears with two CIKs.
    """
    inventory: dict[str, dict[str, Any]] = {}
    skipped = 0
    for ticker, cik, fiscal_year, family, concept, unit in rows:
        cf_concept = companyfacts_concept(str(concept))
        if cf_concept is None or fiscal_year is None:
            skipped += 1
            continue
        t = str(ticker).strip().upper()
        cik10 = str(cik).strip().zfill(10)
        entry = inventory.setdefault(t, {"cik": cik10, "years": {}})
        if entry["cik"] != cik10:
            raise ValueError(f"ticker {t} has two CIKs: {entry['cik']} and {cik10}")
        entry["years"].setdefault(int(fiscal_year), {})[str(family)] = {
            "concept": cf_concept, "unit": companyfacts_unit(str(unit))}
    if skipped:
        logger.info("v2 inventory: %d row(s) without a companyfacts concept left out", skipped)
    return inventory


def drop_component_rows(rows: Iterable[Sequence[Any]]) -> list[tuple[Any, ...]]:
    """Leaves out inventory rows whose concept is in the component band.

    Args:
        rows: Seven-column ``INVENTORY_SQL`` rows, ``concept_pref_rank`` last.

    Returns:
        The rows with rank < 50 or rank >= 100, in order, as their first six columns.
    """
    return [tuple(row[:6]) for row in rows
            if row[6] is None or not 50 <= int(row[6]) < 100]


def candidate_concepts_from_rows(
    rows: Iterable[Sequence[Any]],
) -> dict[tuple[str, int, str], list[str]]:
    """Every concept the mapping offers per filer, year and family, for the witness.

    Args:
        rows: Seven-column ``CANDIDATES_SQL`` rows, in rank order.

    Returns:
        ``(cik, fiscal year, family)`` -> companyfacts concepts in rank order, every rank
        included: the statement witness, not the rank, decides between them
        (docs/specs/key-statement-witness.md requirement 7).
    """
    out: dict[tuple[str, int, str], list[str]] = {}
    for _ticker, cik, fiscal_year, family, concept, _unit, _rank in rows:
        cf_concept = companyfacts_concept(str(concept))
        if cf_concept is None or fiscal_year is None:
            continue
        found = out.setdefault((str(cik).strip().zfill(10), int(fiscal_year), str(family)), [])
        if cf_concept not in found:
            found.append(cf_concept)
    return out


def read_candidate_concepts(conn: Any) -> dict[tuple[str, int, str], list[str]]:
    """Reads every offered concept per filer, year and family (read-only).

    Args:
        conn: An open psycopg2 connection (see ``connect_readonly``).

    Returns:
        As ``candidate_concepts_from_rows``.
    """
    with conn.cursor() as cur:
        cur.execute(CANDIDATES_SQL, (list(ANNUAL_DOCUMENT_TYPES),))
        rows = list(cur.fetchall())
    return candidate_concepts_from_rows(rows)


def read_corpus_inventory(conn: Any) -> dict[str, dict[str, Any]]:
    """Reads the corpus inventory from ``mv_financial_facts`` (read-only).

    Args:
        conn: An open psycopg2 connection (see ``connect_readonly``).

    Returns:
        The inventory, as ``inventory_from_rows`` builds it.
    """
    with conn.cursor() as cur:
        cur.execute(INVENTORY_SQL, (list(ANNUAL_DOCUMENT_TYPES),))
        rows = list(cur.fetchall())
    return inventory_from_rows(drop_component_rows(rows))


def restrict_to_lexicon(inventory: Mapping[str, Any],
                        lexicon: Lexicon) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Keeps the filers and metrics the lexicon can phrase.

    A filer the lexicon does not name (a held-out filer, a filer not yet added) and a
    metric it has no surface form for cannot be rendered, so they are left out.

    Args:
        inventory: The corpus inventory.
        lexicon: The v2 lexicon.

    Returns:
        The restricted inventory, and ``{"tickers": [...], "metrics": [...]}`` of what was
        left out (sorted).
    """
    kept: dict[str, Any] = {}
    dropped_tickers: set[str] = set()
    dropped_metrics: set[str] = set()
    for ticker, entry in inventory.items():
        if ticker not in lexicon.companies:
            dropped_tickers.add(ticker)
            continue
        years: dict[int, dict[str, Any]] = {}
        for year, metrics in entry["years"].items():
            known = {m: spec for m, spec in metrics.items() if m in lexicon.metrics}
            dropped_metrics.update(m for m in metrics if m not in lexicon.metrics)
            if known:
                years[year] = known
        if years:
            kept[ticker] = {"cik": entry["cik"], "years": years}
    return kept, {"tickers": sorted(dropped_tickers), "metrics": sorted(dropped_metrics)}


def _iso(value: dt.date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _xbrl_end(value: dt.date | str) -> str:
    """The XBRL period end of a stored one: the store keeps Arelle's next-midnight date."""
    stored = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    return (stored - dt.timedelta(days=1)).isoformat()


def _decimals(value: Any) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.upper() == "INF":
        return "INF"
    return int(value)


class PostgresFactStoreSource(FactStoreSource):
    """The fact store's value per filing, concept, unit and period, from ``xbrl_db``.

    Reads each filing's facts once (one parameterised query per accession) and answers
    every later call from memory. Matching follows the store's conventions: concepts by
    companyfacts taxonomy and local name, units in companyfacts form, and the stored
    period end (Arelle's next midnight) moved back one day.
    """

    def __init__(self, conn: Any) -> None:
        """Builds the source over an open (read-only) connection.

        Args:
            conn: A psycopg2 connection, e.g. from ``connect_readonly``.
        """
        self._conn = conn
        self._filings: dict[tuple[str, str], list[tuple[Any, ...]]] = {}

    def _facts(self, cik: str, accession: str) -> list[tuple[Any, ...]]:
        key = (cik, accession)
        if key not in self._filings:
            with self._conn.cursor() as cur:
                cur.execute(FILING_FACTS_SQL, key)
                self._filings[key] = [tuple(r) for r in cur.fetchall()]
        return self._filings[key]

    def value(self, cik: str, concept: str, unit: str, period_start: str | None,
              period_end: str, accession: str) -> AnswerKey | None:
        """Returns the value the filing tagged, as a ``fact_store`` key.

        Args:
            cik: The filer's CIK, 10 digits.
            concept: ``"<taxonomy>:<tag>"``.
            unit: The unit in companyfacts form, e.g. ``USD``.
            period_start: The duration's start, or ``None`` for an instant.
            period_end: The period's end (ISO date).
            accession: The filing whose value is wanted.

        Returns:
            The key (``decimals`` from the store; among duplicate facts the most precise),
            or ``None`` when the filing tagged no such value.
        """
        cik10 = str(cik).strip().zfill(10)
        matches = []
        for row_concept, row_unit, start, end, val, decimals in self._facts(cik10, accession):
            if end is None or companyfacts_concept(str(row_concept)) != concept:
                continue
            if companyfacts_unit(str(row_unit)) != unit:
                continue
            if _iso(start) != period_start or _xbrl_end(end) != period_end:
                continue
            matches.append((val, _decimals(decimals)))
        if not matches:
            return None

        def precision(m: tuple[Any, int | str | None]) -> tuple[int, float]:
            d = m[1]
            rank = 10**6 if d == "INF" else (-10**6 if d is None else int(d))
            return rank, float(m[0])

        val, decimals = max(matches, key=precision)
        return AnswerKey(
            value=float(val), unit=unit, decimals=decimals, concept=concept, cik=cik10,
            period_start=period_start, period_end=period_end, fiscal_year=None,
            calendar_year=None, accession=accession, filed=None, form=None,
            key_source="fact_store", selection="as_reported", policies=[],
            status="single-source", verified_by=[],
        )


class CacheOnlyError(RuntimeError):
    """An SEC payload was not in the cache and the build may not fetch it."""


class RefusingTransport(HttpTransport):
    """An ``HttpTransport`` that refuses every request (``--sec-cache-only``)."""

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        """Refuses the request.

        Args:
            url: The URL that was not cached.
            headers: Ignored.

        Raises:
            CacheOnlyError: Always.
        """
        raise CacheOnlyError(f"{url} is not in the SEC cache and fetching is disabled")
