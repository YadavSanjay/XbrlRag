"""U+0000 never reaches Postgres through the audit or eval persistence paths.

Test strategy area K (docs/design/XbrlRag_Test_Strategy.md). Postgres rejects NUL in
``text`` (psycopg2 refuses the literal) and in ``jsonb`` (the ``\\u0000`` escape), and both
stores write a whole query or run in one transaction, so one stray byte loses all of it.
The stores run against a fake connection that captures every parameter; each parameter
is rendered the way psycopg2 would send it and checked for both forms of NUL.
"""

from __future__ import annotations

from typing import Any

import pytest
from psycopg2.extras import Json

from src.eval.core import CallableSystem, EvalCase, EvalRunner, EvalSuite, SystemAnswer
from src.eval.core.store import PostgresEvalStore, persist_report_dict
from src.observability.audit_store import PostgresAuditStore
from src.observability.trace_context import log_event, trace_query

NUL = "\x00"


class _Cursor:
    def __init__(self, sink: list[Any]):
        self._sink = sink

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self._sink.append(params)

    def executemany(self, sql: str, seq: Any) -> None:
        self._sink.extend(list(seq))


class _Conn:
    def __init__(self) -> None:
        self.params: list[Any] = []

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def cursor(self) -> _Cursor:
        return _Cursor(self.params)

    def close(self) -> None:
        pass


def _wire_values(params: Any) -> list[str]:
    """Every parameter as the text psycopg2 would send (Json adapted, lists flattened)."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, Json):
            out.append(v.dumps(v.adapted))
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
        elif isinstance(v, str):
            out.append(v)

    walk(params)
    return out


def _nul_carriers(params: Any) -> list[str]:
    return [v for v in _wire_values(params) if NUL in v or "\\u0000" in v]


# ---------------------------------------------------------------------------
# audit.query_audit / audit.query_event
# ---------------------------------------------------------------------------


def _trace_with_nul():
    holder = {}
    with trace_query(f"What was Micron{NUL} revenue?", user_id=f"analyst{NUL}",
                     query_id="qid-nul") as tc:
        log_event("llm_call", stage="synthesizer", response=f"Micron{NUL} Technology")
        tc.annotate(branch="PLAN_GENERATOR", fully_answered=True)
        tc.answer = {
            "answer_summary": f"Micron{NUL} revenue was $100.",
            "answer_details": [{"statement": f"x{NUL}", "fact_ids": ["1"], "chunk_ids": []}],
        }
        holder["tc"] = tc
    return holder["tc"]


def test_audit_fake_connection_sees_the_nul_unscrubbed_input():
    # Guard on the probe itself: without scrubbing, the NUL is visible to the checker.
    assert _nul_carriers({"a": Json({"t": NUL}), "b": [f"x{NUL}"]}) == ['{"t": "\\u0000"}', "x\x00"]


def test_audit_persist_scrubs_nul_from_text_and_jsonb(monkeypatch: pytest.MonkeyPatch):
    tc = _trace_with_nul()
    store = PostgresAuditStore(db_conn_params={})
    conn = _Conn()
    monkeypatch.setattr(store, "_connect", lambda: conn)

    store.persist_query(tc)

    assert conn.params, "nothing was written"
    assert _nul_carriers(conn.params) == []


def test_audit_persist_scrubs_a_column_added_to_the_rows(monkeypatch: pytest.MonkeyPatch):
    """Scrubbing is generic at the write boundary: a future column or event field is covered."""
    tc = _trace_with_nul()
    store = PostgresAuditStore(db_conn_params={})
    built = PostgresAuditStore._build_rows

    def build_with_new_fields(trace):
        row, events = built(trace)
        row = {**row, "new_column": f"new{NUL}", "new_json": {f"k{NUL}": [f"v{NUL}"]}}
        events = [(*e[:4], {**e[4], "new_field": f"z{NUL}"}) for e in events]
        return row, events

    monkeypatch.setattr(store, "_build_rows", build_with_new_fields)
    conn = _Conn()
    monkeypatch.setattr(store, "_connect", lambda: conn)

    store.persist_query(tc)

    assert any(isinstance(p, dict) and p.get("new_column") == "new" for p in conn.params)
    assert all(isinstance(p, (dict, tuple)) for p in conn.params)
    assert _nul_carriers(conn.params) == []


# ---------------------------------------------------------------------------
# eval schema
# ---------------------------------------------------------------------------


class _Store(PostgresEvalStore):
    def __init__(self, conn: _Conn):
        super().__init__()
        self._conn = conn

    def _connect(self) -> _Conn:
        return self._conn


def _report(case_text: str = ""):
    """A one-case run whose answer, question and grade text carry a NUL."""
    case = EvalCase(
        id="a", question=f"q a? Micron{NUL}", kind="lookup", expected_value=100.0,
        expected_fact_ids=[1], ticker=f"MU{case_text}", fy=2026,
        tags=[f"revenue{case_text}"], paraphrase_group=f"grp{case_text}",
        origin=f"analyst_queries.sql#1a{case_text}",
    )
    suite = EvalSuite(name="t", version="v1", cases=[case], corpus_fingerprint="pm:abc")
    system = CallableSystem("stub", lambda q: SystemAnswer(
        text=f"Micron{NUL} revenue was 100", numeric_values=[100.0], fact_ids=[1],
        error=f"partial{NUL}"))
    report = EvalRunner().run_suite(suite, system, git_sha="deadbeef")
    report.results[0].grade.reason = f"reason{NUL}"
    return report


def test_eval_persist_run_scrubs_nul_from_answer_and_grade_text():
    conn = _Conn()
    assert _Store(conn).persist_run(_report()) is True
    assert _nul_carriers(conn.params) == []


def test_eval_write_case_scrubs_nul():
    report = _report()
    conn = _Conn()
    assert _Store(conn).write_case(report.run_id, report.results[0]) is True
    assert conn.params and _nul_carriers(conn.params) == []


def test_eval_reimported_report_dict_scrubs_nul():
    conn = _Conn()
    assert persist_report_dict(_report().to_dict(), _Store(conn)) is True
    assert _nul_carriers(conn.params) == []


def test_eval_persist_run_scrubs_nul_from_case_metadata():
    report = _report(case_text=NUL)
    report.notes = f"operator note{NUL}"
    conn = _Conn()
    assert _Store(conn).persist_run(report) is True
    assert _nul_carriers(conn.params) == []


def test_eval_persist_scrubs_a_column_added_to_the_params(monkeypatch: pytest.MonkeyPatch):
    """A new run or case column is scrubbed without a per-field call."""
    import src.eval.core.store as store_mod

    run_cols, case_cols = store_mod.run_columns, store_mod.case_columns
    monkeypatch.setattr(store_mod, "run_columns",
                        lambda r: {**run_cols(r), "new_run_col": f"n{NUL}"})
    # A list value: the per-column string scrub in _case_slice_params would miss it.
    monkeypatch.setattr(store_mod, "case_columns",
                        lambda r: {**case_cols(r), "new_case_col": [f"c{NUL}"]})
    conn = _Conn()

    assert _Store(conn).persist_run(_report()) is True

    dicts = [p for p in conn.params if isinstance(p, dict)]
    assert any(p.get("new_run_col") == "n" for p in dicts)
    assert any(p.get("new_case_col") == ["c"] for p in dicts)
    assert _nul_carriers(conn.params) == []
