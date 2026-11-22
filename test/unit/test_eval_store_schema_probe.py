"""PostgresEvalStore.start_run refuses an eval schema without result_payload.

A stored checkpointed run writes ``result_payload`` on every case row, so a database whose
``eval`` schema predates the column would start a paid run and then fail every case write.
``start_run`` probes the column first and raises before anything is committed.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from src.eval.core.report import RunReport
from src.eval.core.store import PostgresEvalStore


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if self.conn.missing_column and "result_payload" in sql and sql.lstrip().upper(
        ).startswith("SELECT"):
            raise RuntimeError('column "result_payload" does not exist')  # UndefinedColumn
        self.conn.pending.append(sql)


class _Conn:
    def __init__(self, missing_column: bool, committed: list[list[str]]) -> None:
        self.missing_column = missing_column
        self.committed = committed
        self.pending: list[str] = []

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, exc_type: Any, *exc: Any) -> None:
        if exc_type is None and self.pending:
            self.committed.append(self.pending)
        self.pending = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        self.pending = []


def _store(monkeypatch: pytest.MonkeyPatch, *, missing_column: bool) -> tuple:
    committed: list[list[str]] = []
    driver = types.ModuleType("psycopg2")
    driver.connect = lambda **_: _Conn(missing_column, committed)  # type: ignore[attr-defined]  # fake driver entry point
    monkeypatch.setitem(sys.modules, "psycopg2", driver)
    return PostgresEvalStore(conn_params={}), committed


def _report() -> RunReport:
    return RunReport(run_id="run-probe", suite="s", system="fake", n_planned=1)


def test_start_run_refuses_a_schema_without_result_payload(
        monkeypatch: pytest.MonkeyPatch) -> None:
    store, committed = _store(monkeypatch, missing_column=True)
    with pytest.raises(RuntimeError, match="result_payload"):
        store.start_run(_report())
    assert committed == []


def test_start_run_probes_then_inserts_in_one_transaction(
        monkeypatch: pytest.MonkeyPatch) -> None:
    store, committed = _store(monkeypatch, missing_column=False)
    store.start_run(_report())
    (txn,) = committed
    assert txn[0].lstrip().upper().startswith("SELECT RESULT_PAYLOAD")
    assert "INSERT INTO eval.eval_run" in txn[1]
