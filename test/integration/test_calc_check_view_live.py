"""The SQL sum-of-parts check agrees with its Python reference (docs/specs/calc-check-c11.md).

`scripts/ddl/mv/40_mv_sum_of_parts_checks.sql` mirrors
`src.ingest.sec_edgar.xbrl.calc_check.check_calculations`. These tests run the two view
bodies (12_ inlined as a CTE named `mv_calculation_network`, so the committed DDL is tested
even before `rebuild_mvs.py` applies it) as one READ-ONLY query against the live xbrl_db
and compare, for the five spec fixture filings:

- the view's rows for each fixture tree with `check_calculations` on the committed
  fixture (an excerpt of the same filing, hand-checked against its page);
- every row the view returns for the filing with `check_calculations` on the filing's
  facts and raw calculation arcs, loaded here straight from the base tables.

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

from src.ingest.sec_edgar.xbrl.calc_check import check_calculations

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
MV_DIR = REPO / "scripts" / "ddl" / "mv"
FIXTURES = REPO / "test" / "spec" / "calc-check-c11" / "fixtures"
CASES = ["apple_assets_2023_arcrole", "nvda_assets", "intel_10q_gross_profit",
         "mcd_assets", "amd_financing"]


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
            cur.execute("SELECT 1 FROM network LIMIT 1")
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


def view_sql() -> str:
    """12_ and 40_ as one SELECT, filtered to one accession (%(acc)s)."""
    arcs = _view_body("12_mv_calculation_network.sql")
    checks = _view_body("40_mv_sum_of_parts_checks.sql")
    assert checks.lstrip().startswith("WITH ")
    combined = f"WITH mv_calculation_network AS ({arcs}),\n{checks.lstrip()[len('WITH '):]}"
    return f"SELECT * FROM ({combined}) v WHERE v.accession_no = %(acc)s"


_FACTS_SQL = """
SELECT f.fact_id, q.namespace || ':' || q.local_name AS concept,
       c.entity_identifier AS entity, c.period_start,
       COALESCE(c.period_end, c.period_instant) AS period_end,
       f.unit_id, f.effective_value AS value,
       CASE WHEN f.is_decimals_infinity THEN NULL ELSE f.decimals_value END AS decimals,
       f.context_id
  FROM fact f
  JOIN accession a ON a.accession_id = f.accession_id
  JOIN context c   ON c.context_id = f.context_id
  JOIN element e   ON e.element_id = f.element_id
  JOIN qname q     ON q.qname_id = e.qname_id
 WHERE a.filing_accession_number = %(acc)s AND f.unit_id IS NOT NULL
"""
_DIMS_SQL = """
SELECT cd.context_id, ax.namespace || ':' || ax.local_name,
       COALESCE(mq.namespace || ':' || mq.local_name, cd.typed_text_content)
  FROM context_dimension cd
  JOIN context c ON c.context_id = cd.context_id
  JOIN accession a ON a.accession_id = c.accession_id
  JOIN qname ax ON ax.qname_id = cd.dimension_qname_id
  LEFT JOIN qname mq ON mq.qname_id = cd.member_qname_id
 WHERE a.filing_accession_number = %(acc)s AND NOT cd.is_default
"""
_UNITS_SQL = """
SELECT um.unit_id, um.location_id, q.local_name
  FROM unit_measure um
  JOIN unit u ON u.unit_id = um.unit_id
  JOIN accession a ON a.accession_id = u.accession_id
  JOIN qname q ON q.qname_id = um.qname_id
 WHERE a.filing_accession_number = %(acc)s
"""
_ARCS_SQL = """
SELECT ar.uri, lr.uri, pq.namespace || ':' || pq.local_name,
       cq.namespace || ':' || cq.local_name, rel.calculation_weight
  FROM network n
  JOIN accession a ON a.accession_id = n.accession_id
  JOIN uri ar ON ar.uri_id = n.arcrole_uri_id
  JOIN uri lr ON lr.uri_id = n.extended_link_role_uri_id
  JOIN relationship rel ON rel.network_id = n.network_id
  JOIN element pe ON pe.element_id = rel.from_element_id
  JOIN qname pq ON pq.qname_id = pe.qname_id
  JOIN element ce ON ce.element_id = rel.to_element_id
  JOIN qname cq ON cq.qname_id = ce.qname_id
 WHERE a.filing_accession_number = %(acc)s
"""


def _iso(d) -> str | None:
    return None if d is None else d.isoformat()


def load_filing(cur, acc: str) -> tuple[list[dict], list[dict]]:
    """One filing's numeric facts and every arc, in check_calculations' input form."""
    params = {"acc": acc}
    cur.execute(_DIMS_SQL, params)
    dims: dict[int, list[tuple[str, str]]] = {}
    for ctx, axis, member in cur.fetchall():
        dims.setdefault(ctx, []).append((axis, member))
    cur.execute(_UNITS_SQL, params)
    measures: dict[int, dict[int, list[str]]] = {}
    for unit_id, loc, name in cur.fetchall():
        measures.setdefault(unit_id, {}).setdefault(loc or 1, []).append(name)
    units = {
        uid: "*".join(sorted(m.get(1, []))) + ("/" + "*".join(sorted(m[2])) if 2 in m else "")
        for uid, m in measures.items()
    }
    cur.execute(_FACTS_SQL, params)
    facts = [
        {"fact_id": fid, "concept": concept, "entity": entity, "period_start": _iso(start),
         "period_end": _iso(end), "dimensions": dims.get(ctx, []), "unit": units[uid],
         "value": value, "decimals": decimals}
        for fid, concept, entity, start, end, uid, value, decimals, ctx in cur.fetchall()
    ]
    cur.execute(_ARCS_SQL, params)
    arcs = [{"arcrole": ar, "link_role": lr, "parent": p, "child": c,
             "weight": Decimal(str(w)) if w is not None else Decimal(0)}
            for ar, lr, p, c, w in cur.fetchall()]
    return facts, arcs


def view_results(cur, acc: str) -> list[dict]:
    """The view's rows for one filing, as check_calculations result dicts."""
    cur.execute(view_sql(), {"acc": acc})
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        r = dict(zip(cols, row, strict=True))
        out.append({
            "link_role": r["link_role_uri"], "parent": r["parent_concept"],
            "entity": r["entity"], "period_start": _iso(r["period_start"]),
            "period_end": _iso(r["period_end"]),
            "dimensions": frozenset(zip(r["dimension_axes"], r["dimension_members"],
                                        strict=True)),
            "unit": r["unit"], "status": r["status"], "dimensional": r["dimensional"],
            "reported_low": r["reported_low"], "reported_high": r["reported_high"],
            "computed_low": r["computed_low"], "computed_high": r["computed_high"],
            "parent_fact_ids": tuple(r["parent_fact_ids"]),
            "child_fact_ids": tuple(r["child_fact_ids"]),
            "missing_children": tuple(r["missing_children"]),
        })
    out.sort(key=lambda r: (r["link_role"], r["parent"], r["entity"], r["period_start"] or "",
                            r["period_end"], sorted(r["dimensions"]), r["unit"]))
    return out


def _fixture(name: str) -> tuple[str, list[dict], list[dict]]:
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    facts = [{**{k: f[k] for k in ("fact_id", "concept", "entity", "period_start",
                                   "period_end", "unit", "decimals")},
              "dimensions": [tuple(p) for p in f["dimensions"]],
              "value": None if f["value"] is None else Decimal(f["value"])}
             for f in raw["facts"]]
    arcs = [{**a, "weight": Decimal(a["weight"])} for a in raw["arcs"]]
    return raw["accession_no"], facts, arcs


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**_conn_params())
    conn.set_session(readonly=True)
    with conn.cursor() as c:
        yield c
    conn.close()


def _skip_if_absent(cur, acc: str) -> None:
    cur.execute("SELECT 1 FROM accession WHERE filing_accession_number = %s", (acc,))
    if cur.fetchone() is None:
        pytest.skip(f"{acc} not loaded")


@requires_db
@pytest.mark.parametrize("name", CASES)
def test_view_matches_reference_on_fixture_tree(cur, name):
    acc, facts, arcs = _fixture(name)
    _skip_if_absent(cur, acc)
    want = check_calculations(facts, arcs)
    trees = {(a["link_role"], a["parent"]) for a in arcs}
    got = [r for r in view_results(cur, acc) if (r["link_role"], r["parent"]) in trees]
    assert got == want


@requires_db
@pytest.mark.parametrize("name", CASES)
def test_view_matches_reference_on_whole_filing(cur, name):
    acc = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["accession_no"]
    _skip_if_absent(cur, acc)
    facts, arcs = load_filing(cur, acc)
    want = check_calculations(facts, arcs)
    got = view_results(cur, acc)
    assert len(got) == len(want)
    assert got == want
