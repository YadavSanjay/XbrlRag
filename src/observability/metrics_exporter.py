"""Prometheus metrics exporter over the ``audit.v_*`` analyst views (WS-7 / B8).

docs/design/XbrlRag_Observability_Tracing.md closed
the *capture* side (query_audit / query_event / ingestion_run + analyst views);
this module closes the *export* side: it reads the read-only ``audit.v_*`` views
and renders the **Prometheus text exposition format** (v0.0.4) so an ops team can
scrape latency / cost / error / degradation metrics, dashboard them (Grafana), and
alert on thresholds (see ``configs/monitoring/``).

Design notes
------------
- **No new dependency.** The exposition format is small and stable, so it is
  hand-rendered rather than pulling in ``prometheus_client``. The output validates
  against the Prometheus text parser (``# HELP`` / ``# TYPE`` / escaped labels).
- **Pure add-on, no schema changes.** Reads only the existing views. The row
  source is an injected ``reader`` callable (defaults to :func:`src.api.db.query`,
  which already degrades gracefully to ``[]`` when the DB is down), so the whole
  collector is unit-testable without a database.
- **Snapshot gauges.** Each scrape reflects the current view aggregates. Rates that
  ops alerts fire on (error rate, degradation rate) are derived here from the most
  recent daily rollup so a single scrape is self-contained.

Entry points
------------
- :func:`render_exposition` — ``reader -> str`` (the scrape body).
- ``GET /metrics`` on the BFF (:mod:`src.api.routes.metrics_prom`).
- ``python -m src.observability.metrics_exporter`` — prints the exposition to
  stdout (handy for verification / a standalone textfile-collector deployment).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Dict, List, Optional, Tuple

from src.logging_config import get_logger

logger = get_logger(__name__)

# A reader runs a SQL string and returns a list of dict rows (or [] on failure).
Reader = Callable[[str], list[dict[str, Any]]]

_NS = "arag"  # metric namespace prefix


# ---------------------------------------------------------------------------
# Prometheus text exposition rendering
# ---------------------------------------------------------------------------

def _escape_label_value(value: str) -> str:
    """Escape a label value per the Prometheus text format (\\, \n, ")."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render_labels(labels: Sequence[tuple[str, str]] | None) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape_label_value(str(v))}"' for k, v in labels if v is not None]
    return "{" + ",".join(parts) + "}" if parts else ""


def _fmt_number(value: Any) -> str | None:
    """Render *value* as a Prometheus sample value, or None if not numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return "NaN"
    if f in (float("inf"), float("-inf")):
        return "+Inf" if f > 0 else "-Inf"
    # Integers render without a trailing .0; everything else keeps full precision.
    if f.is_integer():
        return str(int(f))
    return repr(f)


class Exposition:
    """Accumulates metric families and renders the text exposition body.

    Emits each family's ``# HELP`` / ``# TYPE`` header exactly once (Prometheus
    rejects duplicate headers for the same metric name), then its samples.
    """

    def __init__(self) -> None:
        self._headers: list[str] = []           # ordered "name" with header emitted
        self._seen: set = set()
        self._lines: list[str] = []

    def family(self, name: str, help_text: str, mtype: str = "gauge") -> None:
        metric = f"{_NS}_{name}"
        if metric in self._seen:
            return
        self._seen.add(metric)
        self._headers.append(metric)
        # HELP text escapes backslash and newline only.
        help_esc = help_text.replace("\\", "\\\\").replace("\n", "\\n")
        self._lines.append(f"# HELP {metric} {help_esc}")
        self._lines.append(f"# TYPE {metric} {mtype}")

    def sample(
        self,
        name: str,
        value: Any,
        labels: Sequence[tuple[str, str]] | None = None,
    ) -> None:
        """Append one sample. Silently drops non-numeric / NULL values."""
        rendered = _fmt_number(value)
        if rendered is None:
            return
        metric = f"{_NS}_{name}"
        self._lines.append(f"{metric}{_render_labels(labels)} {rendered}")

    def render(self) -> str:
        # Exposition must end with a trailing newline.
        return "\n".join(self._lines) + "\n"


# ---------------------------------------------------------------------------
# Collectors — one per view family
# ---------------------------------------------------------------------------

def _rows(reader: Reader, sql: str) -> list[dict[str, Any]]:
    try:
        return reader(sql) or []
    except Exception as exc:  # pragma: no cover - reader already swallows most
        logger.warning("metrics reader failed for %s: %s", sql.split()[-1], exc)
        return []


def _collect_query_latency(exp: Exposition, reader: Reader) -> None:
    rows = _rows(reader, "SELECT * FROM audit.v_query_latency")
    if not rows:
        return
    r = rows[0]
    exp.family("queries_observed", "Total queries seen in the audit store (all time).")
    exp.sample("queries_observed", r.get("queries"))
    exp.family("query_latency_milliseconds", "End-to-end query latency percentiles (ms).")
    for q, col in (("0.5", "p50_ms"), ("0.95", "p95_ms"), ("0.99", "p99_ms")):
        exp.sample("query_latency_milliseconds", r.get(col), [("quantile", q)])
    exp.family("query_latency_avg_milliseconds", "Mean end-to-end query latency (ms).")
    exp.sample("query_latency_avg_milliseconds", r.get("avg_ms"))
    exp.family("query_latency_max_milliseconds", "Max end-to-end query latency (ms).")
    exp.sample("query_latency_max_milliseconds", r.get("max_ms"))


def _collect_stage_latency(exp: Exposition, reader: Reader) -> None:
    rows = _rows(reader, "SELECT * FROM audit.v_stage_latency")
    if not rows:
        return
    exp.family("stage_latency_milliseconds", "Per-stage latency percentiles (ms).")
    for r in rows:
        stage = r.get("stage")
        if stage is None:
            continue
        for q, col in (("0.5", "p50_ms"), ("0.95", "p95_ms"), ("0.99", "p99_ms")):
            exp.sample(
                "stage_latency_milliseconds",
                r.get(col),
                [("stage", stage), ("quantile", q)],
            )


def _collect_daily(exp: Exposition, reader: Reader) -> None:
    """Most-recent daily rollup + derived error / degradation rates for alerting."""
    rows = _rows(reader, "SELECT * FROM audit.v_query_daily ORDER BY day DESC LIMIT 1")
    if not rows:
        return
    r = rows[0]
    fields = {
        "query_daily_queries": ("queries", "Queries in the most recent audited day."),
        "query_daily_errored": ("errored", "Errored queries in the most recent day."),
        "query_daily_degraded": ("degraded", "Degraded queries in the most recent day."),
        "query_daily_fully_answered": ("fully_answered", "Fully-answered queries (most recent day)."),
        "query_daily_cost_usd": ("cost_usd", "Total query LLM cost USD (most recent day)."),
        "query_daily_tokens": ("tokens", "Total tokens (most recent day)."),
        "query_daily_llm_calls": ("llm_calls", "Total LLM calls (most recent day)."),
        "query_daily_retries": ("retries", "Total retries (most recent day)."),
        "query_daily_p95_latency_milliseconds": ("p95_latency_ms", "p95 latency (most recent day)."),
    }
    for metric, (col, help_text) in fields.items():
        exp.family(metric, help_text)
        exp.sample(metric, r.get(col))

    # Derived rates — what the ops alerts fire on. Guard divide-by-zero.
    queries = _as_float(r.get("queries"))
    if queries and queries > 0:
        errored = _as_float(r.get("errored")) or 0.0
        degraded = _as_float(r.get("degraded")) or 0.0
        exp.family("query_error_rate", "Errored / total queries in the most recent day (0..1).")
        exp.sample("query_error_rate", errored / queries)
        exp.family("query_degradation_rate", "Degraded / total queries in the most recent day (0..1).")
        exp.sample("query_degradation_rate", degraded / queries)


def _collect_branch_mix(exp: Exposition, reader: Reader) -> None:
    rows = _rows(reader, "SELECT * FROM audit.v_branch_mix")
    if not rows:
        return
    exp.family("branch_queries", "Query count per branch/intent.")
    exp.family("branch_degraded", "Degraded query count per branch/intent.")
    exp.family("branch_avg_cost_usd", "Mean cost USD per branch/intent.")
    for r in rows:
        labels = [("branch", r.get("branch") or ""), ("intent", r.get("intent") or "")]
        exp.sample("branch_queries", r.get("queries"), labels)
        exp.sample("branch_degraded", r.get("degraded"), labels)
        exp.sample("branch_avg_cost_usd", r.get("avg_cost_usd"), labels)


def _collect_llm(exp: Exposition, reader: Reader) -> None:
    rows = _rows(reader, "SELECT * FROM audit.v_llm_calls")
    if rows:
        exp.family("llm_calls_total", "LLM calls per stage/model.", "counter")
        exp.family("llm_errors_total", "LLM error calls per stage/model.", "counter")
        exp.family("llm_refusals_total", "LLM refused calls per stage/model.", "counter")
        exp.family("llm_retries_total", "LLM retries per stage/model.", "counter")
        exp.family("llm_cost_usd", "LLM cost USD per stage/model.")
        exp.family("llm_p95_latency_milliseconds", "LLM p95 latency per stage/model (ms).")
        for r in rows:
            labels = [("stage", r.get("stage") or ""), ("model", r.get("model") or "")]
            exp.sample("llm_calls_total", r.get("calls"), labels)
            exp.sample("llm_errors_total", r.get("errors"), labels)
            exp.sample("llm_refusals_total", r.get("refusals"), labels)
            exp.sample("llm_retries_total", r.get("retries"), labels)
            exp.sample("llm_cost_usd", r.get("cost_usd"), labels)
            exp.sample("llm_p95_latency_milliseconds", r.get("p95_latency_ms"), labels)

    cost_rows = _rows(reader, "SELECT * FROM audit.v_model_cost")
    if cost_rows:
        exp.family("model_cost_usd", "Cumulative LLM cost USD per model (billed calls).")
        for r in cost_rows:
            exp.sample("model_cost_usd", r.get("cost_usd"), [("model", r.get("model") or "")])


def _collect_verifier_failures(exp: Exposition, reader: Reader) -> None:
    rows = _rows(reader, "SELECT count(*) AS n FROM audit.v_verifier_failures")
    exp.family("verifier_failures_total", "Queries whose faithfulness verifier could not complete.", "counter")
    n = rows[0].get("n") if rows else 0
    exp.sample("verifier_failures_total", n if n is not None else 0)


def _collect_ingestion(exp: Exposition, reader: Reader) -> None:
    daily = _rows(reader, "SELECT * FROM audit.v_ingestion_daily ORDER BY day DESC LIMIT 20")
    if daily:
        # Collapse to the most recent day per pipeline (rows come day DESC).
        exp.family("ingestion_succeeded", "Items succeeded in the most recent ingestion day, per pipeline.")
        exp.family("ingestion_failed", "Items failed in the most recent ingestion day, per pipeline.")
        exp.family("ingestion_skipped", "Items skipped in the most recent ingestion day, per pipeline.")
        seen_pipelines: set = set()
        for r in daily:
            pipeline = r.get("pipeline") or ""
            if pipeline in seen_pipelines:
                continue
            seen_pipelines.add(pipeline)
            labels = [("pipeline", pipeline)]
            exp.sample("ingestion_succeeded", r.get("succeeded"), labels)
            exp.sample("ingestion_failed", r.get("failed"), labels)
            exp.sample("ingestion_skipped", r.get("skipped"), labels)

    failures = _rows(reader, "SELECT * FROM audit.v_ingestion_failures")
    if failures:
        exp.family("ingestion_failures", "Ingestion item failures per class, split retryable vs permanent.")
        for r in failures:
            labels = [
                ("failure_class", r.get("failure_class") or ""),
                ("retryability", r.get("retryability") or ""),
            ]
            exp.sample("ingestion_failures", r.get("failures"), labels)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Top-level render
# ---------------------------------------------------------------------------

_COLLECTORS: tuple[Callable[[Exposition, Reader], None], ...] = (
    _collect_query_latency,
    _collect_stage_latency,
    _collect_daily,
    _collect_branch_mix,
    _collect_llm,
    _collect_verifier_failures,
    _collect_ingestion,
)


def render_exposition(reader: Reader | None = None) -> str:
    """Render the full Prometheus text exposition over the ``audit.v_*`` views.

    ``reader`` runs a SQL string → list-of-dict-rows. Defaults to the BFF's
    graceful :func:`src.api.db.query`. A DB-up gauge plus a partial-failure count
    are always emitted so a scrape is meaningful even when the store is down.
    """
    if reader is None:
        from src.api import db

        reader = db.query

    exp = Exposition()

    # DB reachability probe — always emitted, drives the AragAuditDBDown alert.
    probe = _rows(reader, "SELECT 1 AS up")
    db_up = 1 if probe else 0
    exp.family("audit_db_up", "1 if the audit DB answered the metrics probe, else 0.")
    exp.sample("audit_db_up", db_up)

    failed_collectors = 0
    for collector in _COLLECTORS:
        try:
            collector(exp, reader)
        except Exception as exc:  # pragma: no cover - defensive; readers swallow
            failed_collectors += 1
            logger.warning("metrics collector %s failed: %s", collector.__name__, exc)

    exp.family("metrics_collector_failures", "Metric collectors that raised during this scrape.")
    exp.sample("metrics_collector_failures", failed_collectors)

    return exp.render()


def main() -> None:  # pragma: no cover - CLI convenience
    import sys

    sys.stdout.write(render_exposition())


if __name__ == "__main__":  # pragma: no cover
    main()
