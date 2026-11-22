"""The rebuilt views agree with the period-disclosure functions (docs/specs/period-disclosure.md
R7.1, R7.4, R7.5).

READ-ONLY against the live xbrl_db, after `scripts/rebuild_mvs.py` has applied the R7 DDL:

- every duration row's `duration_kind` in `mv_financial_facts` and
  `mv_financial_facts_multidim` equals `duration_class` of its stored dates (R7.1), and the
  spec's C1 facts carry their C1 class;
- `mv_yoy_comparison` carries both periods and their day counts, and AAPL FY2024 revenue
  against FY2023 is disclosed as 364 against 371 days (C3);
- `mv_derived_metrics` averages with the same filing's opening balance, AAPL FY2022 asset
  turnover being 394,328 / ((351,002 + 352,755) / 2) (C4), and every average-based row
  equals `average_based_ratio` over its own cited facts, its formula saying when the
  closing balance was used (R7.5).

Skips when the database is unavailable; fails when the R7 DDL has not been applied.
"""

from __future__ import annotations

import datetime as dt
import os

import psycopg2
import psycopg2.extras
import pytest
from dotenv import load_dotenv

from src.retriever.xbrl.period_disclosure import (
    average_based_ratio,
    duration_class,
    year_length_disclosure,
)

pytestmark = pytest.mark.integration

_CLOSING = "(closing; opening balance not filed)"
# metric -> (numerator role, closing role, opening role) in component_fact_ids.
_AVERAGED = {
    "AssetTurnover": ("revenue", "assets_end", "assets_begin"),
    "ReturnOnAssetsPct": ("net_income", "assets_end", "assets_begin"),
    "InventoryTurnover": ("cost_of_revenue", "inventory_end", "inventory_begin"),
}


def _conn_params() -> dict:
    load_dotenv()
    return {
        "host": os.environ.get("XBRL_DB_HOST", "localhost"),
        "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
        "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
        "user": os.environ.get("XBRL_DB_USER", "postgres"),
        "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
    }


@pytest.fixture(scope="module")
def cur():
    try:
        conn = psycopg2.connect(**_conn_params(), connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(f"xbrl_db unavailable: {e}")
    conn.set_session(readonly=True)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        yield c
    conn.close()


@pytest.mark.parametrize("view", ["mv_financial_facts", "mv_financial_facts_multidim"])
def test_every_duration_row_is_classed_like_duration_class(cur, view):
    cur.execute(  # trusted: view names are the two constants above
        f"SELECT DISTINCT period_days, duration_kind FROM {view} "
        "WHERE period_type = 'duration' AND period_start IS NOT NULL")
    pairs = cur.fetchall()
    assert pairs
    start = dt.date(2000, 1, 1)
    wrong = [
        (r["period_days"], r["duration_kind"]) for r in pairs
        if duration_class(start, start + dt.timedelta(days=r["period_days"])) != r["duration_kind"]
    ]
    assert wrong == []


@pytest.mark.parametrize("fact_id,kind", [
    (94704, "quarter"), (94728, "half"), (52104, "nine_month"), (50640, "quarter"),
    (105742, "annual"), (12096, "annual"), (215829, "quarter"), (116516, "other"),
    (217103, "other"), (218467, "other"), (135373, "other"),
])
def test_c1_facts_carry_their_class(cur, fact_id, kind):
    # oracle: spec C1 (FSDS qtrs, iXBRL contexts and companyfacts start/end).
    cur.execute(
        "SELECT DISTINCT duration_kind FROM mv_financial_facts WHERE fact_id = %s", (fact_id,))
    assert [r["duration_kind"] for r in cur.fetchall()] == [kind]


def test_yoy_rows_carry_their_day_counts(cur):
    cur.execute("""
        SELECT count(*) AS n FROM mv_yoy_comparison
         WHERE period_start_current IS NOT NULL
           AND (days_current <> period_end_current - period_start_current
                OR days_prior <> period_end_prior - period_start_prior
                OR days_prior IS NULL)""")
    assert cur.fetchone()["n"] == 0


def test_aapl_fy2024_revenue_yoy_discloses_the_53_week_year(cur):
    # oracle: spec C3 — 364 days (FY2024) against 371 (FY2023, 53 weeks).
    cur.execute("""
        SELECT period_start_current AS cs, period_end_current AS ce,
               period_start_prior AS ps, period_end_prior AS pe, days_current, days_prior
          FROM mv_yoy_comparison
         WHERE ticker = 'AAPL' AND concept_family = 'Revenue' AND fy_current = 2024
           AND fiscal_period = 'FY' AND duration_kind = 'annual' AND segment_axis IS NULL""")
    rows = cur.fetchall()
    assert rows
    for r in rows:
        assert (r["days_current"], r["days_prior"]) == (364, 371)
        d = year_length_disclosure({"period_start": r["cs"], "period_end": r["ce"]},
                                   {"period_start": r["ps"], "period_end": r["pe"]})
        assert d["differs"] and "53-week" in d["note"]


def test_aapl_fy2022_asset_turnover_uses_the_filings_opening_balance(cur):
    # oracle: spec C4 — 394,328M / ((351,002M + 352,755M) / 2), facts 11023 (opening) and
    # 11022 (closing). rel 1e-9: the inputs are exact integers.
    cur.execute("""
        SELECT value, formula, component_fact_ids AS c FROM mv_derived_metrics
         WHERE ticker = 'AAPL' AND fiscal_year = 2022 AND fiscal_period = 'FY'
           AND metric_name = 'AssetTurnover'""")
    row = cur.fetchone()
    assert float(row["value"]) == pytest.approx(394_328 / ((351_002 + 352_755) / 2), rel=1e-9)
    assert row["formula"] == "Revenue / avg(TotalAssets)"
    assert (row["c"]["assets_begin"], row["c"]["assets_end"]) == (11023, 11022)


@pytest.mark.parametrize("metric", sorted(_AVERAGED))
def test_every_averaged_row_equals_average_based_ratio(cur, metric):
    num_role, end_role, begin_role = _AVERAGED[metric]
    cur.execute("SELECT ticker, fiscal_year, value, formula, component_fact_ids AS c "
                "FROM mv_derived_metrics WHERE metric_name = %s", (metric,))
    rows = cur.fetchall()
    assert rows
    ids = {r["c"][k] for r in rows for k in (num_role, end_role, begin_role) if r["c"].get(k)}
    cur.execute("SELECT DISTINCT ON (fact_id) fact_id, value FROM mv_financial_facts "
                "WHERE fact_id = ANY(%s)", (sorted(ids),))
    value = {r["fact_id"]: float(r["value"]) for r in cur.fetchall()}
    bad = []
    for r in rows:
        c = r["c"]
        opening = value[c[begin_role]] if c.get(begin_role) else None
        expected = average_based_ratio(value[c[num_role]], value[c[end_role]], opening)
        closing_formula = _CLOSING in r["formula"]
        if (expected["value"] != pytest.approx(float(r["value"]), rel=1e-9)
                or closing_formula != (expected["basis"] == "closing")):
            bad.append((r["ticker"], r["fiscal_year"]))
    assert bad == []
