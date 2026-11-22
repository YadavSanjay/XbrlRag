# XbrlRag BFF (`src/api/`)

Thin FastAPI service. Four concerns:

- **Run queries** — wraps `XbrlRetrievalPipeline` (modes `auto`/`xbrl`) and the
  sec10k `RetrievalPipeline` (`basic`/`hyde`/`subquery`).
- **Read observability** — thin SELECTs over the `audit` schema + analyst views
  (`audit.query_audit`, `audit.query_event`, `audit.v_*`, `audit.ingestion_run*`),
  plus a Prometheus text exposition at root-mounted `GET /metrics`.
- **Read eval results** (`routes/eval.py`) — the same shape over the separate `eval`
  schema, backing the `/tech/eval` console. See
  [xbrl-flow guide 11](../../docs/guides/xbrl-flow/11-evaluation.md).
- **v1 Analyst API** (`src/api/v1/`) — granular, mostly-LLM-free services for
  programmatic (LLM-agent) callers such as Faultline's `scenariogen`. See
  the [v1 section](#v1-analyst-api-apiv1) below.

## Run

```bash
pip install -r requirements.txt          # includes fastapi + uvicorn
uvicorn src.api.main:app --reload --port 8000
```

Or run the packaged `api` service (slim image + compose/Helm) — see
[`deploy/README.md`](../../deploy/README.md).

Open http://localhost:8000/docs for the OpenAPI explorer.

## Config (env)

Reuses the system's existing vars (no new config needed):

- `XBRL_DB_HOST` / `XBRL_DB_PORT` / `XBRL_DB_NAME` / `XBRL_DB_USER` / `XBRL_DB_PASSWORD`
- `XBRLRAG_LOG_FILE` (default: this process's own `logs/xbrlrag_<process>_<pid>.log`) — tailed by `GET /api/logs`
- `XBRLRAG_UI_ORIGINS` — comma-separated CORS origins (default localhost:5173)

## Graceful degradation

The app boots even without a database or models. `GET /api/health` reports
`audit_db` reachability and whether each pipeline module imports. Observability
endpoints return empty result sets when the DB is unreachable, so the UI renders
an honest "store not configured" state instead of erroring.

## Endpoints

**The endpoint tables live in the guides** — one table per surface, so they can't drift
apart from each other:

| Surface | Documented in |
|---|---|
| BFF (`/api/*`), `GET /metrics`, `/api/eval/*` | [xbrl-flow guide 7 §7.2](../../docs/guides/xbrl-flow/07-api-ui-observability.md) |
| v1 Analyst API (`/api/v1/*`) + MCP | [xbrl-flow guide 9](../../docs/guides/xbrl-flow/09-programmatic-access-rest-and-mcp.md) |

In code, [`src/api/v1/router.py`](v1/router.py) is the authoritative v1 surface.
Live schemas: `http://localhost:8000/docs` · `/openapi.json`.

## v1 Analyst API (`/api/v1`)

Granular building blocks of the XBRL pipeline, exposed individually for
programmatic / LLM-tool-calling callers (e.g. `scenariogen`). Unlike `POST
/api/query`, the fact / coverage / company / narrative endpoints make **zero
synthesis-LLM calls** — they're deterministic and cheap, and each maps cleanly
to one LLM tool. Code lives in `src/api/v1/` (`router.py` / `schemas.py` /
`service.py`); each endpoint wraps an existing pipeline component, so
resolution/citation logic has one source of truth.

> Endpoint list, request/response shapes and usage recipes:
> [xbrl-flow guide 9 §9.2](../../docs/guides/xbrl-flow/09-programmatic-access-rest-and-mcp.md).
> The MCP server exposes a **7-tool core subset** of these (no `bridge`/`metrics*`/`screen`).

**No auth.** Intended for a trusted private network / localhost alongside its
callers, same trust model as the UI BFF. Put it behind a gateway before any
public exposure.

**MCP.** The same v1 services are also exposed as Model Context Protocol tools
(`src/mcp_server/`) for LLM-agent hosts that discover tools via `tools/list`
(e.g. `scenariogen`). See [`src/mcp_server/README.md`](../mcp_server/README.md).

**Concurrency.** v1 reuses the same heavy cached singletons (BGE/Jina models,
DB/LLM clients) as the BFF, guarded by a process-wide lock; external callers
serialize on first construction and on the models. Load-test before a
portfolio-wide fan-out.

**Tests.** `test/unit/test_api_v1.py` (service layer mocked — wiring/validation/
mapping) and `test/integration/test_api_v1_live.py` (live-backend endpoint
validation against `xbrl_db`; deterministic endpoints run by default, the
narrative + `/answer` cases are gated behind `RUN_NARRATIVE_E2E=1`). Run the
live suite with:

```bash
conda run -n py312 python -m pytest test/integration/test_api_v1_live.py -v -m integration -s
```
