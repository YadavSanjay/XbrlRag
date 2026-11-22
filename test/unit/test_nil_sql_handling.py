"""Nil handling outside the sign-and-nil spec tests (docs/specs/sign-and-nil.md, Notes).

A filed nil is stored as value NULL. Under ``ORDER BY value DESC`` Postgres puts NULL
first, so every per-filer pick and rank must prefer a valued row explicitly. The database
is faked at ``psycopg2.connect``; the SQL text is checked where only a live database could
check the behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg2
import pytest

from src.retriever.xbrl.screen_disclosure import screen_disclosure_text
from src.retriever.xbrl.xbrl_factstore_breakdown import XbrlFactstoreBreakdown
from src.retriever.xbrl.xbrl_factstore_screen import (
    XbrlFactstoreScreen,
    select_preferred_concept,
    split_nil_rows,
)

REPO = Path(__file__).resolve().parents[2]
MV = REPO / "scripts" / "ddl" / "mv"


class _Cursor:
    def __init__(self, conn, as_dict):
        self._conn = conn
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        self._conn.sql.append(sql)
        if "to_regclass" in sql:
            rows: list[dict] = [{"present": False}]
        elif "entity_master" in sql:
            rows = [{"cik": "0001692819", "ticker": "VST"}]
        else:
            rows = self._conn.answer(sql)
        self._rows = rows if self._as_dict else [tuple(r.values()) for r in rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def mogrify(self, sql, params=None):
        return sql.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, answer):
        self.answer = answer
        self.sql: list[str] = []

    def cursor(self, cursor_factory=None):
        return _Cursor(self, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_nil_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _order_by(sql: str) -> str:
    return " ".join(sql.split("ORDER  BY")[-1].split())


def test_split_nil_rows_keeps_order_and_discloses_each_nil():
    rows = [{"ticker": "VST", "value": None, "fact_id": 146221},
            {"ticker": "AAPL", "value": 0.0, "fact_id": 1},
            {"ticker": "CEG", "value": None, "fact_id": 7},
            {"ticker": "AMD", "value": -5.0, "fact_id": 2}]
    valued, nil = split_nil_rows(rows)
    # a 0 is a value, not a nil
    assert [r["ticker"] for r in valued] == ["AAPL", "AMD"]
    assert nil == [{"ticker": "CEG", "reason": "nil", "fact_id": "7"},
                   {"ticker": "VST", "reason": "nil", "fact_id": "146221"}]


def test_flat_screen_picks_a_valued_row_in_python(monkeypatch):
    # The flat query returns every candidate row of the filer's latest year (no
    # DISTINCT ON); select_preferred_concept picks with candidate_order, which puts a
    # valued row before a filed nil (docs/specs/screen-candidate-order.md R1, R4).
    nil = {"ticker": "VST", "cik": "0001692819", "fiscal_year": 2025, "value": None,
           "unit": "USD", "period_end": "2026-01-01", "fact_id": 146221,
           "concept": "us-gaap:InterestExpense", "concept_pref_rank": 1,
           "concept_role": None, "presented": False, "decimals": None}
    valued = {**nil, "value": 5.0, "fact_id": 7}

    def answer(sql):
        return [dict(nil), dict(valued)] if "unit               = 'USD'" in sql else []

    conn = _Conn(answer)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: conn)
    step = {"id": "s1", "op": "screen", "concept_family": "InterestExpense", "fy": 2025,
            "fiscal_period": "FY", "dimension": "NONE", "order": "desc"}
    XbrlFactstoreScreen(db_conn_params={}).screen(step)
    flat = [s for s in conn.sql if "FROM   mv_financial_facts" in s and "latest_fiscal_year" in s]
    assert flat and all("DISTINCT ON" not in s for s in flat)

    kept, apart = select_preferred_concept([nil, valued])
    assert apart == [] and [r["fact_id"] for r in kept] == [7]


@pytest.mark.parametrize("dimension", [{"segment": "operating segments"},
                                       {"geography": "US"}])
def test_screen_sql_prefers_a_valued_row(monkeypatch, dimension):
    conn = _Conn(lambda sql: [])
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: conn)
    step = {"id": "s1", "op": "screen", "concept_family": "InterestExpense", "fy": 2025,
            "fiscal_period": "FY", "dimension": dimension, "order": "desc"}
    XbrlFactstoreScreen(db_conn_params={}).screen(step)
    # The member screens read every row (no DISTINCT ON) and pick per filer in Python
    # (docs/specs/segment-lookup-screens.md); their row order still puts a valued row
    # first.
    picks = [
        _order_by(s) for s in conn.sql
        if "DISTINCT ON" in s or "FROM   mv_segment_matrix" in s
        or "FROM   mv_geographic_facts" in s
    ]
    assert picks
    for order in picks:
        assert "value DESC NULLS LAST" in order or "(value IS NULL)" in order, order


def test_screen_of_a_nil_only_filer_is_a_nil_exclusion_not_metric_not_tagged(monkeypatch):
    def answer(sql):
        if "mv_segment_matrix" in sql:
            return [{"ticker": "VST", "cik": "0001692819", "fiscal_year": 2025,
                     "value": None, "unit": "USD", "pct_of_total": None,
                     "segment_label": "Operating Segments [Member]",
                     "section_path": None, "fact_id": 146221}]
        return []
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: _Conn(answer))
    step = {"id": "s1", "op": "screen", "concept_family": "InterestExpense", "fy": 2025,
            "fiscal_period": "FY", "dimension": {"segment": "operating segments"}}
    result = XbrlFactstoreScreen(db_conn_params={}).screen(step)
    assert result.disclosure["exclusions"] == [
        {"ticker": "VST", "reason": "nil", "fact_id": "146221"}]
    assert "VST (filed nil, nothing reported)" in screen_disclosure_text(result.disclosure)


def test_breakdown_of_a_nil_total_is_not_found_and_prefers_a_valued_total(monkeypatch):
    def answer(sql):
        if "mv_segment_matrix" in sql:
            return [{"member_label": "A", "value": 1.0, "fact_id": 2, "unit": "USD"}]
        if "mv_financial_facts" in sql:
            return [{"value": None, "fact_id": 1, "unit": "USD"}]
        return []
    conn = _Conn(answer)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: conn)
    step = {"id": "b1", "op": "breakdown", "cik": "0001692819", "fy": 2025,
            "concept_family": "InterestExpense", "axis": "segment"}
    result = XbrlFactstoreBreakdown(db_conn_params={}).breakdown(step)
    assert result.status.value == "NOT_FOUND"
    assert any("nil" in n and "1" in n for n in result.resolution_notes)
    total_sql = next(s for s in conn.sql if "LIMIT  1" in s)
    assert "(f.value IS NULL), f.fact_id DESC" in " ".join(total_sql.split())


def test_mv_segment_matrix_never_ranks_a_nil_first():
    ddl = (MV / "31_mv_segment_matrix.sql").read_text(encoding="utf-8")
    rank = re.search(r"RANK\(\) OVER \((.*?)\) AS segment_rank", ddl, re.S)
    assert rank is not None
    assert "ORDER BY f.value DESC NULLS LAST" in rank.group(1)


def test_mv_derived_metrics_inputs_prefer_a_valued_row():
    ddl = (MV / "34_mv_derived_metrics.sql").read_text(encoding="utf-8")
    picks = re.findall(r"SELECT DISTINCT ON \(cik, fiscal_year, fiscal_period, "
                       r"concept_family\).*?ORDER BY (.*?)\n\)", ddl, re.S)
    assert len(picks) == 2  # prim and inst
    for order in picks:
        flat = " ".join(order.split())
        assert "concept_pref_rank NULLS LAST, (value IS NULL), fact_id" in flat, flat
