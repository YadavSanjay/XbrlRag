"""The dimensional views agree with their Python reference (docs/specs/segment-geo-views.md).

`scripts/ddl/mv/32_mv_geographic_facts.sql` (`geo_code`, `geo_name`, `geo_type`,
`pct_of_total`) and `scripts/ddl/mv/31_mv_segment_matrix.sql` (`pct_of_axis_total`) mirror
`src.ingest.sec_edgar.xbrl.segment_geo`. These tests run each committed view body as one
READ-ONLY query against the live xbrl_db (over the live `mv_financial_facts` and
`geography_normalization`; the MVs themselves are not rebuilt, so the committed DDL is tested
before `rebuild_mvs.py` applies it) and compare, for the spec's fixture filings, every row the
view returns with what `resolve_geography` and `shares_of_total` give on the same filing's
facts, whose dimensions are loaded here straight from `context_dimension`.

Skips when the database or a fixture filing is unavailable.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

from src.ingest.sec_edgar.xbrl.segment_geo import (
    is_consolidated,
    resolve_geography,
    shares_of_total,
    view_qname,
)

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
MV_DIR = REPO / "scripts" / "ddl" / "mv"
FIXTURES = REPO / "test" / "spec" / "segment-geo-views" / "fixtures"
CASES = ["adbe_fy2025_revenues", "unp_fy2024_revenue", "meta_fy2024_revenue",
         "tsla_fy2023_revenues", "lly_fy2023_revenues", "tsm_fy2024_geo_members"]
TSLA = "0001628280-24-002390"


def _accession(name: str) -> str:
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return raw["accession_no"]


ACCESSIONS = sorted({_accession(n) for n in CASES})


def _conn_params() -> dict:
    load_dotenv()
    return {
        "host": os.environ.get("XBRL_DB_HOST", "localhost"),
        "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
        "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
        "user": os.environ.get("XBRL_DB_USER", "postgres"),
        "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
    }


def _db_available() -> bool:
    try:
        with psycopg2.connect(**_conn_params()) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mv_financial_facts LIMIT 1")
            cur.execute("SELECT 1 FROM geography_normalization LIMIT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="xbrl_db not reachable")


def _view_body(filename: str) -> str:
    sql = (MV_DIR / filename).read_text(encoding="utf-8")
    sql = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    match = re.search(r"CREATE MATERIALIZED VIEW \w+ AS\s+(.*?);", sql, re.S)
    assert match, f"no view body in {filename}"
    return match.group(1)


def view_rows(cur, filename: str, fact_ids: list[int]) -> dict[int, dict]:
    """The view body's rows for the given facts, by fact_id."""
    body = _view_body(filename).replace("%", "%%")  # LIKE patterns vs psycopg2 params
    cur.execute(f"SELECT * FROM ({body}) v WHERE v.fact_id = ANY(%(ids)s)", {"ids": fact_ids})
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    by_id = {r["fact_id"]: r for r in rows}
    assert len(by_id) == len(rows), f"{filename}: a fact appears more than once"
    return by_id


_FACTS_SQL = """
SELECT m.fact_id, m.accession_no, m.concept, m.unit, m.period_start, m.period_end,
       m.value, m.decimals_value, m.segment_axis, m.segment_member, m.segment_label,
       m.is_canonical_filing, m.is_current_period, m.period_type, f.context_id
  FROM mv_financial_facts m
  JOIN fact f ON f.fact_id = m.fact_id
 WHERE m.accession_no = %(acc)s
"""
_DIMS_SQL = """
SELECT cd.context_id, aq.namespace || ':' || aq.local_name,
       mq.namespace || ':' || mq.local_name, cd.is_default
  FROM context_dimension cd
  JOIN qname aq ON aq.qname_id = cd.dimension_qname_id
  LEFT JOIN qname mq ON mq.qname_id = cd.member_qname_id
 WHERE cd.context_id = ANY(%(ctx)s)
"""
_GN_SQL = "SELECT member_qname, iso_code, geo_name, geo_type FROM geography_normalization"
GEO_AXES = ("srt:StatementGeographicalAxis", "ifrs-full:GeographicalAreasAxis")


def load_facts(cur, acc: str) -> list[dict]:
    """One filing's mv_financial_facts rows with their context's dimension entries."""
    cur.execute(_FACTS_SQL, {"acc": acc})
    cols = [d[0] for d in cur.description]
    facts = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    cur.execute(_DIMS_SQL, {"ctx": sorted({f["context_id"] for f in facts})})
    dims: dict[int, list[dict]] = {}
    for ctx, axis, member, is_default in cur.fetchall():
        dims.setdefault(ctx, []).append({
            "axis": view_qname(axis),
            "member": None if member is None else view_qname(member),
            "is_default": bool(is_default),
        })
    for f in facts:
        f["dimensions"] = dims.get(f["context_id"], [])
        f["decimals"] = f["decimals_value"]
    return facts


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**_conn_params())
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor() as c:
        yield c
    conn.close()


@pytest.fixture(scope="module")
def normalization(cur) -> list[dict]:
    cur.execute(_GN_SQL)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


@pytest.fixture(scope="module")
def filing(cur):
    cache: dict[str, list[dict]] = {}

    def load(acc: str) -> list[dict]:
        if acc not in cache:
            cache[acc] = load_facts(cur, acc)
            if not cache[acc]:
                pytest.skip(f"{acc} not loaded in xbrl_db")
        return cache[acc]

    return load


def _close(view_value, ref: float | None) -> bool:
    if ref is None or view_value is None:
        return ref is None and view_value is None
    # tolerance: numeric division in Postgres vs Decimal division then float; rounding only
    return float(view_value) == pytest.approx(ref, rel=1e-12, abs=1e-15)


@requires_db
@pytest.mark.parametrize("acc", ACCESSIONS)
def test_segment_axis_is_null_exactly_when_consolidated(filing, acc):
    for f in filing(acc):
        assert (f["segment_axis"] is None) == is_consolidated(f["dimensions"]), f["fact_id"]


@requires_db
@pytest.mark.parametrize("acc", ACCESSIONS)
def test_segment_matrix_share_equals_reference(cur, filing, acc):
    facts = filing(acc)
    shares = shares_of_total(facts)
    expected = [f["fact_id"] for f in facts
                if f["segment_axis"] is not None and f["is_canonical_filing"]
                and f["is_current_period"] and f["period_type"] == "duration"]
    rows = view_rows(cur, "31_mv_segment_matrix.sql", [f["fact_id"] for f in facts])
    assert sorted(rows) == sorted(expected)
    bad = [(fid, rows[fid]["pct_of_axis_total"], shares[fid]) for fid in rows
           if not _close(rows[fid]["pct_of_axis_total"], shares[fid])]
    assert not bad, bad[:10]


@requires_db
@pytest.mark.parametrize("acc", ACCESSIONS)
def test_geographic_codes_and_share_equal_reference(cur, filing, normalization, acc):
    facts = filing(acc)
    shares = shares_of_total(facts)
    expected = {f["fact_id"]: f for f in facts
                if f["segment_axis"] in GEO_AXES and f["is_canonical_filing"]
                and f["is_current_period"]}
    rows = view_rows(cur, "32_mv_geographic_facts.sql", [f["fact_id"] for f in facts])
    assert sorted(rows) == sorted(expected)
    for fid, row in rows.items():
        f = expected[fid]
        ref = resolve_geography(f["segment_member"], f["segment_label"], normalization)
        got = {k: row[k] for k in ("geo_code", "geo_name", "geo_type")}
        assert got == ref, (fid, f["segment_member"])
        assert _close(row["pct_of_total"], shares[fid]), (fid, row["pct_of_total"], shares[fid])


@requires_db
def test_tesla_china_is_fact_78189_not_other_international(cur, filing):
    # oracle: Tesla's FY2023 10-K, revenue by geography: China 21,745M (f-1645, country:CN);
    # Other international 29,793M (f-1648) is not China (docs/specs/segment-geo-views.md, C2).
    facts = filing(TSLA)
    rows = view_rows(cur, "32_mv_geographic_facts.sql", [f["fact_id"] for f in facts])
    if 78189 not in rows or 78192 not in rows:
        pytest.skip("Tesla fixture facts not in this database")
    assert rows[78189]["geo_code"] == "CN"
    assert rows[78189]["value"] == Decimal("21745000000")
    assert rows[78192]["geo_code"] == "ROW"
    cn_revenue = [fid for fid, r in rows.items() if r["geo_code"] == "CN"
                  and r["concept"].endswith(":Revenues")]
    assert cn_revenue == [78189]
