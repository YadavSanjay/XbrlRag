"""A Q4 lookup answers MSFT FY2026 over the live fact store (docs/specs/q4-lookup.md).

READ-ONLY against the live xbrl_db: `XbrlFactstoreLookup.lookup` with `fiscal_period` Q4
runs for real. Expected values are the spec's FSDS `num` oracles (C1, C2): revenue FY -
nine months = 331,839M - 241,832M = 90,007M (derived); dividends per share 0.91 (filed in
the 10-K); total assets at Q4 is the FY-end balance with the Q4 note.

Skips when the database is unavailable.
"""

from __future__ import annotations

import os

import psycopg2
import pytest
from dotenv import load_dotenv

from src.retriever.xbrl.xbrl_factstore_lookup import (
    Q4_DIMENSIONAL_REASON,
    Q4_INSTANT_NOTE,
    LookupStatus,
    XbrlFactstoreLookup,
)

pytestmark = pytest.mark.integration

MSFT_CIK = "0000789019"


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
def lookup() -> XbrlFactstoreLookup:
    params = _conn_params()
    try:
        psycopg2.connect(**params, connect_timeout=3).close()
    except psycopg2.OperationalError as e:
        pytest.skip(f"xbrl_db unavailable: {e}")
    return XbrlFactstoreLookup(db_conn_params=params)


def _step(family: str, **kw) -> dict:
    return {"id": "s1", "op": "lookup", "cik": MSFT_CIK, "ticker": "MSFT", "fy": 2026,
            "concept_family": family, "fiscal_period": "Q4", **kw}


def test_msft_fy2026_q4_revenue_is_derived(lookup):
    # oracle: FSDS num 10-K qtrs 4 at 20260630 = 331,839,000,000; 10-Q qtrs 3 at 20260331 =
    # 241,832,000,000 (spec C1)
    r = lookup.lookup(_step("Revenue"))
    assert r.status == LookupStatus.OK, r.resolution_notes
    assert r.value == 90_007_000_000
    assert r.unit == "USD"
    assert r.q4["status"] == "derived"
    assert (r.q4["period_start"], r.q4["period_end"], r.q4["days"]) == (
        "2026-04-01", "2026-06-30", 91)
    assert len(r.q4["fact_ids"]) == 2 and r.citation.fact_id == r.q4["fact_ids"][0]


def test_msft_fy2026_q4_dividends_per_share_is_filed(lookup):
    # oracle: FSDS num 10-K qtrs 1 at 20260630 = 0.91 (spec C2)
    r = lookup.lookup(_step("DividendsPerShare"))
    assert r.status == LookupStatus.OK, r.resolution_notes
    assert r.value == pytest.approx(0.91, abs=1e-9)  # a two-decimal per-share value
    assert r.unit == "USD/shares"
    assert r.q4["status"] == "filed"


def test_msft_q4_total_assets_is_the_fy_end_balance(lookup):
    fy = lookup.lookup(_step("TotalAssets", fiscal_period="FY"))
    q4 = lookup.lookup(_step("TotalAssets"))
    assert q4.status == fy.status == LookupStatus.OK
    assert q4.value == fy.value and q4.citation.fact_id == fy.citation.fact_id
    assert Q4_INSTANT_NOTE in q4.resolution_notes


def test_a_dimensional_q4_is_not_found(lookup):
    r = lookup.lookup(_step("Revenue", dimensions={"segment": "intelligent cloud"}))
    assert r.status == LookupStatus.NOT_FOUND and r.error == Q4_DIMENSIONAL_REASON
