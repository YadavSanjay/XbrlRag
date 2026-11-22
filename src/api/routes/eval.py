"""WS-9 / E4 — evaluation endpoints, thin reads over the ``eval`` schema.

Follows the same fail-soft contract as ``observability.py``: if the eval store is
unreachable, endpoints return empty result sets so the UI renders an honest
"no eval runs recorded" state instead of 500ing. View and column names follow
``scripts/ddl/eval/eval_runs.sql``.

**There is deliberately no POST to trigger a run.** A suite run is a multi-minute,
cost-incurring background job, and auth (WS-6/B6) is still deferred — exposing it on
an unauthenticated surface would let anyone spend the OpenAI budget. Runs are
launched from ``scripts/eval/run_suite.py``; revisit once auth lands.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from src.api import db
from src.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/eval", tags=["eval"])


# ----------------------------------------------------------------------------
# Suites
# ----------------------------------------------------------------------------


@router.get("/suites")
def list_suites() -> dict[str, list[dict[str, Any]]]:
    """Known suites, newest generation first."""
    rows = db.query(
        """
        SELECT suite, version, generated_at, corpus_fingerprint, n_cases,
               by_kind, description
        FROM   eval.eval_suite
        ORDER  BY suite, version DESC
        """
    )
    return {"items": rows}


# ----------------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------------


@router.get("/runs")
def list_runs(
    suite: str | None = None,
    system: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Run history for the /tech/eval list, with each run's delta vs the previous
    run of the same suite+system (and whether the corpus changed underneath it)."""
    where, params = [], []
    if suite:
        # Qualified: v_run_trend, joined below, has its own suite and system columns.
        where.append("r.suite = %s")
        params.append(suite)
    if system:
        where.append("r.system = %s")
        params.append(system)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.query(
        f"""
        SELECT r.run_id, r.suite, r.suite_version, r.system, r.tier, r.status,
               r.created_at, r.started_at, r.finished_at,
               r.n_cases, r.n_skipped, r.n_correct, r.n_partial, r.n_incorrect,
               r.n_refused, r.n_error, r.n_hedged_miss, r.n_needs_review,
               r.n_confident_wrong, r.n_imprecise,
               r.accuracy, r.value_accuracy, r.citation_rate, r.grounding_precision,
               r.grounding_recall,
               r.silent_wrong_rate, r.confident_wrong_rate, r.refusal_rate,
               r.hedged_miss_rate, r.imprecise_rate, r.decline_rate,
               r.error_rate, r.grader_version,
               r.paraphrase_stability, r.total_cost_usd,
               r.latency_p50_ms, r.latency_p95_ms,
               r.corpus_fingerprint, r.git_sha,
               t.accuracy_delta, t.silent_wrong_delta, t.prev_run_id, t.corpus_changed
        FROM   eval.eval_run r
        LEFT   JOIN eval.v_run_trend t USING (run_id)
        {clause}
        ORDER  BY r.created_at DESC
        LIMIT  %s OFFSET %s
        """,
        tuple(params) + (limit, offset),
    )
    return {"items": rows}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """Full scorecard + per-kind / per-tag breakdowns for one run."""
    header = db.query_one("SELECT * FROM eval.eval_run WHERE run_id = %s", (run_id,))
    if header is None:
        return {"run": None, "by_kind": [], "by_ticker": [], "instability": []}

    by_kind = db.query(
        "SELECT kind, n, n_correct, accuracy FROM eval.v_run_by_kind "
        "WHERE run_id = %s ORDER BY kind",
        (run_id,),
    )
    # Filer x fiscal-year accuracy — where a systemic weakness becomes visible in
    # one glance ("every dimensional lookup on TSM fails").
    by_ticker = db.query(
        """
        SELECT ticker, fy,
               count(*)                          AS n,
               count(*) FILTER (WHERE passed)    AS n_correct,
               round(avg(CASE WHEN passed THEN 1 ELSE 0 END)::numeric, 4) AS accuracy
        FROM   eval.eval_case_result
        WHERE  run_id = %s AND ticker IS NOT NULL
        GROUP  BY ticker, fy
        ORDER  BY ticker, fy
        """,
        (run_id,),
    )
    instability = db.query(
        "SELECT paraphrase_group, n_variants, n_passed, case_ids, example_question "
        "FROM eval.v_paraphrase_instability WHERE run_id = %s LIMIT 100",
        (run_id,),
    )
    return {
        "run": header,
        "by_kind": by_kind,
        "by_ticker": by_ticker,
        "instability": instability,
    }


@router.get("/runs/{run_id}/cases")
def list_cases(
    run_id: str,
    grade: str | None = None,
    kind: str | None = None,
    tag: str | None = None,
    ticker: str | None = None,
    silent_wrong: bool = False,
    confident_wrong: bool = False,
    failed_only: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Case rows for the drill-down table. ``query_id`` links each row to its trace."""
    where = ["run_id = %s"]
    params: list[Any] = [run_id]
    if grade:
        where.append("grade = %s")
        params.append(grade.upper())
    if kind:
        where.append("kind = %s")
        params.append(kind)
    if tag:
        where.append("%s = ANY(tags)")
        params.append(tag)
    if ticker:
        where.append("ticker = %s")
        params.append(ticker.upper())
    if silent_wrong:
        where.append("silent_wrong")
    if confident_wrong:
        where.append("confident_wrong")
    if failed_only:
        where.append("NOT passed")

    rows = db.query(
        f"""
        SELECT case_id, kind, grade, passed, silent_wrong, needs_review,
               confident_wrong, value_correct, question, reason,
               expected, produced, matched_value, has_citation,
               grounding_precision, grounding_recall, screen_metrics,
               latency_ms, cost_usd, query_id, paraphrase_group, origin,
               ticker, fy, tags, error
        FROM   eval.eval_case_result
        WHERE  {' AND '.join(where)}
        ORDER  BY passed, kind, case_id
        LIMIT  %s OFFSET %s
        """,
        tuple(params) + (limit, offset),
    )
    total = db.query_one(
        f"SELECT count(*) AS n FROM eval.eval_case_result WHERE {' AND '.join(where)}",
        tuple(params),
    )
    return {"items": rows, "total": (total or {}).get("n", 0)}


@router.get("/runs/{run_id}/cases/{case_id}")
def get_case(run_id: str, case_id: str) -> dict[str, Any]:
    row = db.query_one(
        "SELECT * FROM eval.eval_case_result WHERE run_id = %s AND case_id = %s",
        (run_id, case_id),
    )
    return {"case": row}


# ----------------------------------------------------------------------------
# Comparison + gates
# ----------------------------------------------------------------------------


@router.get("/compare")
def compare(a: str, b: str) -> dict[str, Any]:
    """Diff two runs: metric deltas, then flipped cases — regressions first.

    ``corpus_mismatch`` is surfaced rather than hidden: two runs measured on
    different corpus states are not commensurable, and the UI must say so instead of
    rendering a misleading delta.
    """
    run_a = db.query_one("SELECT * FROM eval.eval_run WHERE run_id = %s", (a,))
    run_b = db.query_one("SELECT * FROM eval.eval_run WHERE run_id = %s", (b,))
    if run_a is None or run_b is None:
        return {"run_a": run_a, "run_b": run_b, "regressions": [], "improvements": []}

    flipped = db.query(
        """
        SELECT ca.case_id, ca.kind, ca.question,
               ca.grade AS grade_a, cb.grade AS grade_b,
               ca.passed AS passed_a, cb.passed AS passed_b,
               ca.reason AS reason_a, cb.reason AS reason_b,
               ca.query_id AS query_id_a, cb.query_id AS query_id_b
        FROM   eval.eval_case_result ca
        JOIN   eval.eval_case_result cb ON cb.case_id = ca.case_id AND cb.run_id = %s
        WHERE  ca.run_id = %s AND ca.passed <> cb.passed
        ORDER  BY ca.passed DESC, ca.kind, ca.case_id
        """,
        (b, a),
    )
    return {
        "run_a": run_a,
        "run_b": run_b,
        "corpus_mismatch": run_a.get("corpus_fingerprint")
        != run_b.get("corpus_fingerprint"),
        # passed in A, failing in B == a regression.
        "regressions": [r for r in flipped if r["passed_a"] and not r["passed_b"]],
        "improvements": [r for r in flipped if not r["passed_a"] and r["passed_b"]],
    }


@router.get("/silent-wrong")
def silent_wrong(limit: int = 100) -> dict[str, Any]:
    """Confident, cited, and wrong — across all runs. The release-gating queue."""
    return {"items": db.query("SELECT * FROM eval.v_silent_wrong LIMIT %s", (limit,))}


@router.get("/flaky")
def flaky_cases(suite: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Cases that pass in some runs and fail in others — LLM non-determinism."""
    if suite:
        rows = db.query(
            "SELECT * FROM eval.v_case_flakiness WHERE suite = %s LIMIT %s",
            (suite, limit),
        )
    else:
        rows = db.query("SELECT * FROM eval.v_case_flakiness LIMIT %s", (limit,))
    return {"items": rows}
