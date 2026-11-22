"""Member screens rank only the requested duration (docs/specs/period-disclosure.md R7.2).

READ-ONLY against the live xbrl_db: the segment and geographic screens run for real over
`mv_geographic_facts` / `mv_segment_matrix`, and every ranked row's own `duration_kind`
(read back from the view by `fact_id`) is the kind of the requested period. Expected
values are the C2 oracles (iXBRL contexts and FSDS `num`) of the spec.

Skips when the database is unavailable.
"""

from __future__ import annotations

import os

import psycopg2
import pytest
from dotenv import load_dotenv

from src.retriever.xbrl.xbrl_factstore_screen import ScreenStatus, XbrlFactstoreScreen

pytestmark = pytest.mark.integration

US = {"geography": "united states"}


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
def screen() -> XbrlFactstoreScreen:
    params = _conn_params()
    try:
        psycopg2.connect(**params, connect_timeout=3).close()
    except psycopg2.OperationalError as e:
        pytest.skip(f"xbrl_db unavailable: {e}")
    return XbrlFactstoreScreen(db_conn_params=params)


def _run(screen, dimension, family, fy, fp, ytd=False):
    step = {"id": "s1", "op": "screen", "concept_family": family, "fy": fy,
            "fiscal_period": fp, "ytd": ytd, "dimension": dimension,
            "metric_kind": "value", "order": "desc", "top_n": None}
    return screen.screen(step)


def _kinds(fact_ids: list[str], view: str) -> set[str]:
    with psycopg2.connect(**_conn_params()) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT duration_kind FROM {view} WHERE fact_id = ANY(%s)",  # trusted
            ([int(f) for f in fact_ids],),
        )
        return {r[0] for r in cur.fetchall()}


@pytest.mark.parametrize("fp,ytd,kind", [
    ("Q2", False, "quarter"), ("Q2", True, "half"), ("Q3", False, "quarter"),
    ("Q3", True, "nine_month"), ("FY", False, "annual"),
])
def test_geographic_screen_ranks_only_the_requested_kind(screen, fp, ytd, kind):
    result = _run(screen, US, "Revenue", 2026 if fp != "FY" else 2025, fp, ytd)
    assert result.status is ScreenStatus.OK
    assert _kinds([r["fact_id"] for r in result.rows], "mv_geographic_facts") == {kind}


def test_q2_googl_is_the_quarter_and_ytd_the_half(screen):
    # oracle: GOOGL iXBRL f-599 (quarter, 60,846M) and f-603 (half, 114,821M)
    q = {r["ticker"]: r["value"] for r in _run(screen, US, "Revenue", 2026, "Q2").rows}
    h = {r["ticker"]: r["value"] for r in _run(screen, US, "Revenue", 2026, "Q2", True).rows}
    assert q["GOOGL"] == 60846000000.0
    assert h["GOOGL"] == 114821000000.0


def test_fy_segment_screen_never_ranks_mcd_quarter_member(screen):
    # oracle: MCD iXBRL f-906, "Other Restructuring" filed for 2023-10-01..12-31 only
    result = _run(screen, {"segment": "other restructuring"}, "RestructuringCharges", 2023, "FY")
    assert "MCD" not in [r["ticker"] for r in result.rows]
    control = _run(screen, {"segment": "u.s. market"}, "RestructuringCharges", 2023, "FY")
    assert [(r["ticker"], r["value"]) for r in control.rows] == [("MCD", 62400000.0)]
