"""WS-9 / E3+E4 — unit tests for eval persistence and the eval API.

No live Postgres: the store is exercised against a fake connection that records the
SQL and parameters it would execute, and the routes against a stubbed ``db`` module.
The point is the *mapping* — a report reaching the right columns, and endpoints
degrading to empty rather than 500ing when the store is unreachable.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.eval.core import CallableSystem, EvalCase, EvalRunner, EvalSuite, SystemAnswer
from src.eval.core.store import PostgresEvalStore, _scrub, persist_report_dict

# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------


class _RecordingCursor:
    def __init__(self, sink: list[dict[str, Any]]):
        self._sink = sink

    def execute(self, sql, params=None):
        self._sink.append({"sql": sql, "params": params, "many": False})

    def executemany(self, sql, seq):
        self._sink.append({"sql": sql, "params": list(seq), "many": True})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RecordingConn:
    def __init__(self, fail: bool = False):
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self._fail = fail

    def cursor(self):
        if self._fail:
            raise RuntimeError("connection lost")
        return _RecordingCursor(self.calls)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        self.closed = True


class _Store(PostgresEvalStore):
    """Store bound to a fake connection."""

    def __init__(self, conn):
        super().__init__()
        self._conn = conn

    def _connect(self):
        return self._conn


def _report(**answers: SystemAnswer):
    cases = [
        EvalCase(
            id=cid,
            question=f"q {cid}?",
            kind="lookup",
            expected_value=100.0,
            expected_fact_ids=[1],
            ticker="NVDA",
            fy=2026,
            tags=["revenue"],
            paraphrase_group="grp",
            origin="analyst_queries.sql#1a",
        )
        for cid in answers
    ]
    suite = EvalSuite(name="t", version="v1", cases=cases, corpus_fingerprint="pm:abc")
    system = CallableSystem("stub", lambda q: answers[q.split()[1].rstrip("?")])
    return EvalRunner().run_suite(suite, system, git_sha="deadbeef")


# ---------------------------------------------------------------------------
# Scrubbing — the bug that silently discarded a 407-case run
# ---------------------------------------------------------------------------


class TestScrub:
    def test_strips_nul_from_strings(self):
        assert _scrub("Micron\x00 Technology") == "Micron Technology"

    def test_strips_nul_recursively(self):
        got = _scrub({"a\x00": ["x\x00y", {"b": "c\x00"}], "n": 1})
        assert got == {"a": ["xy", {"b": "c"}], "n": 1}

    def test_leaves_clean_values_untouched(self):
        payload = {"a": ["x", 1, None, True]}
        assert _scrub(payload) == payload

    def test_persisting_an_answer_containing_nul_succeeds(self):
        """Postgres rejects U+0000 in text and jsonb alike. Because persistence is
        atomic and fail-soft, one stray byte previously discarded a whole run."""
        report = _report(
            a=SystemAnswer(text="Micron\x00", numeric_values=[100.0], fact_ids=[1])
        )
        conn = RecordingConn()
        assert _Store(conn).persist_run(report) is True
        blob = repr(conn.calls)
        assert "\\x00" not in blob and "\x00" not in blob


# ---------------------------------------------------------------------------
# Store mapping
# ---------------------------------------------------------------------------


class TestPersistRun:
    def test_writes_one_run_row_and_one_row_per_case(self):
        report = _report(
            a=SystemAnswer(numeric_values=[100.0], fact_ids=[1], cost_usd=0.01),
            b=SystemAnswer(numeric_values=[1.0], fact_ids=[1], cost_usd=0.01),
        )
        conn = RecordingConn()
        assert _Store(conn).persist_run(report) is True

        run_calls = [c for c in conn.calls if not c["many"]]
        case_calls = [c for c in conn.calls if c["many"]]
        assert len(run_calls) == 1
        assert len(case_calls[0]["params"]) == 2

        run = run_calls[0]["params"]
        assert run["n_cases"] == 2
        assert run["n_correct"] == 1
        assert run["accuracy"] == 0.5
        assert run["corpus_fingerprint"] == "pm:abc"
        assert run["git_sha"] == "deadbeef"
        assert run["status"] == "OK"

    def test_case_row_carries_the_trace_correlation_key(self):
        report = _report(
            a=SystemAnswer(numeric_values=[100.0], fact_ids=[1], query_id="qid-123")
        )
        conn = RecordingConn()
        _Store(conn).persist_run(report)
        case = [c for c in conn.calls if c["many"]][0]["params"][0]
        assert case["query_id"] == "qid-123"
        assert case["paraphrase_group"] == "grp"
        assert case["tags"] == ["revenue"]
        assert case["origin"] == "analyst_queries.sql#1a"

    def test_silent_wrong_is_persisted_as_its_own_flag(self):
        report = _report(a=SystemAnswer(numeric_values=[42.0], fact_ids=[9]))
        conn = RecordingConn()
        _Store(conn).persist_run(report)
        case = [c for c in conn.calls if c["many"]][0]["params"][0]
        assert case["grade"] == "INCORRECT"
        assert case["silent_wrong"] is True

    def test_arm_neutral_metrics_reach_their_columns(self):
        # a: right but uncited (PARTIAL, value-correct); b: wrong and uncited
        # (confident-wrong, not silent-wrong); c: right and cited (CORRECT)
        report = _report(
            a=SystemAnswer(numeric_values=[100.0]),
            b=SystemAnswer(numeric_values=[42.0]),
            c=SystemAnswer(numeric_values=[100.0], fact_ids=[1]),
        )
        conn = RecordingConn()
        _Store(conn).persist_run(report)
        run = [c for c in conn.calls if not c["many"]][0]["params"]
        assert run["n_confident_wrong"] == 1
        assert run["confident_wrong_rate"] == pytest.approx(1 / 3)
        assert run["value_accuracy"] == pytest.approx(2 / 3)
        cases = {c["case_id"]: c for c in [c for c in conn.calls if c["many"]][0]["params"]}
        assert cases["a"]["value_correct"] is True and cases["a"]["confident_wrong"] is False
        assert cases["b"]["confident_wrong"] is True and cases["b"]["silent_wrong"] is False
        assert cases["c"]["value_correct"] is True

    def test_db_failure_is_reported_not_raised(self):
        """Losing persistence must not cost the hours of LLM calls that produced
        the results."""
        report = _report(a=SystemAnswer(numeric_values=[100.0], fact_ids=[1]))
        assert _Store(RecordingConn(fail=True)).persist_run(report) is False

    def test_connection_is_always_closed(self):
        conn = RecordingConn()
        _Store(conn).persist_run(
            _report(a=SystemAnswer(numeric_values=[100.0], fact_ids=[1]))
        )
        assert conn.closed is True


class TestTransientRetry:
    """A backend blip must not destroy a case. RagInference dropped out twice under
    eval load and took 31 breakdown cases of a $2 run with it."""

    @staticmethod
    def _system(exc_seq):
        """XbrlRagFullSystem over a stub pipeline that raises from exc_seq, then succeeds."""
        from src.eval.systems.xbrlrag_full import XbrlRagFullSystem

        class _Pipe:
            def __init__(self):
                self.calls = 0

            def generate(self, q):
                self.calls += 1
                if self.calls <= len(exc_seq):
                    raise exc_seq[self.calls - 1]
                return type(
                    "R",
                    (),
                    {
                        "answer_summary": "ok",
                        "execution_result": {},
                        "metadata": {},
                        "unanswered_parts": [],
                    },
                )()

        pipe = _Pipe()
        sys_ = XbrlRagFullSystem(pipeline=pipe, retry_backoff_s=0.0)
        return sys_, pipe

    def test_a_connection_refusal_is_retried(self):
        exc = ConnectionError(
            "ConnectError('[WinError 10061] ... actively refused it')"
        )
        system, pipe = self._system([exc])
        answer = system.answer(EvalCase(id="c", question="q?"))
        assert answer.error is None and pipe.calls == 2

    def test_a_genuine_pipeline_error_is_not_retried(self):
        system, pipe = self._system([ValueError("plan validation failed")])
        answer = system.answer(EvalCase(id="c", question="q?"))
        assert answer.error is not None and pipe.calls == 1

    def test_credit_exhaustion_is_not_retried(self):
        """Retrying 'no credits remaining' just burns wall-clock against a wall."""
        exc = RuntimeError("RateLimitError: 429 - You have no credits remaining.")
        system, pipe = self._system([exc])
        answer = system.answer(EvalCase(id="c", question="q?"))
        assert answer.error is not None and pipe.calls == 1

    def test_retries_are_bounded(self):
        exc = ConnectionError("ConnectError('[WinError 10061]')")
        system, pipe = self._system([exc, exc, exc])
        answer = system.answer(EvalCase(id="c", question="q?"))
        assert answer.error is not None
        assert pipe.calls == 2  # one attempt + one retry, then give up


class TestPersistReportDict:
    def test_reimports_a_serialised_report(self):
        report = _report(
            a=SystemAnswer(
                numeric_values=[100.0], fact_ids=[1], cost_usd=0.02, query_id="q1"
            )
        )
        conn = RecordingConn()
        assert persist_report_dict(report.to_dict(), _Store(conn)) is True
        run = [c for c in conn.calls if not c["many"]][0]["params"]
        assert run["run_id"] == report.run_id
        assert run["n_cases"] == 1
        assert run["accuracy"] == 1.0
        case = [c for c in conn.calls if c["many"]][0]["params"][0]
        assert case["case_id"] == "a" and case["query_id"] == "q1"

    def test_a_report_graded_before_v3_stores_null_not_zero(self):
        """A run that never measured confident-wrong must not read as 'none found'."""
        data = _report(a=SystemAnswer(numeric_values=[42.0])).to_dict()
        for key in ("n_confident_wrong", "confident_wrong_rate", "value_accuracy"):
            data["metrics"].pop(key)
        for case in data["results"]:
            case.pop("confident_wrong")
            case.pop("value_correct")
        conn = RecordingConn()
        assert persist_report_dict(data, _Store(conn)) is True
        run = [c for c in conn.calls if not c["many"]][0]["params"]
        assert run["n_confident_wrong"] is None
        assert run["confident_wrong_rate"] is None
        assert run["value_accuracy"] is None
        case = [c for c in conn.calls if c["many"]][0]["params"][0]
        assert case["confident_wrong"] is None and case["value_correct"] is None

    def test_round_trips_through_json(self):
        import json

        report = _report(a=SystemAnswer(numeric_values=[100.0], fact_ids=[1]))
        data = json.loads(json.dumps(report.to_dict(), default=str))
        conn = RecordingConn()
        assert persist_report_dict(data, _Store(conn)) is True


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    """TestClient with the db layer stubbed — routes are pure SQL-to-JSON mappers."""
    from fastapi.testclient import TestClient

    from src.api import db
    from src.api.main import app

    rows: dict[str, list[dict[str, Any]]] = {"default": []}

    def fake_query(sql, params=None):
        if "eval_case_result" in sql and "count(*)" in sql:
            return [{"n": len(rows.get("cases", []))}]
        if "eval.eval_run" in sql and "eval_case_result" not in sql:
            return rows.get("runs", [])
        if "eval_case_result" in sql or "v_run_by_kind" in sql:
            return rows.get("cases", [])
        return rows.get("default", [])

    def fake_query_one(sql, params=None):
        got = fake_query(sql, params)
        return got[0] if got else None

    monkeypatch.setattr(db, "query", fake_query)
    monkeypatch.setattr(db, "query_one", fake_query_one)
    return TestClient(app), rows


class TestEvalRoutes:
    def test_runs_endpoint_returns_items(self, client):
        c, rows = client
        rows["runs"] = [{"run_id": "r1", "suite": "sql_derived", "accuracy": 0.3}]
        r = c.get("/api/eval/runs")
        assert r.status_code == 200
        assert r.json()["items"][0]["run_id"] == "r1"

    def test_empty_store_degrades_to_empty_not_500(self, client):
        """Matches observability.py's contract: the UI shows an honest empty state."""
        c, _ = client
        for path in (
            "/api/eval/runs",
            "/api/eval/suites",
            "/api/eval/silent-wrong",
            "/api/eval/flaky",
        ):
            r = c.get(path)
            assert r.status_code == 200, path
            assert r.json()["items"] == []

    def test_missing_run_returns_nulls_not_500(self, client):
        c, _ = client
        r = c.get("/api/eval/runs/nope")
        assert r.status_code == 200
        assert r.json()["run"] is None

    def test_cases_endpoint_reports_total(self, client):
        c, rows = client
        rows["cases"] = [{"case_id": "a", "passed": False}]
        r = c.get("/api/eval/runs/r1/cases?failed_only=true")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_compare_with_missing_runs_is_safe(self, client):
        c, _ = client
        r = c.get("/api/eval/compare?a=x&b=y")
        assert r.status_code == 200
        assert r.json()["regressions"] == []

    def test_there_is_no_run_trigger_endpoint(self, client):
        """A run costs real money and auth (WS-6/B6) is deferred — triggering one
        must not be reachable from an unauthenticated surface."""
        c, _ = client
        assert c.post("/api/eval/runs", json={}).status_code in (404, 405)
