# The production CoverageCardSource: reads mv_coverage_card from the fact store.
# Spec: docs/specs/coverage-card.md (requirement 7).

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.ingest.sec_edgar.coverage.base import CoverageCardSource, CoverageRow

# Statement and connect timeouts of the read-only session.
STATEMENT_TIMEOUT_MS = 60_000
CONNECT_TIMEOUT_S = 10

CARD_ROWS_SQL = """
SELECT cik, ticker, fiscal_year, metric, status,
       standard_qnames, extension_qnames, component_qnames
  FROM mv_coverage_card
 WHERE (%s::text IS NULL OR cik = %s::text)
   AND (%s::int IS NULL OR fiscal_year = %s::int)
"""


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


class PostgresCoverageCardSource(CoverageCardSource):
    """Reads the card rows from ``mv_coverage_card``, read-only and time-limited."""

    def __init__(self, conn: Any = None) -> None:
        """Initializes the source.

        Args:
            conn: An open psycopg2 connection to use; when ``None``, each call opens (and
                closes) its own read-only session.
        """
        self._conn = conn

    def rows(self, cik: str | None, fiscal_year: int | None) -> Sequence[CoverageRow]:
        """Returns the card rows, optionally restricted to one filer and one fiscal year.

        Args:
            cik: The filer's 10-digit CIK, or ``None`` for every filer.
            fiscal_year: The fiscal year, or ``None`` for every year.

        Returns:
            The matching rows, in no particular order.
        """
        conn = self._conn if self._conn is not None else connect_readonly()
        try:
            with conn.cursor() as cur:
                cur.execute(CARD_ROWS_SQL, (cik, cik, fiscal_year, fiscal_year))
                fetched = cur.fetchall()
        finally:
            if self._conn is None:
                conn.close()
        return [CoverageRow(r[0], r[1], int(r[2]), r[3], r[4], tuple(r[5] or ()),
                            tuple(r[6] or ()), tuple(r[7] or ())) for r in fetched]
