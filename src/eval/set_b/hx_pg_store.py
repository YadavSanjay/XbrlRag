"""The Postgres ``HxKeyFactStore`` for H-X's answer-key check.

``facts_for(ticker, fiscal_year)`` runs set B's read (:class:`PostgresKeyFactStore`,
``key_candidates``): flat facts on S1, and a lone dimensional fact on S1 too (the latest
later-labelled canonical filing that reports the own column's member with the same full
dimension set, else as filed; docs/specs/set-b-dimensional-s1.md); unlike set B's, it never
logs the filer. ``breakdown_facts_for`` is inherited (H-X has no breakdown kind and never
calls it) and logs counts only.
``facts_in_filing(ticker, accession, fiscal_year)`` reads one filing, canonical or
superseded, and keeps the column that reports the filer's fiscal year ``fiscal_year``:
the current column when the filing's own fiscal year (DEI ``DocumentFiscalYearFocus``)
is ``fiscal_year``, else the comparative column whose period end lies
``focus_year - fiscal_year`` years before the current one (within
``COLUMN_TOLERANCE_DAYS``, which absorbs 52/53-week calendars; comparative columns are
about 365 days apart). Annual durations and instants only, flat and dimensional, nil
facts left out. This is what an "as originally reported" vs "latest" pair (S1) needs.

Dates follow set B's store: the fact store keeps Arelle's one-day-late ``period_end``,
moved back here by ``candidate_from_row``. The column arithmetic runs on the stored dates,
where the shift cancels.

Read-only session with statement and connect timeouts; every query is parameterised.
Each ``(ticker, accession, fiscal_year)`` is read once and cached.

Spec: docs/specs/hx-authoring.md (requirement 9; not spec-tested).
"""

from __future__ import annotations

from typing import Any

from src.eval.set_b.hx_base import HxKeyFactStore
from src.eval.set_b.pg_store import PostgresKeyFactStore, candidate_from_row
from src.logging_config import get_logger

logger = get_logger(__name__)

# How far a comparative column's period end may drift from the whole-year offset.
COLUMN_TOLERANCE_DAYS = 30

_FILING_SQL = """
WITH f AS (
    SELECT m.fact_id, m.concept, m.concept_family, m.unit, m.value,
           m.period_start, m.period_end,
           (m.segment_axis IS NOT NULL) AS dimensional,
           m.segment_label, m.segment_member,
           COALESCE(m.has_multiple_dimensions, FALSE) AS multi_axis,
           md.dimension_labels,
           m.fiscal_year AS focus_year, m.is_current_period
      FROM mv_financial_facts m
      LEFT JOIN mv_financial_facts_multidim md
             ON md.fact_id = m.fact_id AND COALESCE(m.has_multiple_dimensions, FALSE)
     WHERE m.ticker = %(ticker)s AND m.accession_no = %(accession)s
       AND m.fiscal_period = 'FY'
       AND m.value IS NOT NULL AND m.period_end IS NOT NULL
       AND (m.duration_kind = 'annual' OR m.period_type = 'instant')
), cur AS (
    SELECT MAX(focus_year) AS focus_year,
           MAX(period_end) FILTER (WHERE is_current_period) AS cur_end
      FROM f
)
SELECT f.fact_id, f.concept, f.concept_family, f.unit, f.value,
       f.period_start, f.period_end, f.dimensional, f.segment_label, f.segment_member,
       f.multi_axis, f.dimension_labels
  FROM f CROSS JOIN cur
 WHERE cur.cur_end IS NOT NULL
   AND ABS(f.period_end
           - (cur.cur_end - make_interval(years => cur.focus_year - %(fiscal_year)s))::date)
       <= %(tolerance)s
 ORDER BY f.fact_id
"""


def filing_params(ticker: str, accession: str, fiscal_year: int) -> dict[str, Any]:
    """The bind parameters of the filing query.

    Args:
        ticker: The filer's ticker.
        accession: The filing's accession number (dashed form).
        fiscal_year: The fiscal year whose column is read.

    Returns:
        The named parameters for ``_FILING_SQL``.
    """
    return {"ticker": ticker, "accession": accession, "fiscal_year": int(fiscal_year),
            "tolerance": COLUMN_TOLERANCE_DAYS}


class PostgresHxKeyFactStore(PostgresKeyFactStore, HxKeyFactStore):
    """Reads H-X candidate facts from the local ``xbrl_db``, read-only."""

    def __init__(self, conn: Any = None) -> None:
        """Builds the store; connects lazily via ``src.db.conn`` unless given a connection.

        Args:
            conn: An open psycopg2 connection; the store opens its own when None.
        """
        super().__init__(conn)
        self._filings: dict[tuple[str, str, int], list[dict]] = {}

    def __enter__(self) -> PostgresHxKeyFactStore:
        return self

    def facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        """Set B's filer-year read, logging counts only (never the filer).

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.

        Returns:
            Candidate dicts with set B's keys (copies).
        """
        key = (ticker, int(fiscal_year))
        if key not in self._facts:
            self._facts[key] = self._read_candidates(ticker, key[1])
            logger.info("hx: one filer-year read: %d candidate facts", len(self._facts[key]))
        return [dict(c) for c in self._facts[key]]

    def facts_in_filing(self, ticker: str, accession: str, fiscal_year: int) -> list[dict]:
        """Returns the candidates filing ``accession`` reports for ``fiscal_year``.

        Args:
            ticker: The filer's ticker.
            accession: The filing's accession number.
            fiscal_year: The filer's fiscal year, current or comparative column.

        Returns:
            Candidate dicts with set B's keys (copies).
        """
        key = (ticker, accession, int(fiscal_year))
        if key not in self._filings:
            with self._connection().cursor() as cur:
                cur.execute(_FILING_SQL, filing_params(ticker, accession, fiscal_year))
                rows = cur.fetchall()
            self._filings[key] = [candidate_from_row(r) for r in rows]
            # Counts only: the log never names the filer or the filing.
            logger.info("hx: one filing column read: %d candidate facts",
                        len(self._filings[key]))
        return [dict(c) for c in self._filings[key]]
