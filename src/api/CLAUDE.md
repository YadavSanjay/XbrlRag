# CLAUDE.md — `src/api/` (FastAPI BFF + v1 Analyst API)

One FastAPI app (`uvicorn src.api.main:app`) serving five concerns. **The endpoint tables
live in the guides, not here** — [`07-api-ui-observability.md`](../../docs/guides/xbrl-flow/07-api-ui-observability.md)
(BFF/UI/observability/eval) and [`09-programmatic-access-rest-and-mcp.md`](../../docs/guides/xbrl-flow/09-programmatic-access-rest-and-mcp.md)
(v1); this file is the local invariants. Serving/packaging:
[`deploy/README.md`](../../deploy/README.md).

- `routes/` — the **BFF** for the React UI: `POST /api/query` (+ `/query/stream` SSE),
  `/api/companies`, and the observability SELECTs over the `audit` schema.
- `routes/eval.py` — **`/api/eval/*`**: graded eval runs read from the separate **`eval`
  schema**, backing the `/tech/eval` console. Same thin-SELECT,
  degrade-to-empty shape as the observability routes; the schema is applied by
  `scripts/ddl/eval/_apply.py`, **not** by `bootstrap_db.py`.
- `routes/metrics_prom.py` — **`GET /metrics`** (root-mounted, **not** `/api/*`): a
  Prometheus text-exposition scrape target over the `audit.v_*` views for
  `configs/monitoring/prometheus.yml`. Distinct from the BFF's JSON
  `/api/metrics/*` reads; degrades to `arag_audit_db_up 0` (never 500) when the audit
  DB is down — same graceful-degradation invariant as the rest of this file.
- `v1/` — the **v1 Analyst API** (`/api/v1/*`) for programmatic/LLM-agent callers.
  `router.py` is the **authoritative endpoint surface** (the `__init__.py` docstring is
  a summary — update it too). Endpoints: coverage, concepts, companies/{}, facts/lookup,
  facts/compute, facts/bridge, metrics/catalog, metrics, metrics.csv, **screen**,
  narrative/search, answer.
- `pipelines.py` — lazy singletons: `run_xbrl` (modes `auto`/`xbrl`) and `run_sec10k`
  (`basic`/`hyde`/`subquery`). `run_xbrl` answers through `xbrl_answer`, the one place a
  `RunHalt` the XBRL pipeline raises becomes the halted "not answered" body (a 200 on every
  surface: BFF, SSE, v1, MCP). Any serving caller of the XBRL pipeline must go through it,
  never `pipeline.generate` directly, or a halt becomes a BFF error body / v1 500.

## Invariants

- **Error contract differs by surface:** the BFF **swallows exceptions into a 200 with an
  `error` field** (never 500); v1 returns **real status codes** (404 unresolved, 422
  validation, 500 typed). Do not "fix" the BFF to raise, or v1 to swallow.
- **Graceful degradation:** the app boots without a DB or models; observability endpoints
  return empty sets (not 500s) when the audit DB is down. `GET /api/health` reports it.
- **`query_id`** is the universal correlation key; it is `null` on the sec10k/legacy path.
- **No auth** on either surface — trusted-network trust model; gateway before public exposure.
- v1 + MCP reuse the same cached singletons; concurrent callers serialize on them
  (`RAGINFER_URL` moves embed/rerank off-process). Config reuses `XBRL_DB_*` / `XBRLRAG_*` env.
