"""Live-DB integration test for mv_coverage_card (docs/specs/coverage-card.md).

The spec tests have no Postgres, so this is the only check of the view's tiering: it
recomputes every (cik, fiscal_year, metric) row's tiers in Python from
``mv_financial_facts`` and ``concept_mapping`` by the rule of requirements 3-6, compares
them with the view, and reads every card through ``PostgresCoverageCardSource`` (which
validates each row with ``cell_from_row``).

When ``mv_coverage_card`` does not exist yet (before the first ``rebuild_mvs.py`` run
after this change), the committed DDL is applied inside the test's own transaction, which
is always rolled back: nothing is left behind in the database. When it exists, the session
is read-only. Skips when xbrl_db is unreachable.

    conda run -n py312 python -m pytest test/integration/test_coverage_card_live.py -v -s
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from src.ingest.sec_edgar.coverage.card import coverage_cards
from src.ingest.sec_edgar.coverage.pg_source import PostgresCoverageCardSource

DDL_PATH = (Path(__file__).resolve().parents[2] / "scripts" / "ddl" / "mv"
            / "42_mv_coverage_card.sql")
STATEMENT_TIMEOUT_MS = 900_000

# Requirement 1, written out independently of card.card_metrics().
CONCEPT_METRICS = ["Revenue", "CostOfRevenue", "OperatingIncome", "NetIncome", "TotalAssets",
                   "StockholdersEquity", "CashFromOperations", "Capex"]
AXIS_METRICS = {
    "SegmentAxis": {"us-gaap:StatementBusinessSegmentsAxis", "ifrs-full:SegmentsAxis"},
    "GeographyAxis": {"srt:StatementGeographicalAxis", "ifrs-full:GeographicalAreasAxis"},
}
METRICS = CONCEPT_METRICS + list(AXIS_METRICS)

FILER_YEARS_SQL = """
SELECT cik, fiscal_year, MAX(ticker)
  FROM mv_financial_facts
 WHERE is_canonical_filing AND fiscal_period = 'FY'
 GROUP BY cik, fiscal_year
"""
CONCEPT_FACTS_SQL = """
SELECT DISTINCT cik, fiscal_year, concept, is_extended
  FROM mv_financial_facts
 WHERE is_canonical_filing AND fiscal_period = 'FY'
   AND is_current_period AND segment_axis IS NULL AND value IS NOT NULL
   AND duration_kind IN ('annual', 'instant')
"""
AXIS_FACTS_SQL = """
SELECT DISTINCT cik, fiscal_year, segment_axis, concept
  FROM mv_financial_facts
 WHERE is_canonical_filing AND fiscal_period = 'FY'
   AND is_current_period AND period_type = 'duration' AND value IS NOT NULL
   AND segment_axis = ANY(%s)
"""
MAPPING_SQL = """
SELECT concept_family, qname, preference_rank, cik_override, COALESCE(is_deprecated, FALSE)
  FROM concept_mapping
 WHERE concept_family = ANY(%s)
"""
VIEW_SQL = """
SELECT cik, ticker, fiscal_year, metric, status,
       standard_qnames, extension_qnames, component_qnames
  FROM mv_coverage_card
"""


def _db_available() -> bool:
    try:
        from src.db.conn import connect

        with connect(connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mv_financial_facts LIMIT 1")
            cur.execute("SELECT 1 FROM concept_mapping LIMIT 1")
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="xbrl_db (mv_financial_facts) not reachable"),
]


@pytest.fixture(scope="module")
def card_conn():
    """A connection that sees mv_coverage_card; its transaction is always rolled back."""
    from src.db.conn import connect

    conn = connect(connect_timeout=10, options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.mv_coverage_card')")
            exists = cur.fetchone()[0] is not None
        if exists:
            conn.rollback()
            conn.set_session(readonly=True)
        else:
            with conn.cursor() as cur:
                cur.execute(DDL_PATH.read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _resolved_ranks(mapping_rows) -> tuple[dict, dict]:
    """(family, qname) -> generic rank and (family, qname, cik) -> override rank."""
    generic: dict[tuple[str, str], int] = {}
    override: dict[tuple[str, str, str], int] = {}
    for family, qname, rank, cik_override, deprecated in mapping_rows:
        if deprecated:
            continue
        if cik_override is None:
            generic[(family, qname)] = rank
        else:
            override[(family, qname, cik_override)] = rank
    return generic, override


def _expected(cur) -> dict[tuple[str, int, str], tuple]:
    """Recomputes every row of the view by requirements 3-6."""
    cur.execute(MAPPING_SQL, (CONCEPT_METRICS,))
    generic, override = _resolved_ranks(cur.fetchall())

    def rank(family: str, qname: str, cik: str) -> int | None:
        return override.get((family, qname, cik), generic.get((family, qname)))

    cur.execute(FILER_YEARS_SQL)
    filer_years = {(cik, fy): ticker for cik, fy, ticker in cur.fetchall()}

    tiers: dict[tuple[str, int, str], dict[str, list[tuple[int, str]]]] = {}
    cur.execute(CONCEPT_FACTS_SQL)
    for cik, fy, concept, is_extended in cur.fetchall():
        for metric in CONCEPT_METRICS:
            r = rank(metric, concept, cik)
            if r is None:
                continue
            tier = "component" if 50 <= r <= 99 else "extension" if is_extended else "standard"
            cell = tiers.setdefault((cik, fy, metric), {"standard": [], "extension": [],
                                                         "component": []})
            cell[tier].append((r, concept))

    axes_found: dict[tuple[str, int, str], set[str]] = {}
    all_axes = sorted(set().union(*AXIS_METRICS.values()))
    cur.execute(AXIS_FACTS_SQL, (all_axes,))
    for cik, fy, axis, concept in cur.fetchall():
        r = rank("Revenue", concept, cik)
        if r is None or 50 <= r <= 99:
            continue
        for metric, axes in AXIS_METRICS.items():
            if axis in axes:
                axes_found.setdefault((cik, fy, metric), set()).add(axis)

    out = {}
    for (cik, fy), ticker in filer_years.items():
        for metric in METRICS:
            s: tuple[str, ...]
            e: tuple[str, ...]
            c: tuple[str, ...]
            if metric in AXIS_METRICS:
                s, e, c = tuple(sorted(axes_found.get((cik, fy, metric), ()))), (), ()
            else:
                cell = tiers.get((cik, fy, metric), {"standard": [], "extension": [],
                                                     "component": []})
                s, e, c = (tuple(q for _r, q in sorted(cell[t]))
                           for t in ("standard", "extension", "component"))
            status = ("standard" if s else "extension" if e else "component_only" if c
                      else "absent")
            out[(cik, fy, metric)] = (ticker, status, s, e, c)
    return out


def test_view_tiers_match_the_rule(card_conn):
    with card_conn.cursor() as cur:
        expected = _expected(cur)
        cur.execute(VIEW_SQL)
        got = {(cik, fy, metric): (ticker, status, tuple(s), tuple(e), tuple(c))
               for cik, ticker, fy, metric, status, s, e, c in cur.fetchall()}
    assert expected, "no canonical FY filer-years in mv_financial_facts"
    assert set(got) == set(expected)
    diffs = {k: (got[k], expected[k]) for k in expected if got[k] != expected[k]}
    assert not diffs, f"{len(diffs)} rows differ, e.g. {list(diffs.items())[:3]}"


def test_every_card_reads_and_validates(card_conn):
    cards = coverage_cards(PostgresCoverageCardSource(card_conn))
    assert cards
    assert all(list(card) == METRICS for card in cards.values())
    counts = Counter((c.metric, c.status) for card in cards.values() for c in card.values())
    filers = len({cik for cik, _fy in cards})
    print(f"\ncoverage card: {len(cards)} filer-years, {filers} filers")
    for metric in METRICS:
        row = {s: counts[(metric, s)] for s in ("standard", "extension", "component_only",
                                                  "absent")}
        print(f"  {metric:<20} {row}")
