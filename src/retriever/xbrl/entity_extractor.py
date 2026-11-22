"""Deterministic entity/period resolution against Layer-0 XBRL tables.

This is "step 2" of the entity_extractor + intent_router pipeline described in
docs/design/XbrlRag_Architecture.md: given the raw
company/period mentions an LLM extracted from a user query, resolve them to
exact `(cik, ticker, fiscal_year, period_type)` via SQL against `entity_master`
and `fiscal_calendar` — never let the LLM invent a CIK or fiscal-year boundary.

Tables used (see scripts/ddl/layer_0/):
- entity_master.sql    — cik, trading_symbol, company_name, fye_month/day, last_seen_fy
- fiscal_calendar.sql  — (cik, fiscal_year, fiscal_period) -> period_start/period_end

Connection parameters are read from environment variables (loaded from .env),
same convention as Layer0Populator:

    XBRL_DB_HOST      default: localhost
    XBRL_DB_PORT      default: 5432
    XBRL_DB_NAME      default: xbrl_db
    XBRL_DB_USER      default: postgres
    XBRL_DB_PASSWORD  default: postgres
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.logging_config import get_logger
from src.observability.trace_context import log_event
from src.retriever.extract_year_util import (
    MAX_RANGE_YEARS,
    extract_years_in_range,
    overlong_year_ranges,
)
from src.retriever.xbrl.entity_aliases import load_company_aliases, match_company_alias
from src.retriever.xbrl.entity_resolution import resolve_company
from src.retriever.xbrl.fiscal_date import (
    find_written_dates,
    match_period_end,
    mention_period_kind,
    range_fill,
    resolve_dated_mention,
)

logger = get_logger(__name__)

# The bounds a year written in a period mention must fall in.
_MENTION_YEAR_RANGE = [2018, 2035]

RELATIVE_PERIOD_PHRASES = (
    "most recent",
    "latest",
    "current",
    "this year",
)
PRIOR_PERIOD_PHRASES = (
    "prior year",
    "last year",
    "previous year",
    "prior fiscal year",
    # The "fiscal" siblings of "last/previous/preceding year": whole phrases only, so
    # "last fiscal quarter" never becomes a full year (docs/specs/last-fiscal-year-phrase.md).
    "last fiscal year",
    "previous fiscal year",
    "preceding fiscal year",
    "year ago",
    "year-ago",
    "preceding year",
)

# Quarter detection (10-Q support). Ordered longest-first for the word forms.
_QUARTER_WORDS = {
    "first quarter": "Q1",
    "1st quarter": "Q1",
    "q1": "Q1",
    "second quarter": "Q2",
    "2nd quarter": "Q2",
    "q2": "Q2",
    "third quarter": "Q3",
    "3rd quarter": "Q3",
    "q3": "Q3",
    "fourth quarter": "Q4",
    "4th quarter": "Q4",
    "q4": "Q4",
}
# Year-to-date / cumulative phrasings -> pick the cumulative duration, not the
# discrete quarter.
_YTD_PHRASES = (
    "year to date",
    "year-to-date",
    "ytd",
    "first half",
    "six months",
    "six-month",
    "first nine months",
    "nine months",
    "nine-month",
    "cumulative",
)
# YTD phrasings that also pin the quarter even without an explicit "Qn".
_H1_PHRASES = ("first half", "1st half", "six months", "six-month", "h1")
_9M_PHRASES = ("first nine months", "nine months", "nine-month", "9 months")


def _detect_quarter(cleaned: str) -> str | None:
    """Return 'Q1'..'Q4' if the (lowercased) mention names a quarter, else None."""
    for phrase, q in _QUARTER_WORDS.items():
        if phrase in cleaned:
            return q
    return None


def _detect_period(cleaned: str) -> tuple:
    """Return (fiscal_period, ytd) from a lowercased mention.

    'FY' (annual) is the default. An explicit "Qn" wins; a cumulative phrase that
    implies a quarter ("first half" -> Q2 YTD, "nine months" -> Q3 YTD) maps to
    that quarter with ytd=True.
    """
    q = _detect_quarter(cleaned)
    if q:
        return q, any(p in cleaned for p in _YTD_PHRASES)
    if any(p in cleaned for p in _H1_PHRASES):
        return "Q2", True
    if any(p in cleaned for p in _9M_PHRASES):
        return "Q3", True
    return "FY", False


@dataclass
class ResolvedCompany:
    """Result of resolving one `company_mentions[]` entry against entity_master."""

    mention: str
    cik: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    fye_month: int | None = None
    fye_day: int | None = None
    last_seen_fy: int | None = None
    # "exact_ticker" | "alias" | "exact_name" | "fuzzy_name" | "ambiguous" | "unresolved"
    resolution: str = "unresolved"
    similarity: float = 0.0
    # When not resolved: the closest filers, best first, as {"cik", "ticker",
    # "company_name"} (docs/specs/entity-resolution-margin.md), for a "did you mean".
    candidates: list[dict[str, str | None]] = field(default_factory=list)

    @property
    def is_ambiguous(self) -> bool:
        return self.resolution in ("ambiguous", "unresolved")

    def clarification(self) -> str:
        """Why the mention did not resolve, naming its candidates when there are any.

        Returns:
            A user-facing sentence, e.g. "'Microchip Technology' does not name a filer
            in the corpus exactly; did you mean MU (Micron Technology, Inc.)?".
        """
        if not self.candidates:
            return f"'{self.mention}' does not match any filer in the corpus"
        named = " or ".join(
            f"{c.get('ticker') or c.get('cik')} ({c.get('company_name') or 'unknown name'})"
            for c in self.candidates
        )
        return (f"'{self.mention}' does not name a filer in the corpus exactly; "
                f"did you mean {named}?")


class _FilerUniverse:
    """The `entity_master` rows, read at most once per connection, on first use."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._by_cik: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._by_cik is None:
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT cik, trading_symbol, company_name, fye_month, fye_day, last_seen_fy
                      FROM entity_master
                     ORDER BY cik
                    """
                )
                self._by_cik = {row["cik"]: dict(row) for row in cur.fetchall()}
        return self._by_cik

    def rows(self) -> list[tuple[str, str | None, str]]:
        """The universe as ``(cik, ticker, company_name)`` rows for `resolve_company`."""
        return [(r["cik"], r["trading_symbol"], r["company_name"] or "")
                for r in self._load().values()]

    def by_cik(self, cik: str) -> dict[str, Any]:
        """The full `entity_master` row of a CIK in the universe."""
        return self._load()[cik]


def _resolved_from_row(
    mention: str, row: dict[str, Any], resolution: str, similarity: float
) -> ResolvedCompany:
    """A resolved company from an `entity_master` row."""
    return ResolvedCompany(
        mention=mention,
        cik=row["cik"],
        ticker=row["trading_symbol"],
        company_name=row["company_name"],
        fye_month=row["fye_month"],
        fye_day=row["fye_day"],
        last_seen_fy=row["last_seen_fy"],
        resolution=resolution,
        similarity=similarity,
    )


def _candidate(row: dict[str, Any]) -> dict[str, str | None]:
    """A candidate filer as surfaced on an ambiguous `ResolvedCompany`."""
    return {"cik": row["cik"], "ticker": row["trading_symbol"],
            "company_name": row["company_name"]}


@dataclass
class ResolvedPeriod:
    """Result of resolving one `period_mentions[]` entry against fiscal_calendar.

    Each instance is one fiscal year. A mention that names several years (a range
    such as "FY21 to FY23", or a list) resolves to one instance per year, each
    carrying the same `mention` (docs/specs/fiscal-year-range.md); a range longer
    than `MAX_RANGE_YEARS` resolves to one ambiguous instance.
    """

    mention: str
    fiscal_year: int | None = None
    fiscal_period: str = "FY"  # 'FY' | 'Q1' | 'Q2' | 'Q3' | 'Q4'
    ytd: bool = False  # cumulative year-to-date (vs discrete quarter)
    period_type: str = "duration"  # 'instant' | 'duration'
    period_start: str | None = None
    period_end: str | None = None
    ambiguous: bool = False
    reason: str | None = None


class EntityExtractor:
    """SQL-backed resolver for company and period mentions.

    Example:
        >>> extractor = EntityExtractor.from_env()
        >>> companies = extractor.resolve_companies(["NVIDIA"])
        >>> periods = extractor.resolve_periods(["most recent fiscal year"], companies)
    """

    def __init__(
        self,
        db_conn_params: dict | None = None,
        company_aliases: dict[str, str] | None = None,
    ):
        self._db_conn_params = db_conn_params or {}
        # Loaded here, not per query, so a malformed or conflicting alias file fails at
        # construction (startup) rather than degrading every resolution to "ambiguous".
        self._company_aliases = (
            company_aliases if company_aliases is not None else load_company_aliases()
        )

    @classmethod
    def from_env(cls, dotenv_path: str | None = None) -> EntityExtractor:
        """Construct from environment variables loaded from .env."""
        load_dotenv(dotenv_path)
        from src.db.conn import conn_params

        return cls(db_conn_params=conn_params())

    def _connect(self):
        return psycopg2.connect(**self._db_conn_params)

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    def resolve_companies(self, mentions: list[str]) -> list[ResolvedCompany]:
        """Resolve each raw company mention to a `(cik, ticker, ...)` row.

        Tiers, first hit wins (docs/specs/entity-resolution-margin.md): an exact
        (case-insensitive) `trading_symbol`, the committed alias table
        (configs/entities/company_aliases.yaml — "TSMC", "Google"), the filer's name
        after normalization, then a fuzzy name match that must clear a threshold *and*
        beat the runner-up by a margin. Anything else is ``ambiguous`` (naming its
        closest candidates) or ``unresolved`` — never a guess at a lookalike filer.
        """
        if not mentions:
            return []
        try:
            with self._connect() as conn:
                universe = _FilerUniverse(conn)
                return [self._resolve_one_company(conn, m, universe) for m in mentions]
        except Exception as e:
            logger.error(f"Company resolution failed ({e}); returning all as ambiguous")
            return [
                ResolvedCompany(mention=m, resolution="ambiguous") for m in mentions
            ]

    def _resolve_one_company(
        self, conn: Any, mention: str, universe: _FilerUniverse | None = None
    ) -> ResolvedCompany:
        """Resolve one mention: ticker and alias by indexed SQL, names by `resolve_company`.

        Args:
            conn: An open connection.
            mention: The raw company mention.
            universe: The `entity_master` rows, loaded at most once per batch; None
                loads them from *conn* when a name tier is reached.

        Returns:
            The resolved company, or an ambiguous/unresolved one naming its candidates.
        """
        cleaned = mention.strip()
        if not cleaned:
            return ResolvedCompany(mention=mention, resolution="ambiguous")

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1) exact ticker match. ``ticker_ciks`` (every CIK holding the symbol; the
            # column is not unique) turns a shared ticker into ambiguity, not a pick.
            cur.execute(
                """
                SELECT cik, trading_symbol, company_name, fye_month, fye_day, last_seen_fy,
                       array_agg(cik) OVER () AS ticker_ciks
                  FROM entity_master
                 WHERE upper(trading_symbol) = upper(%s)
                 ORDER BY cik
                 LIMIT 1
                """,
                (cleaned,),
            )
            row = cur.fetchone()
            if row:
                ticker_ciks = sorted(set(row.get("ticker_ciks") or [row["cik"]]))
                if len(ticker_ciks) > 1:
                    logger.warning(
                        f"Ticker mention '{mention}' is held by several filers: {ticker_ciks}"
                    )
                    return ResolvedCompany(
                        mention=mention, resolution="ambiguous", similarity=1.0,
                        candidates=[{"cik": c, "ticker": row["trading_symbol"],
                                     "company_name": None} for c in ticker_ciks],
                    )
                return _resolved_from_row(mention, row, "exact_ticker", 1.0)

            # 2) committed alias -> CIK. An alias is authoritative: if its CIK is not an
            # ingested filer, the mention is unresolved rather than handed to fuzzy
            # matching, which would guess at a different company.
            alias_cik = match_company_alias(cleaned, self._company_aliases)
            if alias_cik is not None:
                cur.execute(
                    """
                    SELECT cik, trading_symbol, company_name, fye_month, fye_day, last_seen_fy
                      FROM entity_master
                     WHERE cik = %s
                     LIMIT 1
                    """,
                    (alias_cik,),
                )
                row = cur.fetchone()
                if not row:
                    logger.warning(
                        f"Company alias '{mention}' -> CIK {alias_cik} is not in entity_master"
                    )
                    return ResolvedCompany(mention=mention, resolution="unresolved")
                return _resolved_from_row(mention, row, "alias", 1.0)

        # 3-5) exact normalized name, then fuzzy name with threshold and margin, over the
        # whole entity_master (deterministic code, never the LLM).
        universe = universe if universe is not None else _FilerUniverse(conn)
        match = resolve_company(cleaned, universe.rows(), self._company_aliases)
        if match.cik is not None:
            return _resolved_from_row(
                mention, universe.by_cik(match.cik), match.status, match.score
            )

        candidates = [_candidate(universe.by_cik(cik)) for cik in match.candidates]
        named = ", ".join(f"{c['ticker'] or c['cik']} ({c['company_name']})" for c in candidates)
        logger.warning(
            f"Could not resolve company mention '{mention}' ({match.status}, best "
            f"similarity={match.score:.2f}); candidates: {named or 'none'}"
        )
        return ResolvedCompany(
            mention=mention, resolution=match.status, similarity=match.score,
            candidates=candidates,
        )

    # ------------------------------------------------------------------
    # Periods
    # ------------------------------------------------------------------

    def resolve_periods(
        self, mentions: list[str], companies: list[ResolvedCompany] | None = None
    ) -> list[ResolvedPeriod]:
        """Resolve each raw period mention to a `(fiscal_year, period_start/end)` row.

        Relative phrases ("most recent", "last year") are resolved against the
        first non-ambiguous company's `last_seen_fy`. "calendar YYYY" phrases
        are flagged ambiguous if the company's fiscal year does not end on
        Dec 31 (§1.3 item 6 / §5.2 risk 2).
        """
        if not mentions:
            return []
        companies = companies or []
        primary_company = next((c for c in companies if not c.is_ambiguous), None)
        try:
            with self._connect() as conn:
                return [
                    period
                    for m in mentions
                    for period in self._resolve_one_period(conn, m, primary_company)
                ]
        except Exception as e:
            logger.error(f"Period resolution failed ({e}); returning all as ambiguous")
            return [
                ResolvedPeriod(mention=m, ambiguous=True, reason=str(e))
                for m in mentions
            ]

    def complete_stated_ranges(
        self,
        query: str,
        periods: list[ResolvedPeriod],
        companies: list[ResolvedCompany] | None = None,
    ) -> list[ResolvedPeriod]:
        """Give back the middle years of a range the question states but the mentions split.

        The router LLM may return "FY23 through FY25" as the two mentions "FY23" and "FY25";
        each resolves to its own year and FY2024 is lost. When the question itself states a
        range whose two endpoints are both resolved FY periods, each missing middle year is
        appended as one FY period whose mention is the range as written
        (docs/specs/fiscal-year-range-split.md requirements 6 and 7). A list ("FY23 and FY25")
        states no range and adds nothing.

        Args:
            query: The question the router was given (the sub-query after decomposition).
            periods: The periods ``resolve_periods`` returned for it.
            companies: The resolved companies; the first non-ambiguous one's fiscal calendar
                reads a dated endpoint, as in ``resolve_periods``.

        Returns:
            ``periods`` followed by the added periods (``periods`` itself when none is added).
        """
        resolved_years = sorted({
            p.fiscal_year for p in periods
            if not p.ambiguous and p.fiscal_period == "FY" and p.fiscal_year is not None
        })
        if len(resolved_years) < 2:
            return periods
        company = next((c for c in (companies or []) if not c.is_ambiguous), None)
        bounds = (_MENTION_YEAR_RANGE[0], _MENTION_YEAR_RANGE[1])
        try:
            calendar: list[dict[str, Any]] = []
            if company is not None and company.cik and find_written_dates(query):
                with self._connect() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute(
                            "SELECT fiscal_year, fiscal_period, period_end FROM fiscal_calendar "
                            "WHERE cik = %s",
                            (company.cik,),
                        )
                        calendar = cur.fetchall()
            fill = range_fill(query, resolved_years, calendar, bounds)
            if not fill:
                return periods
            with self._connect() as conn:
                added = [
                    self._resolve_year(conn, text, text.strip().lower(), company, year, "FY",
                                       False)
                    for year, text in fill
                ]
        except Exception as e:
            # Same degradation as resolve_periods: the fill is a completion, so a failed
            # calendar read keeps the periods the mentions resolved.
            logger.error(f"Stated-range completion failed ({e}); keeping the resolved periods")
            return periods
        log_event("decision", point="period_resolution", decision="stated_range_fill",
                  added=[year for year, _text in fill], resolved=resolved_years)
        return [*periods, *added]

    def _resolve_one_period(
        self, conn, mention: str, company: ResolvedCompany | None
    ) -> list[ResolvedPeriod]:
        cleaned = mention.strip().lower()
        if not cleaned:
            return [ResolvedPeriod(
                mention=mention, ambiguous=True, reason="empty_mention"
            )]

        target_period, ytd = _detect_period(cleaned)

        fiscal_year: int | None = None
        if any(p in cleaned for p in RELATIVE_PERIOD_PHRASES):
            fiscal_year = company.last_seen_fy if company else None
            if fiscal_year is None:
                return [ResolvedPeriod(
                    mention=mention,
                    ambiguous=True,
                    reason="relative period phrase but no resolved company to anchor 'most recent' to",
                )]
        elif any(p in cleaned for p in PRIOR_PERIOD_PHRASES):
            if company and company.last_seen_fy is not None:
                fiscal_year = company.last_seen_fy - 1
            else:
                return [ResolvedPeriod(
                    mention=mention,
                    ambiguous=True,
                    reason="relative period phrase but no resolved company to anchor 'prior year' to",
                )]
        elif any(ch.isdigit() for ch in mention):
            # A full written date names the fiscal period that ends on it, by the filer's own
            # calendar (docs/specs/fiscal-year-ended-date.md); None is today's reading.
            dated = self._resolve_dated(conn, mention, cleaned, company, ytd)
            if dated is not None:
                return dated
            # A range or a list names several years: one ResolvedPeriod per year, each with
            # the mention (docs/specs/fiscal-year-range.md). A range too long to expand is
            # ambiguous rather than guessed at.
            overlong = overlong_year_ranges(mention, year_range=_MENTION_YEAR_RANGE)
            if overlong:
                spans = ", ".join(f"{a}-{b}" for a, b in overlong)
                return [ResolvedPeriod(
                    mention=mention,
                    ambiguous=True,
                    reason=(f"range {spans} spans more than {MAX_RANGE_YEARS} fiscal years; "
                            "name the years wanted"),
                )]
            years = extract_years_in_range(mention, year_range=_MENTION_YEAR_RANGE)
            if not years:
                # A digit but no fiscal year in range ("Q3", "the 10-K", "fiscal 2015"): one
                # ambiguous period, so the query degrades rather than guessing a year
                # (docs/specs/no-default-year.md).
                lo, hi = _MENTION_YEAR_RANGE[0], _MENTION_YEAR_RANGE[1]
                return [ResolvedPeriod(
                    mention=mention,
                    ambiguous=True,
                    reason=f"no fiscal year between {lo} and {hi} is written in the mention",
                )]
            return [
                self._resolve_year(conn, mention, cleaned, company, year, target_period, ytd)
                for year in years
            ]

        if fiscal_year is None:
            return [ResolvedPeriod(
                mention=mention, ambiguous=True, reason="could_not_resolve_year"
            )]
        return [self._resolve_year(conn, mention, cleaned, company, fiscal_year, target_period,
                                   ytd)]

    def _resolve_dated(
        self,
        conn: Any,
        mention: str,
        cleaned: str,
        company: ResolvedCompany | None,
        ytd: bool,
    ) -> list[ResolvedPeriod] | None:
        """Resolve a mention that writes a full date through the filer's fiscal calendar.

        Args:
            conn: An open connection.
            mention: The raw period mention.
            cleaned: The mention, stripped and lowercased.
            company: The company the period belongs to, if one resolved.
            ytd: Whether the mention asks for a cumulative year-to-date figure.

        Returns:
            The periods, one ambiguous period when a date matches no single calendar row, or
            None when the mention writes no full date, no company with a cik resolved, or the
            filer has no calendar rows (the caller then reads the mention as before).
        """
        dates = find_written_dates(mention)
        if not dates or company is None or not company.cik:
            return None
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT fiscal_year, fiscal_period, period_end FROM fiscal_calendar "
                "WHERE cik = %s",
                (company.cik,),
            )
            calendar = cur.fetchall()
        result = resolve_dated_mention(mention, calendar,
                                       year_range=(_MENTION_YEAR_RANGE[0], _MENTION_YEAR_RANGE[1]))
        if result is None or result["status"] == "no_calendar":
            return None
        if result["status"] != "resolved":
            return [ResolvedPeriod(mention=mention, ambiguous=True, reason=result["reason"])]

        period, quarter = mention_period_kind(mention)
        for value, _start, _end in dates:
            match = match_period_end(value, calendar, period, quarter)
            if match["status"] == "matched" and match["fiscal_year"] != value.year:
                log_event("decision", point="period_resolution", decision="fiscal_year_ended_date",
                          mention=mention, date=value.isoformat(),
                          fiscal_year=match["fiscal_year"])
        return [
            self._resolve_year(conn, mention, cleaned, company, fiscal_year, fiscal_period, ytd)
            for fiscal_year, fiscal_period in result["periods"]
        ]

    def _resolve_year(
        self,
        conn: Any,
        mention: str,
        cleaned: str,
        company: ResolvedCompany | None,
        fiscal_year: int,
        target_period: str,
        ytd: bool,
    ) -> ResolvedPeriod:
        """Resolve one fiscal year of *mention* against fiscal_calendar.

        Args:
            conn: An open connection.
            mention: The raw period mention.
            cleaned: The mention, stripped and lowercased.
            company: The company the period belongs to, if one resolved.
            fiscal_year: The year to resolve.
            target_period: The detected ``FY`` or quarter.
            ytd: Whether the mention asks for a cumulative year-to-date figure.

        Returns:
            The resolved period, ambiguous on a fiscal-vs-calendar mismatch.
        """
        # fiscal-vs-calendar mismatch check (§1.3 item 6 / §5.2 risk 2)
        if (
            "calendar" in cleaned
            and company is not None
            and company.fye_month is not None
        ):
            fye_is_dec31 = company.fye_month == 12 and (company.fye_day or 31) >= 28
            if not fye_is_dec31:
                return ResolvedPeriod(
                    mention=mention,
                    fiscal_year=fiscal_year,
                    ambiguous=True,
                    reason=(
                        f"'{mention}' refers to a calendar year but "
                        f"{company.ticker or company.cik}'s fiscal year ends "
                        f"{company.fye_month:02d}-{(company.fye_day or 0):02d} — "
                        "clarify fiscal vs calendar."
                    ),
                )

        period_row = None
        if company and company.cik:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Prefer the requested period (a detected quarter, else FY); fall
                # back to whatever period exists for that fiscal year so a quarter
                # mention still resolves the year even if that quarter isn't ingested.
                cur.execute(
                    """
                    SELECT fiscal_period, period_start, period_end
                      FROM fiscal_calendar
                     WHERE cik = %s AND fiscal_year = %s
                     ORDER BY (fiscal_period = %s) DESC, (fiscal_period = 'FY') DESC
                     LIMIT 1
                    """,
                    (company.cik, fiscal_year, target_period),
                )
                period_row = cur.fetchone()

        # The DETECTED period always wins ('FY' or a quarter): fiscal_calendar
        # decides only the date bounds, and its quarterly coverage may lag the
        # fact store. Attach date bounds only when the matched calendar row is for
        # the same period (else leave them None rather than mislabel a quarter's
        # dates as the year's). The factstore looks up by (fiscal_year,
        # fiscal_period), so exact dates are advisory.
        dates_match = bool(period_row) and period_row["fiscal_period"] == target_period
        return ResolvedPeriod(
            mention=mention,
            fiscal_year=fiscal_year,
            fiscal_period=target_period,
            ytd=ytd,
            period_start=str(period_row["period_start"]) if dates_match else None,
            period_end=str(period_row["period_end"]) if dates_match else None,
        )
