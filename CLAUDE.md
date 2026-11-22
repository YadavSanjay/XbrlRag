# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

XbrlRag answers financial-analyst questions over **SEC filings** (10-K / 10-Q / 20-F) with
**cited, audited numbers**: every figure resolves to a source XBRL `fact_id` via
deterministic SQL, and the LLM narrates rather than calculates.

The **XBRL / iXBRL flow is the product** — SEC EDGAR iXBRL → Arelle ingest → a Postgres
fact store → a plan-based query pipeline → LLM synthesis → an independent faithfulness
verifier. A legacy **PDF/VLM** SEC-10K flow also exists and is **maintenance-only** (no
new features — see [`VISION.md`](VISION.md) non-goals). When a change could target
either, it targets the XBRL flow.

## Operational Guides

End-to-end setup and how-to guides live in [`docs/guides/`](docs/guides/README.md), one
folder per flow: the **XBRL flow** ([`docs/guides/xbrl-flow/`](docs/guides/xbrl-flow/README.md),
guides 1–12: setup, ingest, vector load, retrieval, API/UI, troubleshooting, programmatic
access, packaging, evaluation, held-out sets), the retained **HTML 10-K flow**
([`docs/guides/html-flow/`](docs/guides/html-flow/README.md)) and the **PDF legacy flow**
([`docs/guides/pdf-legacy-flow/`](docs/guides/pdf-legacy-flow/README.md)). Evaluation
protocol records (preregistrations, sealed hashes, calibration data; read-only) are indexed
in [`docs/eval/`](docs/eval/README.md).

**Design references** (current state, not history) live in
[`docs/design/`](docs/design/README.md): architecture, HTML-canonical source, fact-store
data model and its invariants, LLM harness, observability, evaluation, UI, local/AWS
deployment. Change-level test-first specs are in `docs/specs/<id>.md` (the tdd-spec
workflow, see [`README.md`](README.md#test-first-workflow-tdd-spec)).

**Module-level `CLAUDE.md` files** capture local invariants/gotchas for the most
complex modules (auto-loaded when you work in them): the XBRL query pipeline
([`src/retriever/xbrl/`](src/retriever/xbrl/CLAUDE.md)), XBRL ingest
([`src/ingest/sec_edgar/`](src/ingest/sec_edgar/CLAUDE.md)), the API/BFF+v1
([`src/api/`](src/api/CLAUDE.md)), and the MCP server
([`src/mcp_server/`](src/mcp_server/CLAUDE.md)). This root file is the whole-repo map.

## Scope & dev-time harness

- **[`VISION.md`](VISION.md)** — the durable scope doc: mission, principles, and
  explicit **non-goals** (e.g. the PDF-legacy path is maintenance-only). Decline
  out-of-scope work against it.
- **[`docs/harness/README.md`](docs/harness/README.md)** — the dev-time harness:
  a machine-checkable **feature manifest** + gate (`python scripts/harness/check_features.py`
  — **the authoritative live implementation status**; prefer it over any status
  written into a doc), the
  runaway-process **watchdog**, the **re-audit cadence**, and the **subagents**.
  Deterministic **hooks** (secret guard, auto-format, red-build Stop-gate) live in
  [`.claude/hooks/`](.claude/hooks/README.md); dev-time **subagents** in
  [`.claude/agents/`](docs/harness/SUBAGENTS.md).

## Deployment

Packaged serving stack (BFF `api` + MCP + React `ui`) — slim Docker image, compose,
and a Helm chart: see [`deploy/README.md`](deploy/README.md). The image is torch-free
because it reaches BGE-M3/Jina over HTTP via `RAGINFER_URL`.

**Local vs AWS (dual-mode).** The same code runs locally or on AWS via
`XBRLRAG_DEPLOY_MODE` (`local` default | `aws`), which selects **backends/policies**, not
endpoints — filings on local FS vs **S3** (`src/config/deploy.py`,
`src/ingest/sec_edgar/storage.py` `FilingStore`), Postgres `sslmode` `prefer` vs
`require` for RDS (`src/db/conn.py`), and Qdrant reached via the `QDRANT_*` env.
Query-time serving is filesystem-free; S3 matters only for ingest, which gets its own
**torch-free** image ([`infra/docker/Dockerfile.ingest`](infra/docker/Dockerfile.ingest),
`arelle-release` on Linux; embeds via `RAGINFER_URL` like serving). On AWS, ingest runs in
the hybrid mode (filings on the runner's local disk): the read-side stages are not yet
S3-aware, so pure-S3 ingest silently skips every filing (not built). Seams, images, topology, open items:
[`docs/design/XbrlRag_AWS_Deployment.md`](docs/design/XbrlRag_AWS_Deployment.md).

## Python environment

**Always use the `py312` conda environment** for running anything in this repo
(tests, pipelines, scripts). It has the full dependency set (FlagEmbedding, Arelle,
psycopg2, etc.). Do not use the base `python` on PATH. Invoke via:

```bash
conda run -n py312 python -m pytest ...
conda run -n py312 python -m src.pipelines.xbrl.retrieval_pipeline
```

## Before any run that spends money

Before **every** command that makes paid LLM calls (`run_suite.py`, slices, pilots, smoke
runs, probes, ingest with embeddings), check the environment it will use, and see each
check pass. Checking a repo's git commit is not checking that its service is running.

1. **Postgres:** connect and print `current_database()` and the corpus fingerprint;
   both must be the ones intended (a held-out run must show `xbrl_heldout`).
2. **Qdrant:** `GET /` on the intended port answers, and the `xbrl_sec10k_*`
   collections exist with the expected point counts.
3. **RagInference:** `GET /healthz` at `RAGINFER_URL` answers and names the expected
   embedder and reranker. `naive_rag_*` needs it to embed every question, and
   `xbrlrag_full` needs it for narrative steps; only the `long_context_*` arms and `closed_book`
   run without it.
4. **Keys and paths:** the provider keys are present (check presence only; never print
   them), and in a worktree the filing roots resolve to directories that exist
   (`XBRLRAG_NORMALIZED_ROOT` is not in `.env`; unset, it points inside the worktree).
5. **One-case smoke** of each system in a batch before its full run; a full run starts
   only after its smoke answered without an error.

When asking the operator to stop services (for example, to free `xbrl_db` for a
`CREATE DATABASE ... TEMPLATE`), name exactly which: usually the API, MCP and UI. Postgres,
Qdrant and RagInference stay up whenever paid runs are planned. After any stop or
restart, repeat the checks above. (On 2026-10-28, RagInference was stopped with the
other services and two runs of external test v1 were aborted.)

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run all unit tests
pytest test/unit/ -v

# Run a single test
pytest test/unit/test_llm.py::TestCostTracker::test_record_usage -v

# Lint (ruff only, config in ruff.toml; no formatter — black/isort/flake8 were retired).
# In Claude Code sessions `ruff check --fix` auto-runs on every edited .py (PostToolUse hook;
# unused imports are removed only at Stop, so an import added before its first use survives)
# and the Stop hook reports standards findings — see .claude/hooks/README.md.
ruff check .
python tools/standards/check_standards.py ratchet   # burn-down only shrinks, config only tightens
mypy src/
```

**Coding standards:** Google Python Style Guide with house deviations, for new and touched code
(global `rules/python.md`); RAG rules in `.claude/rules/rag.md`. Existing ruff findings live in
`ruff-burndown.toml`, which may only shrink — never add entries, ignores or overrides to get green.

## Git

- **Never add a `Co-Authored-By: Claude ...` line (or any other Claude/AI attribution)
  to commit messages.** This overrides the global convention and any tool default.

## Naming (formerly ARag)

The project was renamed **ARag → XbrlRag**. Some old names were **kept on purpose** —
a lowercase `arag` is usually intentional, not a missed rename:

- **AWS / `aws-genai-iac`-owned names** — ECR images `arag`, `arag-ui`, `arag-ingest`;
  `/opt/arag/.env`; the `arag-resolve-image` / `arag-render-secrets` host scripts; the
  `genai-dev-arag-*` bucket and tags; RDS role `arag_admin`; container user `arag`.
- **Prometheus metrics** `arag_*`, alert names `Arag*`, and the `arag-xbrl*` groups/job.
- **MCP server name** `arag-analyst`, and the UI storage keys `arag:theme` / `arag:answers`.

Env vars are `XBRLRAG_*`; a legacy `ARAG_*` name is still honored (mirrored in
`src/__init__.py`, new name wins). Renaming any kept name is a coordinated change with
the infra repo, not a text sweep.

## Architecture

Two flows share the embedding / vector-store / LLM layers but are otherwise separate:

```
XBRL (the product)
  EDGAR iXBRL → [ingest/sec_edgar] → Postgres xbrl_db (Layer-0 + materialized views)
                                 ↘ canonical HTML → [chunking] → Qdrant
  Query → [retriever/xbrl] plan-based: route → plan → execute → synthesize → verify → Answer

PDF (legacy, maintenance-only)
  PDF → [ingest/pdfparse] → [preprocess] → [chunking] → [embeddings] → Qdrant
  Query → [retriever] embed + hybrid search + rerank + MMR → [llm] → Answer
```

### Module responsibilities

**XBRL flow**
- **`src/ingest/sec_edgar/`** — EDGAR download → Arelle fact ingest → Layer-0 registries →
  canonical HTML + DOM positions → publish gate. See its [`CLAUDE.md`](src/ingest/sec_edgar/CLAUDE.md).
- **`src/retriever/xbrl/`** — the plan-based query pipeline (below). See its
  [`CLAUDE.md`](src/retriever/xbrl/CLAUDE.md).
- **`src/pipelines/xbrl/`** — orchestration: `retrieval_pipeline.py`, `load_to_vectordb_pipeline.py`, query cache.
- **`src/ingest/html10k/`**, **`src/chunking/html10k_chunking.py`**, **`src/retriever/html10k/`**, **`src/pipelines/html10k/`** — the HTML 10-K flow. Retained but not evaluated: `html_rag` left the v2 comparison on 2026-11-06 ([decision](docs/design/XbrlRag_HtmlRag_Decision.md)); no further work. See its [`CLAUDE.md`](src/retriever/html10k/CLAUDE.md).
- **`src/observability/`** — `trace_context.py`, `audit_store.py`, `ingestion_audit.py`, `metrics_exporter.py`.
- **`src/api/`** (BFF + `/api/v1` + `/api/eval` + `GET /metrics`), **`src/mcp_server/`**, **`ui/`** —
  serving surfaces, each with its own `CLAUDE.md` / `README.md`.
- **`scripts/ddl/`** — `base/` (vendored Arelle schema), `layer_0/`, `mv/` (19 managed MVs),
  `audit/`, `eval/`, `populate/`.

**Shared layers**
- **`src/llm/`** — provider-neutral gateway (`gateway/`), `config.py` (model tiers, pricing,
  escalation policy), `cost_tracker.py`, `rate_limiter.py`, `escalation.py`, `batch.py`,
  `sec_table_processor.py` (PDF-flow vision tables). See [README](src/llm/README.md).
- **`src/embeddings/`** — BGE-M3 dense+sparse local, `remote_embedder.py` (via `RAGINFER_URL`), OpenAI.
- **`src/vectorstores/`** — Qdrant (primary; `xbrl_qdrant_vectordb_client.py` for the XBRL payload schema).
- **`src/chunking/`** — `xbrl_html_base_chunking.py` (canonical HTML) + the legacy title/table-aware chunkers.
- **`prompts/`** — `prompt_registry.py` resolves `(stage, provider, series, version)` under
  `templates/xbrl/`; `prompts.py` + `templates/sec10k/` is the legacy enum-based registry.

**PDF legacy (maintenance-only)** — `src/ingest/pdfparse/` (element extraction),
`src/preprocess/` (layout/OCR/tables), `src/retriever/` (`base_retriever.py`,
`hyde_retriever.py`, `llm_query_rewriter.py`, `bge_reranker.py`, `jina_reranker.py`).

### Key constants (`src/__init__.py`)
- `PROJECT_ROOT` is **derived from the package location** (override: `XBRLRAG_PROJECT_ROOT`) —
  never hardcode it; a Windows literal is a *relative* path on POSIX and silently
  resolved to `/app/D:/...` inside the images.
- `CHUNK_SIZE_IN_CHARS = 1000`, `CHUNK_SIZE_IN_TOKENS = 256`
- Qdrant collections: XBRL `xbrl_sec10k_text` / `xbrl_sec10k_metadata`; legacy PDF
  `sec10k_text` / `sec10k_metadata` (separate — the payload schemas differ)

**`src/eval/`** — evaluation. Two generations, both live:
- `core/` + `systems/` + `suites/` — the eval framework (`EvalCase`, `SystemUnderTest`,
  `EvalRunner`, deterministic graders). Metrics include **grounding precision** (the
  *right* fact, not merely *a* fact) and **silent-wrong rate** (confident + cited +
  wrong — a release gate). Design, where results live and the rules learned from the
  accuracy work:
  [`docs/design/XbrlRag_Evaluation.md`](docs/design/XbrlRag_Evaluation.md).
  The primary benchmark is the **internal SQL-derived suite** (**513 cases**, committed at
  `test/eval/suites/sql_derived_v1.json`) generated from `scripts/xbrl/analyst_queries.sql`
  via `configs/eval/sql_derived_templates.yaml`; external benchmarks (FinanceBench,
  FinLoRA) were surveyed and **deliberately not adopted**. Operator guide:
  [`docs/guides/xbrl-flow/11-evaluation.md`](docs/guides/xbrl-flow/11-evaluation.md).
  **Measured results live in the `eval` schema and render at `/tech/eval`** — that is the
  source of truth for accuracy numbers, not any figure pasted into a doc. Gotchas an
  editor must not break:
  - **`screen` ≠ `breakdown`.** A cross-filer screen produces one `screen` step with
    ranked rows; a single-filer split (revenue by segment/product/geography) is one
    `breakdown` step whose rows are the members that partition the filer's total.
    "The breakdown" is defined once (`choose_partition`: the finest member set summing
    to the total) and shared by the step and the suite's ground truth
    (docs/specs/breakdown-op.md). Grading a breakdown as a screen scores correct answers
    as refusals.
  - **Narrative cases run serialized** (`DEFAULT_KIND_CONCURRENCY`) — the reranker is
    CPU-bound and has segfaulted under unbounded batching.
  - **Everything persisted is NUL-scrubbed** (`src/db/scrub.py` `scrub_nul`, applied to
    every whole parameter set in the eval and audit stores): Postgres rejects U+0000 in
    text and jsonb, and persistence is atomic, so one stray byte discards a whole run.
  - **A narrowed run is not a baseline.** `--kind` / `--rerun-failed` / `--sample` runs are
    stored with `tier='partial'` over a deliberately-biased subset; only a full-suite run
    yields a comparable headline number.
  - **Validate on slices, not full runs** (`build_slice_suite.py` → `run_suite.py
    --suite-path` → `compare_runs.py`, about $0.30–0.50); one full run at the end. A single
    full run cannot resolve a change under ~2 points (sd ≈ 0.9).
  - **Rebuild and diff the suite after any template, view, concept-mapping or corpus
    change**; the corpus fingerprint does not reliably change on a load.
  - Runs: `scripts/eval/build_sql_derived_suite.py` then `scripts/eval/run_suite.py`
    (hard `--max-cost-usd`; `--persist-audit` is what makes the UI's trace links resolve).
- **v2 evaluation** (the headline comparison; [plan](docs/design/XbrlRag_v2_Plan.md),
  binding rules in [`docs/eval/heldout_v2/preregistration.md`](docs/eval/heldout_v2/preregistration.md)
  and its amendments): `xbrlrag_full` against **`frontier_sec_tools`** (a frontier model
  with tools over SEC's public APIs), with `long_context_oracle` as the single-filer ceiling
  and `closed_book` as the floor (`naive_rag_filtered` runs on dev only); `html_rag`, `long_context_resolved` and
  `long_context_multi` stay runnable in code but run on no set. Keys and
  grading follow [`docs/design/XbrlRag_Eval_Semantics.md`](docs/design/XbrlRag_Eval_Semantics.md)
  (S1–S6). Intents and phrasings `suites/intents.py` + `configs/eval/v2/`; SEC answer keys
  `keys/`; run/case columns `core/run_columns.py` (a run never mixes splits); text judge
  `core/graders/judge.py` + `configs/eval/judge.yaml` (never the generator's model
  family); key-free checks `keyfree/`. Held-out sets (H-F, H-X, set B) are never read or
  tuned on by an agent; only their aggregates are shared.
- `xbrl/` — the 13-case golden set + drift + prompt×model matrix (still the CI entry
  points via `scripts/eval/`). `xbrl/numeric.py` re-exports `core/graders/numeric.py`.
- `deepeval/` — PDF-legacy RAG evaluation (maintenance-only)

### XBRL hybrid query pipeline (`src/retriever/xbrl/`)

The XBRL flow is **separate** from the legacy PDF/sec10k retriever above.
Orchestrated by `src/pipelines/xbrl/retrieval_pipeline.py`
(`XbrlRetrievalPipeline.generate`):

```
query → QueryDecomposer.decompose()  # split a COMPOUND query into ordered sub-queries (else 1 passthrough)
  each sub-query ↓ (run in dependency order; a dependent one gets the prior screen's top-N injected)
      → IntentRouter.route()      # LLM classify + deterministic entity resolve + fast-path plan
      → BranchDispatcher.decide()  # FACT_BRANCH_DIRECT | NARRATIVE_BRANCH | PLAN_GENERATOR
      → (fast-path plan | PlanGenerator.generate_plan)
      → PlanExecutor.execute()     # per-step: lookup (SQL) | compute / cagr (calc) | screen (cross-filer SQL) | aggregate (total/median over a screen) | breakdown (one filer's partition) | narrative (RAG)
      → XbrlRagSynthesizer         # LLM answer with per-statement citations
      → citation integrity check   # deterministic: every cited id resolves to a step's evidence
      → XbrlFaithfulnessVerifier   # LLM: drop statements unsupported by their own cited evidence
  → merge sub-query answers (XbrlRetrievalPipeline._merge_responses)
```

Stage → module (one line each; the **per-stage detail is in
[`src/retriever/xbrl/CLAUDE.md`](src/retriever/xbrl/CLAUDE.md)** for invariants and in
[guide 6](docs/guides/xbrl-flow/06-retrieval-pipeline.md) for the operator view — do not
re-expand it here):

| Module | Does |
|---|---|
| `query_decomposer.py` | Split a COMPOUND query into ordered sub-queries; a dependent one references an earlier result via a `<<TOP:id:n>>` token. Any error → single passthrough. |
| `intent_router.py` + `entity_extractor.py` | Classify intent (`NUMERIC_LOOKUP`/`_COMPUTE`/`_SCREEN`/`NARRATIVE`/`HYBRID`), lift mentions, emit a 1-step fast-path plan for the trivial lookup/narrative. Entities resolve by **SQL**, never by the LLM. |
| `branch_dispatcher.py` | `FACT_BRANCH_DIRECT` / `NARRATIVE_BRANCH` / `PLAN_GENERATOR`. |
| `plan_generator.py` + `plan_validator.py` | Typed `{"steps":[…]}` plan; ops `lookup` / `compute` / `cagr` / `screen` / `aggregate` / `breakdown` / `narrative` (`VALID_OPS`). Escalates one model tier on `plan_validation_failed`. |
| `plan_executor.py` | Walk steps → `xbrl_factstore_lookup.py` (SQL), `xbrl_calculator_tool.py` (sandboxed arithmetic), `xbrl_factstore_screen.py` (cross-filer ranked rows), `screen_aggregate.py` (group total/median), `xbrl_factstore_breakdown.py` (single-filer partition), `xbrl_narrative_retriever.py` (Qdrant + rerank). Narrow OK-only fallbacks (sub-family → `Revenue`, glued-hint split, sibling intersection, derived gross profit), each logged as a `decision` event. |
| `llm_xbrl_synthesizer.py` / `llm_xbrl_faithfulness_verifier.py` | Cited synthesis, then an independent pass that drops statements their own citations don't support. |

Models come from the active LLM profile in `configs/llm/llm.yaml` (`openai-6.1` is the
default, `DEFAULT_PROFILE` in `src/llm/profiles.py`; also `openai`, `openai-5.6`,
`anthropic`, and `deepseek` for development runs only; pick with `XBRLRAG_LLM_PROFILE` or
`run_suite.py --llm-profile`),
priced by `configs/llm/pricing.yaml` — the single source of truth; each stage keeps its tier
(`XBRL_STAGE_TIERS`) and reads the profile when built (`src/llm/stage_models.py`);
**never hardcode a model name in a doc or a stage**. All query-time LLM
calls go through the `LLMGateway` (`src/llm/gateway/`), built per stage by
`src/llm/xbrl_gateway.py::build_xbrl_gateway`: retry, cost, tracing, prompt-registry
resolution, escalation, the shared rate limiter and the per-run budget. Design:
[`docs/design/XbrlRag_LLM_Harness.md`](docs/design/XbrlRag_LLM_Harness.md).

Data/SQL reference: `scripts/xbrl/analyst_queries.sql` is the authoritative map
from analyst question → canonical SQL per view (`mv_financial_facts`,
`mv_geographic_facts`, `mv_segment_matrix`, `mv_yoy_comparison`,
`mv_derived_metrics`, …). Backend: local Postgres `xbrl_db` (port 5432, `XBRL_DB_*`
env vars). Observability: every query runs in a trace
(`src/observability/trace_context.py`), persisted to the `audit` schema
(`query_audit` rollup + `query_event` stream) and viewable in the `ui/` React
Trace Explorer (`/tech/traces/<id>`). The trace captures the **final answer**
(`audit.query_audit.answer`, rendered as the Ask-page answer view), each LLM
call's **system/user prompt + response + cost** (`llm_call` events, gated by
`XBRLRAG_TRACE_LLM_BODIES`), and the **exact fact-store SQL** (`sql_query` events
from `xbrl_factstore_lookup`/`_screen`). The UI mirrors the MacroRAG sister
project's Traces tab for consistency. The same `audit.v_*` views are exported for
Prometheus at **`GET /metrics`** (`src/observability/metrics_exporter.py` →
`src/api/routes/metrics_prom.py`); Grafana dashboards + alert rules live in
[`configs/monitoring/`](configs/monitoring/README.md). The parallel **`eval` schema**
(`scripts/ddl/eval/`) holds graded eval runs and renders at `/tech/eval`; with
`--persist-audit` each eval case also lands in `audit.*`, so a graded case links
straight to its own trace.

### Configuration
- `configs/logging.yaml` — console + JSON file logging; every process writes its own
  `logs/xbrlrag_<process>_<pid>.log` (7-day pruning), stamped with `query_id` / `stage`
- `configs/entities/company_aliases.yaml` — committed short-name aliases ("TSMC") for
  entity resolution, validated by `src/retriever/xbrl/entity_aliases.py`
- `configs/pipelines/sec_edgar_downloads.yaml`, `configs/pipelines/xbrl/load_to_vectordb.yaml` —
  XBRL flow scope (tickers × fiscal years × form types). Their **path keys are deliberately
  empty**: roots come from `SEC_EDGAR_ROOT` / `XBRLRAG_NORMALIZED_ROOT`, and env wins. Do not
  put an absolute Windows path back in — see the `PROJECT_ROOT` note above.
- `configs/pipelines/sec10k_data_to_process.yaml` — PDF-legacy ingestion scope
- `configs/eval/sql_derived_templates.yaml` — the eval suite's template spec
- `.env` — API keys (`OPENAI_API_KEY`, `JINA_API_KEY`), `HF_HOME`, `LOG_LEVEL`; see
  [`.env.example`](.env.example) for the full set — DB (`XBRL_DB_*` incl. `XBRL_DB_SSLMODE`),
  Qdrant (`QDRANT_HOST/PORT/API_KEY/HTTPS`), `RAGINFER_URL`, dual-mode
  (`XBRLRAG_DEPLOY_MODE`, `XBRLRAG_STORAGE_BACKEND`, `XBRLRAG_S3_BUCKET/PREFIX`, `SEC_EDGAR_ROOT`,
  `XBRLRAG_NORMALIZED_ROOT`), and other `XBRLRAG_*` controls
