# XbrlRag XBRL monitoring

Alerting + dashboards + Prometheus metrics export over the existing `audit.v_*`
analyst views. Pure add-on — **no schema changes**; the exporter only *reads* the
views that Phase 3 of the observability design already populates.

## Pieces

| File | Role |
|------|------|
| [`src/observability/metrics_exporter.py`](../../src/observability/metrics_exporter.py) | Renders `audit.v_*` → Prometheus text exposition (dependency-free). |
| `GET /metrics` ([`src/api/routes/metrics_prom.py`](../../src/api/routes/metrics_prom.py)) | Scrape target on the BFF. |
| [`prometheus.yml`](prometheus.yml) | Scrape config + `rule_files`. |
| [`alerts.yml`](alerts.yml) | Alerting rules (latency / error / degradation / cost / DB-down / ingestion). |
| [`grafana_dashboard.json`](grafana_dashboard.json) | Grafana dashboard over the `arag_*` metrics. |

## Metrics

All metrics are namespaced `arag_*`. Highlights:

- `arag_audit_db_up` — 1/0 DB reachability probe (drives `AragAuditDBDown`).
- `arag_query_latency_milliseconds{quantile}` — end-to-end p50/p95/p99 (`v_query_latency`).
- `arag_stage_latency_milliseconds{stage,quantile}` — per-stage percentiles (`v_stage_latency`).
- `arag_query_error_rate`, `arag_query_degradation_rate` — derived from the most recent daily rollup (`v_query_daily`).
- `arag_query_daily_cost_usd`, `arag_query_daily_*` — recent-day economics/volume.
- `arag_branch_queries{branch,intent}` etc. — routing mix (`v_branch_mix`).
- `arag_llm_calls_total` / `arag_llm_errors_total` / `arag_llm_refusals_total` / `arag_llm_cost_usd` `{stage,model}` (`v_llm_calls`), `arag_model_cost_usd{model}` (`v_model_cost`, billed calls: successes plus billed refusals).
- `arag_verifier_failures_total` — faithfulness-verifier failures (`v_verifier_failures`).
- `arag_ingestion_{succeeded,failed,skipped}{pipeline}`, `arag_ingestion_failures{failure_class,retryability}`.

Each scrape is a **snapshot** of the view aggregates; the store degrades to
`arag_audit_db_up 0` (plus otherwise-empty metrics) when the DB is unreachable,
so a scrape always returns a parseable body.

## Quick start

```bash
# 1. Serve the exporter (part of the BFF).
conda run -n py312 uvicorn src.api.main:app --port 8000
curl -s localhost:8000/metrics | head -40          # sanity-check the exposition

# ...or render standalone without the web server:
conda run -n py312 python -m src.observability.metrics_exporter

# 2. Scrape it (adjust the target in prometheus.yml first).
prometheus --config.file=configs/monitoring/prometheus.yml
promtool check rules configs/monitoring/alerts.yml  # validate the rules

# 3. Dashboard: Grafana -> Dashboards -> Import -> grafana_dashboard.json,
#    pointed at the Prometheus data source above.
```

In the deployment stack this exporter is scraped by the bundled
Prometheus; here it targets `localhost:8000` for local/dev use.

## Acceptance check (B8)

> latency/cost/error metrics from `audit.v_*` appear on a dashboard and an alert
> fires when a threshold (e.g. p95 latency or error rate) is breached.

- **Metrics appear:** `curl localhost:8000/metrics` shows `arag_query_latency_milliseconds`,
  `arag_query_daily_cost_usd`, `arag_query_error_rate`, etc.; the Grafana dashboard renders them.
- **Alert fires:** e.g. `AragHighErrorRate` (`arag_query_error_rate > 0.10`) or
  `AragHighP95Latency` (`arag_query_latency_milliseconds{quantile="0.95"} > 30000`)
  transition to FIRING in the Prometheus UI when breached. Lower a threshold in
  `alerts.yml` to force a demo fire against current data.
