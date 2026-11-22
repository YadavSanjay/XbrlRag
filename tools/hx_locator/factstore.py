"""The fact store side: what ``hx.py check-keys`` will say, and SQL to look for yourself.

``check`` resolves a written amount exactly as ``check-keys`` does: the same store
(``PostgresHxKeyFactStore``, S1 candidates per filer-year, or one filing's column when an
accession is given) and the same matching (``find_classes`` over ``parse_amount``). The SQL
templates are for reading the views directly; ``run_sql`` runs a SELECT read-only.

Only the development database ``xbrl_db`` is ever opened, whatever ``XBRL_DB_NAME`` says:
the held-out database is out of bounds for this tool.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
import threading
from typing import Any

from src.eval.set_b.core import find_classes, parse_amount
from src.eval.set_b.hx import resolve_hx_case
from src.eval.set_b.hx_pg_store import PostgresHxKeyFactStore

DEV_DB = "xbrl_db"
_STATEMENT_TIMEOUT_MS = 30_000
_MAX_ROWS = 500
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_FAMILY_RE = re.compile(r"^[A-Za-z0-9_]+$")
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_STATEMENT_START = re.compile(r"^\s*(\(\s*)*(select|with|explain)\b", re.IGNORECASE)


def connect_dev() -> Any:
    """A read-only connection to ``xbrl_db`` with statement and connect timeouts."""
    from src.db.conn import connect

    conn = connect(dbname=DEV_DB, connect_timeout=10,
                   options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}")
    conn.set_session(readonly=True, autocommit=True)
    return conn


def quiet_error_logging(conn: Any) -> bool:
    """Stops the server from logging this session's failing statements and their errors.

    Postgres writes a failing statement (an error or a timeout in the SQL tab) to its server
    log at the default ``log_min_error_statement = error``, and the error message itself,
    which can quote the statement, at ``log_min_messages = warning``. Both settings need a
    superuser.

    Args:
        conn: An open connection.

    Returns:
        True when the session no longer logs failing statements; False when the role may
        not change the setting, so the server's own setting applies.
    """
    try:
        cur = conn.cursor()
        cur.execute("SET log_min_error_statement = panic")
        cur.execute("SET log_min_messages = panic")
    except Exception:  # isolation point: a non-superuser keeps the server's setting
        return False
    return True


def short_concept(concept: str) -> str:
    """``http://fasb.org/us-gaap/2024:Revenues`` -> ``us-gaap:Revenues`` (else ``ext:``)."""
    ns, _, local = concept.rpartition(":")
    if not ns or "/" not in ns:
        return concept
    if "fasb.org/us-gaap" in ns:
        return f"us-gaap:{local}"
    if "ifrs.org" in ns:
        return f"ifrs-full:{local}"
    if "xbrl.sec.gov/dei" in ns:
        return f"dei:{local}"
    if "fasb.org/srt" in ns:
        return f"srt:{local}"
    return f"ext:{local}"


def _plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


class FactStore:
    """A lazily connected, read-only view of ``xbrl_db`` (one connection, serialised)."""

    def __init__(self) -> None:
        """Builds the store; nothing connects until first use."""
        self._conn: Any = None
        self._store: PostgresHxKeyFactStore | None = None
        self._lock = threading.Lock()
        self._quiet_errors = False

    def _connection(self) -> Any:
        if self._conn is None or self._conn.closed:
            self._conn = connect_dev()
            self._quiet_errors = quiet_error_logging(self._conn)
            self._store = PostgresHxKeyFactStore(self._conn)
        return self._conn

    def status(self) -> dict[str, Any]:
        """Whether the database answers, and which one it is."""
        try:
            with self._lock:
                cur = self._connection().cursor()
                cur.execute("SELECT current_database(), "
                            "(SELECT count(DISTINCT ticker) FROM mv_financial_facts)")
                name, n = cur.fetchone()
            return {"ok": True, "database": name, "tickers": n,
                    "quiet_errors": self._quiet_errors}
        except Exception as exc:  # isolation point: the UI works without the database
            self._conn = None
            return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:300]}

    def registry_labels(self) -> dict[str, int] | None:
        """``{accession: fiscal_year}`` from ``filing_registry``; None when unreachable."""
        try:
            with self._lock:
                cur = self._connection().cursor()
                cur.execute("SELECT accession_no, fiscal_year FROM filing_registry "
                            "WHERE fiscal_year IS NOT NULL")
                return {str(a): int(y) for a, y in cur.fetchall()}
        except Exception:  # isolation point: fall back to the cover page's label
            self._conn = None
            return None

    def _details(self, fact_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not fact_ids:
            return {}
        cur = self._connection().cursor()
        cur.execute(
            "SELECT fact_id, accession_no, fiscal_year, filed_date, xml_id, "
            "concept_pref_rank, concept_role, decimals_value "
            "FROM mv_financial_facts WHERE fact_id = ANY(%s)", (fact_ids,))
        return {int(r[0]): {"accession": r[1], "filing_fy": r[2], "filed_date": _plain(r[3]),
                            "xml_id": r[4], "rank": r[5], "role": r[6], "decimals": r[7]}
                for r in cur.fetchall()}

    def candidates(self, ticker: str, fiscal_year: int, family: str | None = None,
                   accession: str | None = None) -> list[dict[str, Any]]:
        """The S1 candidates ``check-keys`` sees for a filer-year (or one filing's column).

        Args:
            ticker: The filer.
            fiscal_year: The fiscal year.
            family: Keep only this concept family (None keeps all).
            accession: Read this filing's column instead of the S1 candidates.

        Returns:
            Candidate dicts with the filing, element id, rank and role added.
        """
        with self._lock:
            self._connection()
            assert self._store is not None
            cands = (self._store.facts_in_filing(ticker, accession, fiscal_year) if accession
                     else self._store.facts_for(ticker, fiscal_year))
            if family:
                cands = [c for c in cands if c.get("concept_family") == family]
            details = self._details([int(c["fact_id"]) for c in cands])
        for c in cands:
            c.update(details.get(int(c["fact_id"]), {}))
            c["concept_short"] = short_concept(str(c["concept"]))
        return _plain(sorted(cands, key=lambda c: (c.get("dimensional", False),
                                                    c.get("rank") or 999, c["concept"])))

    def check(self, ticker: str, fiscal_year: int, expected: str, scale: str | None,
              unit: str, concept: str | None = None, member: str | None = None,
              accession: str | None = None) -> dict[str, Any]:
        """What ``check-keys`` will say about one written amount item.

        Args:
            ticker: The filer.
            fiscal_year: The fiscal year of the item.
            expected: The amount as you will write it (``"391,035"``).
            scale: ``units``/``thousands``/``millions``/``billions`` or None.
            unit: ``USD``, ``TWD``, ``pure``, ``shares`` or ``USD/shares``.
            concept: The case's ``concept`` hint, if any.
            member: The case's ``member`` hint, if any (makes the item dimensional).
            accession: The item's accession, if any (point-in-time items).

        Returns:
            ``{status, classes, matches}``: ``verified`` (one class), ``ambiguous`` or
            ``not_found``, and the matching facts.
        """
        try:
            amount = parse_amount(expected, scale)
        except ValueError as exc:
            return {"status": "invalid", "error": str(exc), "classes": [], "matches": []}
        cands = self.candidates(ticker, fiscal_year, None, accession)
        classes = find_classes(cands, amount, unit, bool(member), concept or None,
                               member or None)
        status = ("verified" if len(classes) == 1 else "ambiguous" if classes
                  else "not_found")
        ids = {i for cls in classes for i in cls}
        matches = [c for c in cands if int(c["fact_id"]) in ids]
        return {"status": status, "classes": classes, "matches": matches,
                "value": amount[0], "half_unit": amount[1]}

    def resolve(self, case: dict[str, Any]) -> dict[str, Any]:
        """``hx.py check-keys`` on one parsed case (``resolve_hx_case``), serialised."""
        with self._lock:
            self._connection()
            assert self._store is not None
            return _plain(resolve_hx_case(case, self._store))

    def run_sql(self, sql: str) -> dict[str, Any]:
        """Runs one read-only SELECT (or WITH / EXPLAIN) and returns at most 500 rows."""
        if not _STATEMENT_START.match(sql) or ";" in sql.strip().rstrip(";"):
            return {"ok": False, "error": "only a single SELECT, WITH or EXPLAIN statement"}
        try:
            with self._lock:
                cur = self._connection().cursor()
                cur.execute(sql.strip().rstrip(";"))
                cols = [d[0] for d in cur.description or []]
                rows = cur.fetchmany(_MAX_ROWS + 1)
            return {"ok": True, "columns": cols, "rows": _plain([list(r) for r in
                                                                 rows[:_MAX_ROWS]]),
                    "truncated": len(rows) > _MAX_ROWS}
        except Exception as exc:  # isolation point: show the database's own message
            self._conn = None
            return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:600]}


# ------------------------------------------------------------------------- SQL text


def _q(value: str, pattern: re.Pattern[str]) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"refusing to quote {value!r}")
    return "'" + value + "'"


def _tickers(tickers: list[str]) -> str:
    return ", ".join(_q(t, _TICKER_RE) for t in tickers)


_COLUMNS = """m.ticker, m.fiscal_year, m.accession_no, m.filed_date, m.concept,
       m.concept_pref_rank AS rank, m.concept_role AS role, m.value, m.unit,
       m.decimals_value AS decimals, m.period_start, m.period_end - 1 AS period_end,
       m.segment_axis, m.segment_member, m.segment_label, m.fact_id, m.xml_id"""


def sql_templates(tickers: list[str], years: list[int], family: str,
                  accession: str | None = None) -> list[dict[str, str]]:
    """Ready-to-run SQL for looking at a family in the views.

    Args:
        tickers: The filers.
        years: The fiscal years.
        family: The concept family.
        accession: A filing to read, for the point-in-time template.

    Returns:
        ``[{name, note, sql}]``. ``period_end - 1`` undoes the store's one-day-late end.
    """
    fam = _q(family, _FAMILY_RE)
    tk = _tickers(tickers)
    yrs = ", ".join(str(int(y)) for y in years)
    own = f"""SELECT {_COLUMNS}
  FROM mv_financial_facts m
 WHERE m.ticker IN ({tk})
   AND m.fiscal_year IN ({yrs}) AND m.fiscal_period = 'FY'
   AND m.is_canonical_filing AND m.is_current_period
   AND m.concept_family = {fam}
   AND (m.duration_kind = 'annual' OR m.period_type = 'instant')
   AND m.segment_axis IS NULL AND m.value IS NOT NULL
 ORDER BY m.ticker, m.fiscal_year, m.concept_pref_rank, m.fact_id"""
    out = [
        {"name": "As originally reported (each year's own column)",
         "note": "The year's own annual report, current-period column, undimensioned. "
                 "Rank 1 / role primary is what the product's lookup answers with.",
         "sql": own},
        {"name": "Every filing that states the same period (S1: newest filed = latest)",
         "note": "Each own-column fact, joined to every annual filing reporting the same "
                 "concept, unit and period. The newest filed_date per concept is the "
                 "'latest' value S1 keys from. Exact-date join: a 52/53-week drift can "
                 "hide a restatement; the Check button applies the exact S1 rule.",
         "sql": f"""WITH own AS (
{own}
)
SELECT o.ticker, o.fiscal_year AS for_fy, m.accession_no, m.fiscal_year AS filing_fy,
       m.filed_date, m.concept, m.value, o.value AS original_value,
       (m.value <> o.value) AS differs, m.fact_id, m.xml_id
  FROM own o
  JOIN mv_financial_facts m
    ON m.ticker = o.ticker AND m.concept = o.concept AND m.unit = o.unit
   AND m.period_end - 1 = o.period_end
   AND m.period_start IS NOT DISTINCT FROM o.period_start
   AND m.segment_axis IS NULL AND m.fiscal_period = 'FY' AND m.value IS NOT NULL
 ORDER BY o.ticker, o.fiscal_year, m.concept, m.filed_date DESC"""},
        {"name": "Members (dimensional facts) of the family",
         "note": "Segment, product and geography splits in each year's own column.",
         "sql": f"""SELECT m.ticker, m.fiscal_year, m.concept, m.segment_axis, m.segment_member,
       m.segment_label, m.has_multiple_dimensions, m.value, m.unit,
       m.period_end - 1 AS period_end, m.fact_id, m.xml_id
  FROM mv_financial_facts m
 WHERE m.ticker IN ({tk})
   AND m.fiscal_year IN ({yrs}) AND m.fiscal_period = 'FY'
   AND m.is_canonical_filing AND m.is_current_period
   AND m.concept_family = {fam}
   AND m.segment_axis IS NOT NULL AND m.value IS NOT NULL
 ORDER BY m.ticker, m.fiscal_year, m.segment_axis, m.value DESC"""},
        {"name": "Coverage: is the family tagged per filer-year?",
         "note": "Counts of undimensioned own-column facts of the family; 0 means "
                 "not_tagged for this family (look for a substitute concept).",
         "sql": f"""SELECT m.ticker, m.fiscal_year,
       count(*) FILTER (WHERE m.concept_family = {fam}
                          AND m.segment_axis IS NULL) AS family_facts,
       string_agg(DISTINCT m.concept, ', ')
         FILTER (WHERE m.concept_family = {fam}) AS concepts
  FROM mv_financial_facts m
 WHERE m.ticker IN ({tk})
   AND m.fiscal_period = 'FY' AND m.is_canonical_filing AND m.is_current_period
 GROUP BY m.ticker, m.fiscal_year
 ORDER BY m.ticker, m.fiscal_year"""},
        {"name": "Concepts the filer tagged that no family maps",
         "note": "Own-column numeric facts with no concept_family, for a word in the "
                 "concept name. Edit the ILIKE pattern.",
         "sql": f"""SELECT m.ticker, m.fiscal_year, m.concept, m.concept_label, m.value, m.unit,
       m.period_end - 1 AS period_end, m.fact_id, m.xml_id
  FROM mv_financial_facts m
 WHERE m.ticker IN ({tk})
   AND m.fiscal_year IN ({yrs}) AND m.fiscal_period = 'FY'
   AND m.is_canonical_filing AND m.is_current_period
   AND m.concept_family IS NULL AND m.segment_axis IS NULL AND m.value IS NOT NULL
   AND m.concept ILIKE '%Research%'
 ORDER BY m.ticker, m.concept"""},
    ]
    if accession:
        acc = _q(accession, _ACCESSION_RE)
        out.append({
            "name": "One filing's values (point in time: as that filing reported them)",
            "note": "Every annual value of the family in this filing, current and "
                    "comparative columns.",
            "sql": f"""SELECT {_COLUMNS}
  FROM mv_financial_facts m
 WHERE m.accession_no = {acc} AND m.fiscal_period = 'FY'
   AND m.concept_family = {fam}
   AND (m.duration_kind = 'annual' OR m.period_type = 'instant')
   AND m.segment_axis IS NULL AND m.value IS NOT NULL
 ORDER BY m.period_end DESC, m.concept_pref_rank"""})
    return out
