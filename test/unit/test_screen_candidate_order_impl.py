"""Implementation tests for screen-candidate-order (docs/specs/screen-candidate-order.md).

The locked spec tests cover the screen's choice (R1), the mapping additions (R2) and the
facts-count rule (R3); these cover the coverage file's ``fallback`` validation and the flat
screen query's shape (R4). Pure: the only fake is ``psycopg2.connect``.
"""

from __future__ import annotations

import psycopg2
import pytest

from src.ingest.util.generate_concept_mapping import validate_family_coverage
from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

P = {"Cash": ["us-gaap:Cash", "us-gaap:CashRestricted"], "Debt": ["us-gaap:Debt"]}
C = {"Cash": ["us-gaap:CashPart"]}


def _coverage(**raw):
    return validate_family_coverage({"version": 1, **raw}, P, C)


class TestFallbackKey:
    def test_absent_is_empty(self):
        assert _coverage()["fallback"] == {}

    def test_a_primary_is_accepted_as_a_frozenset(self):
        got = _coverage(fallback={"Cash": ["us-gaap:CashRestricted"]})["fallback"]
        assert got == {"Cash": frozenset({"us-gaap:CashRestricted"})}

    @pytest.mark.parametrize("bad", [
        {"Cash": ["us-gaap:CashPart"]},              # a component, not a primary
        {"Cash": ["us-gaap:Debt"]},                  # a primary of another family
        {"Nope": ["us-gaap:Cash"]},                  # unknown family
        {"Cash": "us-gaap:CashRestricted"},          # not a list
        {"Cash": ["us-gaap:Cash", "us-gaap:Cash"]},  # repeated
        ["us-gaap:Cash"],                            # not a mapping
    ])
    def test_anything_else_raises(self, bad):
        with pytest.raises(ValueError, match="fallback"):
            _coverage(fallback=bad)


class _Cursor:
    def __init__(self, sqls: list[str]):
        self._sqls = sqls

    def execute(self, sql, params=None):
        self._sqls.append(sql)

    def fetchall(self):
        return []

    def fetchone(self):
        return (False,)

    def mogrify(self, sql, params=None):
        return sql.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.sql: list[str] = []

    def cursor(self, cursor_factory=None):
        return _Cursor(self.sql)

    def get_dsn_parameters(self):
        return {"dbname": "fake_sco_unit"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def test_flat_query_returns_the_latest_years_candidates_with_the_order_columns(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **kw: conn)
    step = {"id": "s1", "op": "screen", "concept_family": "Revenue", "fy": 2025,
            "fiscal_period": "FY", "dimension": "NONE"}
    XbrlFactstoreScreen(db_conn_params={}).screen(step)
    flat = [s for s in conn.sql if "latest_fiscal_year" in s]
    assert len(flat) == 1
    sql = flat[0]
    assert "DISTINCT ON" not in sql
    assert "unit               = 'USD'" in sql  # the literal the restatement-s1 fake keys on
    for column in ("concept_role", "presented", "AS decimals",
                   "c.fiscal_year = c.latest_fiscal_year"):
        assert column in sql, column
