"""The Postgres ``FactStore`` for the SEC companyfacts check.

Reads our facts from ``mv_financial_facts`` (falling back to
``mv_financial_facts_multidim`` for a multi-axis fact the flat view lacks) and a derived
metric's components from ``mv_derived_metrics``. Read-only; every record read is cached,
so the CLI can prefetch all cited facts in one query.

A fact is **dimensional** when its XBRL context carries any non-default dimension
(``context_dimension.is_default = FALSE``) — read from the base tables, not from
``mv_financial_facts.segment_axis``, which keeps only the first axis.

Spec: docs/specs/sec-companyfacts-check.md (requirement 11; not spec-tested).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from src.eval.sec_check.base import FactRecord, FactStore
from src.logging_config import get_logger

logger = get_logger(__name__)

# Statement and connect timeouts for the read-only session.
_STATEMENT_TIMEOUT_MS = 120_000
_CONNECT_TIMEOUT_S = 10

_FACTS_SQL = """
SELECT m.fact_id, m.ticker, m.cik, m.accession_no, m.concept, m.unit,
       m.period_start, m.period_end, m.value,
       EXISTS (SELECT 1
                 FROM fact f
                 JOIN context_dimension cd
                   ON cd.context_id = f.context_id AND cd.is_default = FALSE
                WHERE f.fact_id = m.fact_id) AS dimensional
  FROM mv_financial_facts m
 WHERE m.fact_id = ANY(%s)
"""

# A multi-axis fact is dimensional by construction (the view joins context_dimension).
_MULTIDIM_SQL = """
SELECT md.fact_id, em.trading_symbol, md.cik, md.accession_no, md.concept, md.unit,
       md.period_start, md.period_end, md.value, TRUE AS dimensional
  FROM mv_financial_facts_multidim md
  LEFT JOIN entity_master em ON em.cik = md.cik
 WHERE md.fact_id = ANY(%s)
"""

_COMPONENTS_SQL = """
SELECT component_fact_ids
  FROM mv_derived_metrics
 WHERE ticker = %s AND fiscal_year = %s AND fiscal_period = 'FY' AND metric_name = %s
 LIMIT 1
"""


def _iso(value: dt.date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def record_from_row(row: Sequence[Any]) -> FactRecord | None:
    """Builds a ``FactRecord`` from a ``_FACTS_SQL``-shaped row.

    Args:
        row: ``(fact_id, ticker, cik, accession_no, concept, unit, period_start,
            period_end, value, dimensional)``.

    Returns:
        The record, with a 10-digit CIK, ISO dates and a float value; None for a nil
        fact (NULL value) or a fact without a period end, which cannot be matched.

    The fact store keeps Arelle's date convention: an XBRL ``endDate`` or ``instant``
    of ``2024-09-28`` is stored as the next midnight, ``2024-09-29``, while start dates
    are stored as written. companyfacts reports the XBRL date, so the stored end is
    moved back one day here.
    """
    (fact_id, ticker, cik, accession_no, concept, unit, period_start, period_end, value,
     dimensional) = row
    if value is None or period_end is None:
        return None
    if isinstance(period_end, str):
        period_end = dt.date.fromisoformat(period_end)
    period_end = period_end - dt.timedelta(days=1)
    return FactRecord(
        fact_id=int(fact_id),
        ticker=ticker or "",
        cik=str(cik).strip().zfill(10),
        accession_no=str(accession_no),
        concept=str(concept),
        unit=str(unit),
        period_start=_iso(period_start),
        period_end=_iso(period_end) or "",
        value=float(value),
        dimensional=bool(dimensional),
    )


class PostgresFactStore(FactStore):
    """Reads facts and derived-metric components from the local ``xbrl_db``."""

    def __init__(self, conn: Any = None) -> None:
        """Builds the store; connects lazily via ``src.db.conn`` unless given a connection.

        Args:
            conn: An open psycopg2 connection; the store opens its own when None.
        """
        self._conn = conn
        self._owns_conn = conn is None
        self._records: dict[int, FactRecord] = {}
        self._absent: set[int] = set()
        self._components: dict[tuple[str, int, str], dict | None] = {}

    def __enter__(self) -> PostgresFactStore:
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

    def _query(self, sql: str, params: tuple) -> list[tuple]:
        with self._connection().cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def facts(self, fact_ids: list[int]) -> dict[int, FactRecord]:
        """Returns the records of the given facts, keyed by fact id.

        Args:
            fact_ids: The facts to read.

        Returns:
            One record per fact id found; a nil fact or an unknown id is left out.
        """
        wanted = [int(i) for i in dict.fromkeys(fact_ids)]
        missing = [i for i in wanted if i not in self._records and i not in self._absent]
        if missing:
            found = self._read(missing)
            self._records.update(found)
            self._absent.update(i for i in missing if i not in found)
        return {i: FactRecord(**self._records[i]) for i in wanted if i in self._records}

    def _read(self, fact_ids: list[int]) -> dict[int, FactRecord]:
        found: dict[int, FactRecord] = {}
        for row in self._query(_FACTS_SQL, (fact_ids,)):
            record = record_from_row(row)
            if record is not None:
                found[record["fact_id"]] = record
        rest = [i for i in fact_ids if i not in found]
        if rest:
            for row in self._query(_MULTIDIM_SQL, (rest,)):
                record = record_from_row(row)
                if record is not None:
                    found[record["fact_id"]] = record
        unknown = [i for i in fact_ids if i not in found]
        if unknown:
            logger.warning("sec_check: %d fact id(s) not found or nil: %s", len(unknown),
                           unknown[:20])
        return found

    def metric_components(self, ticker: str, fiscal_year: int, metric: str) -> dict | None:
        """Returns a derived metric's FY ``component_fact_ids`` (role -> id or ids).

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.
            metric: The metric name, as in ``mv_derived_metrics.metric_name``.

        Returns:
            The components, or None when the metric has no FY row.
        """
        key = (ticker, int(fiscal_year), metric)
        if key not in self._components:
            rows = self._query(_COMPONENTS_SQL, key)
            self._components[key] = dict(rows[0][0]) if rows and rows[0][0] else None
        components = self._components[key]
        return dict(components) if components is not None else None
