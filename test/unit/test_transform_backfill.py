"""Unit tests for the transform repair's helpers and the export plugin's pure parts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ingest.sec_edgar.ingest import transform_backfill
from src.ingest.sec_edgar.ingest.arelle_plugins.export_transformed_facts import (
    fact_row,
    walk_facts,
)
from src.ingest.sec_edgar.ingest.transform_backfill import (
    plan_fact_updates,
    repair_filings,
    source_path,
)

MARKER = "(ixTransformValueError)"


def _fact(value="1", *, numeric=True, nil=False, children=()):
    return SimpleNamespace(
        value=value, isNumeric=numeric, isNil=nil, precision=None, decimals="0",
        isTuple=bool(children), modelTupleFacts=list(children),
    )


def test_walk_reaches_inside_tuples():
    inner = _fact("2")
    outer = _fact(children=[inner])
    assert list(walk_facts([outer, _fact("3")]))[:2] == [outer, inner]
    assert len(list(walk_facts([outer, _fact("3")]))) == 3


def test_a_numeric_fact_is_rounded_as_xbrldb_does():
    row = fact_row(_fact("3"), "f-1", lambda value, _p, _d: Decimal(value))
    assert row == {"xml_id": "f-1", "value": "3", "effective_value": "3"}


def test_a_percent_sign_is_exported_as_the_store_holds_it():
    row = fact_row(_fact("0.500% Notes Due 2033", numeric=False), "f-1", None)
    assert row["value"] == "0.500%% Notes Due 2033"


@pytest.mark.parametrize("fact", [_fact("P5Y", numeric=False), _fact("", nil=True)])
def test_text_and_nil_facts_have_no_effective_value(fact):
    def never(*_args):
        raise AssertionError("roundValue is for numeric, non-nil facts")

    assert fact_row(fact, "f-1", never)["effective_value"] is None


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "three"])
def test_an_unusable_effective_value_stops_the_filing(raw):
    stored = [{"fact_id": 1, "xml_id": "f-1", "fact_value": MARKER}]
    with pytest.raises(ValueError):
        plan_fact_updates(stored, {"f-1": {"value": "x", "effective_value": raw}})


def test_source_path_prefers_the_stored_path(tmp_path):
    document = tmp_path / "a.htm"
    document.write_text("x", encoding="utf-8")
    assert source_path(str(document), None) == document


def test_source_path_moves_a_renamed_root(tmp_path):
    root = tmp_path / "sec_edgar"
    document = root / "KO" / "2023" / "10-K" / "ko.htm"
    document.parent.mkdir(parents=True)
    document.write_text("x", encoding="utf-8")
    old = r"D:\Work\Repos\ARag\data\storage\sec_edgar\KO\2023\10-K\ko.htm"
    assert source_path(old, root) == Path(document)


def test_source_path_raises_when_nothing_exists(tmp_path):
    with pytest.raises(FileNotFoundError):
        source_path(r"D:\gone\sec_edgar\KO\ko.htm", tmp_path)


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params):
        self.conn.statements.append(" ".join(sql.split()))
        self.result = self.conn.filings if "FROM accession" in sql else self.conn.facts

    def fetchall(self):
        return self.result


class _Conn:
    """The database boundary."""

    def __init__(self, facts):
        self.filings = [("0001", 7, "unused")]
        self.facts = facts
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _repair(monkeypatch, conn, *, apply, exported):
    monkeypatch.setattr(transform_backfill, "source_path", lambda *_a: Path("a.htm"))
    monkeypatch.setattr(transform_backfill, "_export_facts", lambda *_a: exported)
    return repair_filings(conn, apply=apply, accessions=None, arelle_cmd="a", root=None, timeout=1)


def test_a_dry_run_writes_nothing(monkeypatch):
    conn = _Conn([(1, "f-1", MARKER)])
    report = _repair(monkeypatch, conn, apply=False,
                     exported={"f-1": {"value": "P5Y", "effective_value": None}})
    assert report["planned"] == 1 and report["applied"] == 0 and report["failed"] == {}
    assert not any(s.startswith("UPDATE") for s in conn.statements)


def test_apply_writes_the_plan(monkeypatch):
    conn = _Conn([(1, "f-1", MARKER)])
    report = _repair(monkeypatch, conn, apply=True,
                     exported={"f-1": {"value": "P5Y", "effective_value": None}})
    assert report["applied"] == 1
    assert sum(s.startswith("UPDATE") for s in conn.statements) == 1


def test_a_mismatch_fails_that_filing_and_writes_nothing(monkeypatch):
    conn = _Conn([(1, "f-1", MARKER), (2, "f-2", "9")])
    report = _repair(monkeypatch, conn, apply=True, exported={
        "f-1": {"value": "P5Y", "effective_value": None},
        "f-2": {"value": "8", "effective_value": "8"},
    })
    assert list(report["failed"]) == ["0001"]
    assert report["applied"] == 0
    assert not any(s.startswith("UPDATE") for s in conn.statements)
    assert conn.rollbacks >= 1
