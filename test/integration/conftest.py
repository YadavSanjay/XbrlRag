"""Shared fixtures for the XBRL end-to-end integration suite.

These tests exercise the *live* stack: PostgreSQL (`xbrl_db` facts + MVs),
Qdrant (`xbrl_sec10k_text`), and OpenAI (intent/plan/synth/verify). They are
opt-in — run with::

    conda run -n py312 python -m pytest test/integration -v -m integration -s

Any test (or the whole module) skips automatically when a backend is
unreachable, so the suite is safe to leave in the default `pytest` run.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Optional

import psycopg2
import pytest
from dotenv import load_dotenv

# WS-2 / A3: the numeric-tolerance compare + structured-value extraction now live in
# one canonical module; the suite and the golden-set grader share this one
# implementation. Re-exported here so tests importing them from conftest still work.
from src.eval.xbrl.numeric import close_enough, compute_step_values, lookup_step_values

load_dotenv()

# Collected per-query timings, rendered in the terminal summary (see
# ``pytest_terminal_summary``). Each entry: {label, query, seconds, status}.
PERF: list[dict] = []


# ---------------------------------------------------------------------------
# Backend availability probes (mirror the unit-test `requires_db` convention)
# ---------------------------------------------------------------------------

def _pg_dsn() -> dict:
    return dict(
        host=os.getenv("XBRL_DB_HOST", "localhost"),
        port=os.getenv("XBRL_DB_PORT", "5432"),
        dbname=os.getenv("XBRL_DB_NAME", "xbrl_db"),
        user=os.getenv("XBRL_DB_USER", "postgres"),
        password=os.getenv("XBRL_DB_PASSWORD", "postgres"),
    )


def _pg_available() -> bool:
    try:
        with psycopg2.connect(**_pg_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM mv_financial_facts LIMIT 1")
                cur.fetchone()
        return True
    except Exception:
        return False


requires_backends = pytest.mark.skipif(
    not _pg_available(),
    reason="xbrl_db (mv_financial_facts) not reachable",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pg_conn():
    """Read-only connection to xbrl_db for ground-truth fact extraction."""
    if not _pg_available():
        pytest.skip("xbrl_db not reachable")
    conn = psycopg2.connect(**_pg_dsn())
    conn.set_session(readonly=True, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def pipeline():
    """One shared, fully-wired pipeline (construction loads BGE-M3 + Jina, ~10s).

    ``persist_audit=False`` keeps the run from writing to the audit schema.
    Skips the whole suite if the pipeline cannot be built (e.g. Qdrant down,
    models missing, no OPENAI_API_KEY).
    """
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    try:
        from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline

        return XbrlRetrievalPipeline(persist_audit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not construct XbrlRetrievalPipeline: {exc}")


@pytest.fixture
def ground_truth(pg_conn) -> Callable[[str, int, str], float | None]:
    """Return ``(ticker, fiscal_year, concept_family) -> value`` resolving the
    canonical, company-level (non-dimensional) headline fact.

    The authority for "which period is fiscal 2024" is ``fiscal_calendar`` (the
    same table the pipeline's entity resolver trusts), NOT the calendar year of
    the fact's ``period_end``: e.g. Amazon's FY2024 annual period is stored with
    a ``2025-01-01`` boundary, so a naive ``year(period_end)`` match is off by
    one. We therefore join to the ``fiscal_calendar`` FY ``period_end`` and pick
    the annual (>300-day) fact whose own ``period_end`` lands within 2 days of
    it, among canonical, preferred-rank, non-dimensional facts."""

    def _lookup(
        ticker: str, fiscal_year: int, concept_family: str, period_type: str = "duration"
    ) -> float | None:
        # Instant (balance-sheet) facts have no duration, so the >300-day filter
        # only applies to flow/duration concepts.
        duration_guard = (
            "AND (f.period_start IS NULL OR (f.period_end - f.period_start) > 300)"
            if period_type == "duration"
            else ""
        )
        with pg_conn.cursor() as cur:
            cur.execute(
                f"""
                WITH cal AS (
                    SELECT cik, period_end
                    FROM fiscal_calendar
                    WHERE ticker = %s AND fiscal_year = %s AND fiscal_period = 'FY'
                    LIMIT 1
                )
                SELECT f.value
                FROM mv_financial_facts f
                JOIN cal ON f.cik = cal.cik
                WHERE f.concept_family = %s
                  AND f.period_type = %s
                  AND f.has_multiple_dimensions IS NOT TRUE
                  AND f.segment_member IS NULL
                  AND f.is_canonical_filing
                  AND f.concept_pref_rank = 1
                  {duration_guard}
                  AND abs(f.period_end - cal.period_end) <= 2
                ORDER BY abs(f.period_end - cal.period_end)
                LIMIT 1
                """,
                (ticker, fiscal_year, concept_family, period_type),
            )
            row = cur.fetchone()
        return float(row[0]) if row else None

    return _lookup


@pytest.fixture
def dim_ground_truth(pg_conn) -> Callable[..., float | None]:
    """Return ``(ticker, fy, concept_family, axis_like, member_like) -> value`` for
    a dimensional/segment/geographic fact, anchored to the FY ``period_end`` the
    same way as :func:`ground_truth`. ``axis_like``/``member_like`` are SQL LIKE/
    ILIKE patterns (e.g. ``"%ProductOrServiceAxis"``, ``"%data center%"``)."""

    def _lookup(ticker, fiscal_year, concept_family, axis_like, member_like) -> float | None:
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                WITH cal AS (
                    SELECT cik, period_end
                    FROM fiscal_calendar
                    WHERE ticker = %s AND fiscal_year = %s AND fiscal_period = 'FY'
                    LIMIT 1
                )
                SELECT f.value
                FROM mv_financial_facts f
                JOIN cal ON f.cik = cal.cik
                WHERE f.concept_family = %s
                  AND f.segment_axis LIKE %s
                  AND f.segment_label ILIKE %s
                  AND f.is_canonical_filing
                  AND (f.period_start IS NULL OR (f.period_end - f.period_start) > 300)
                  AND abs(f.period_end - cal.period_end) <= 2
                ORDER BY abs(f.period_end - cal.period_end), f.value DESC
                LIMIT 1
                """,
                (ticker, fiscal_year, concept_family, axis_like, member_like),
            )
            row = cur.fetchone()
        return float(row[0]) if row else None

    return _lookup


@pytest.fixture
def run_query(pipeline):
    """Run a query through the pipeline, timing it and recording the result in
    ``PERF`` for the end-of-run performance summary."""

    def _run(query: str, label: str = "", budget: float = 0.0):
        t0 = time.perf_counter()
        resp = pipeline.generate(query)
        elapsed = time.perf_counter() - t0
        status = resp.execution_result.get("status")
        PERF.append(
            {
                "label": label or query,
                "query": query,
                "seconds": elapsed,
                "status": status,
                "budget": budget,
            }
        )
        return resp, elapsed

    return _run


# ---------------------------------------------------------------------------
# Helpers used across tests
# ---------------------------------------------------------------------------

# lookup_step_values / compute_step_values / close_enough are imported from
# src.eval.xbrl.numeric (single source of truth) at the top of this module.


def plan_ops(resp) -> list[str]:
    return [s.get("op") for s in resp.plan.get("steps", [])]


def matches_any(values, expected, rel=1e-6) -> bool:
    """True if any value in *values* is close_enough to *expected*."""
    return any(close_enough(v, expected, rel) for v in values)


# ---------------------------------------------------------------------------
# Terminal summary: accuracy is asserted per-test; here we report performance.
# ---------------------------------------------------------------------------

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not PERF:
        return
    tr = terminalreporter
    tr.write_sep("=", "XBRL e2e — per-query latency")
    tr.write_line(f"{'query':<58} {'status':<8} {'seconds':>8}")
    tr.write_line("-" * 76)
    for row in PERF:
        label = (row["label"][:55] + "...") if len(row["label"]) > 58 else row["label"]
        tr.write_line(f"{label:<58} {str(row['status']):<8} {row['seconds']:>8.2f}")
    times = [r["seconds"] for r in PERF]
    tr.write_line("-" * 76)
    tr.write_line(
        f"n={len(times)}  total={sum(times):.1f}s  "
        f"mean={sum(times)/len(times):.2f}s  "
        f"min={min(times):.2f}s  max={max(times):.2f}s"
    )
