# XbrlRag UI

React UI for XbrlRag with two consoles on one navigational spine (`query_id`):

- **Business** (Analyst Workspace) — ask fundamental questions about SEC filings;
  see the answer, citations, and a glass-box explainability of how it was built
  (query understanding → plan/sub-queries → fact lookups → computation →
  retrieval → prompts → synthesis).
- **Technical** (System & Observability) — system health, the architecture map,
  the end-to-end **Trace Explorer** (stage waterfall + structured event stream),
  LLM/prompt economics, numeric lineage/audit, ingestion runs, and logs.

Design (personas, information architecture, shared components):
[`docs/design/XbrlRag_UI_Design.md`](../docs/design/XbrlRag_UI_Design.md).

## Architecture

```
React (ui/)  ──/api──►  FastAPI BFF (src/api/)  ──►  pipelines + Postgres audit views
   Vite dev proxy            uvicorn :8000
```

The BFF (`src/api/`) is the thin layer the UI talks to. See [`../src/api/README.md`](../src/api/README.md).

## Prerequisites

- Node from `<node-v24.14.0 folder>`
- Python deps installed (`pip install -r ../requirements.txt`, incl. fastapi/uvicorn)

## Run (two terminals)

**1. Backend (BFF)** — from the repo root:

```bash
uvicorn src.api.main:app --reload --port 8000
```

**2. Frontend** — from `ui/`:

```powershell
$env:Path = "<node-v24.14.0 folder>;$env:Path"
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` → `http://localhost:8000`.

## Graceful degradation

The UI runs even before the DB/models are wired:

- `/api/health` reports audit-DB reachability + pipeline importability (shown on
  the System Overview page).
- Observability pages render an honest "store not reachable" empty state when the
  audit DB isn't configured.
- The Business **Ask** flow needs the pipelines + their models/keys to return a
  real answer; otherwise the BFF returns a clean error string (no 500).

## Layout

```
ui/
  src/
    lib/            api client, types, answer stash, answerModel (sub-query parts), repeats (noise band)
    components/     AppShell, AnswerView, StageStepper, StageWaterfall, EventStream,
                    charts/ (TrendChart, BarRank, Waterfall, MixChart, Sparkline), ui primitives
    pages/business/ Ask, Answer, History, Compare, Company, Bridge, Metrics, Screen
    pages/technical/ Overview, Architecture, Traces, TraceDetail, Llm, Lineage,
                     Ingestion, Logs, Eval, EvalRun, EvalCompare, EvalReliability
```

## Status

Implemented: app shell + persona switch + `query_id` spine; Business Ask→Answer
with explainability, **SSE streaming of stage progress** (`/api/query/stream` via
`EventSource`), History + **Compare**, Company, Bridge, Metrics, Screen; Technical
Overview, Architecture, Trace Explorer (list + waterfall + event stream),
Cost & LLM Economics, Lineage, Ingestion (run → item drill-down + triage/retry), Logs,
and the **Eval console** (run history, run detail with per-case trace drill-down, run
compare; see [guide 11](../docs/guides/xbrl-flow/11-evaluation.md)).

The canonical route → page → data-source inventory is
[xbrl-flow guide 7 §7.4](../docs/guides/xbrl-flow/07-api-ui-observability.md); this file
does not repeat it.

Not built: exports beyond the Metrics CSV, per-user cost chargeback,
`mv_fact_prose_links` fact↔prose surfacing, auth (deferred by decision).
Packaged as the `ui` service (nginx) in [`deploy/README.md`](../deploy/README.md).

**Answer shapes the explainability view understands** (kept in step with the XBRL
pipeline): compound questions answered per sub-query (`metadata.subqueries`), `screen`
and `breakdown` steps with their rows and the filers left out for lack of the requested
year, executor fallback lookups (sibling intersection, derived gross profit), `n/m`
compute steps, and the faithfulness verifier's dropped statements. Citations are joined
to the step that produced them (`lib/answerModel.ts`, `components/sourcesRegistry.ts`),
because the HTML-canonical citation carries only where a fact is, not what it is.
