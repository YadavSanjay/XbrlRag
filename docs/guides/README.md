# XbrlRag guides

XbrlRag answers financial-analyst questions over SEC filings (10-K / 10-Q / 20-F) with
**cited, audited numbers**: every figure comes from a tagged XBRL fact reached by deterministic
SQL, and the language model only writes the narrative around it. These guides explain how to
install, run and operate it. For *why* it is built the way it is, see the
[design docs](../design/README.md).

## Which guide do I need?

| Flow | Folder | Status | What it is |
|------|--------|--------|------------|
| **XBRL** | [`xbrl-flow/`](./xbrl-flow/README.md) | **The product**: actively developed | SEC EDGAR iXBRL → Arelle fact ingest into PostgreSQL → materialized views + canonical-HTML chunks in Qdrant → plan-based retrieval (SQL lookups, sandboxed arithmetic, narrative search) → cited synthesis → faithfulness check. Powers the REST API, MCP server and React UI. |
| **HTML 10-K** | [`html-flow/`](./html-flow/README.md) | Retained, not evaluated | RAG over the primary HTML document with the XBRL tags stripped. It was an evaluation baseline until 2026-11-06, when it left the comparison ([decision](../design/XbrlRag_HtmlRag_Decision.md)); the code stays, with no further work. |
| **PDF** | [`pdf-legacy-flow/`](./pdf-legacy-flow/README.md) | Legacy, maintenance-only | The original pipeline over PDF 10-Ks: layout and table detection, summaries, title-aware chunking, hybrid search and rerank. Prose-centric, no structured numbers. No new features ([`VISION.md`](../../VISION.md)). |

If you are new, start with the [XBRL flow guide](./xbrl-flow/README.md): it has an overview
diagram, the reading order and a quick path.

## XBRL flow guides

| # | Guide | Use it to |
|---|-------|-----------|
| 1 | [Setup & dependencies](./xbrl-flow/01-setup-and-dependencies.md) | Install the environment, services and settings |
| 2 | [PostgreSQL setup](./xbrl-flow/02-postgres-setup.md) | Create `xbrl_db` and apply the schema |
| 3 | [Data download](./xbrl-flow/03-data-download.md) | Fetch filings from EDGAR, full or incremental |
| 4 | [Ingest & populate](./xbrl-flow/04-ingest-and-populate.md) | Load facts with Arelle, build registries and views, run on AWS |
| 5 | [Vector DB load](./xbrl-flow/05-vectordb-load.md) | Chunk canonical HTML, embed and load Qdrant |
| 6 | [Retrieval pipeline](./xbrl-flow/06-retrieval-pipeline.md) | Run a query and read what each stage did |
| 7 | [API, UI & observability](./xbrl-flow/07-api-ui-observability.md) | Serve the UI, read traces and metrics |
| 8 | [Troubleshooting](./xbrl-flow/08-troubleshooting.md) | Match a symptom to its cause and fix |
| 9 | [Programmatic access](./xbrl-flow/09-programmatic-access-rest-and-mcp.md) | Call the v1 REST API or the MCP server |
| 10 | [Packaging](./xbrl-flow/10-packaging-images.md) | Build and push the images |
| 11 | [Evaluation](./xbrl-flow/11-evaluation.md) | Build suites, run and compare evaluations |
| 12 | [Held-out sets](./xbrl-flow/12-heldout-set-b.md) | Run the maintainer-only held-out procedure |

## Platform dependencies

| Dependency | Used by | Default | Notes |
|------------|---------|---------|-------|
| **PostgreSQL** (`xbrl_db`) | XBRL flow: facts, registries, audit, eval | `localhost:5432` | [Guide 2](./xbrl-flow/02-postgres-setup.md) |
| **Qdrant** | All flows: hybrid dense + sparse search | `localhost:6333` | Run it on a named Docker volume, not a Windows bind mount ([why](./xbrl-flow/08-troubleshooting.md#qdrant-wont-start-corrupted-collection)) |
| **Arelle** | XBRL ingest only | `ARELLE_CMD` | Windows: the frozen `arelleCmdLine.exe`; Linux/containers: pip `arelle-release` |
| **Embedder + reranker** (BGE-M3, Jina reranker) | Narrative search in all flows | Local model cache, or the RagInference service at `RAGINFER_URL` | The serving images are torch-free and always use `RAGINFER_URL` |
| **LLM provider** | Planning, synthesis, verification | The active profile in [`configs/llm/llm.yaml`](../../configs/llm/llm.yaml) | Set the matching key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `DEEPSEEK_API_KEY`) in `.env` |

Exact versions and environment variables are in
[guide 1](./xbrl-flow/01-setup-and-dependencies.md). To bring the whole serving stack up in
containers, see [`deploy/README.md`](../../deploy/README.md).

## Repository map (the parts these guides cover)

```
src/
  ingest/sec_edgar/      EDGAR download, Arelle ingest, Layer-0 registries, canonical HTML (XBRL)
  ingest/html10k/        HTML 10-K parsing (retained)
  ingest/pdfparse/       PDF element extraction (legacy)
  chunking/              canonical-HTML chunker (XBRL), HTML 10-K and legacy chunkers
  embeddings/            BGE-M3 local or remote (RagInference)
  vectorstores/          Qdrant clients (separate XBRL and legacy collections)
  retriever/xbrl/        the plan-based query pipeline
  pipelines/xbrl/        load and retrieval orchestration, query cache
  llm/                   LLM gateway, profiles, cost tracking, escalation, run budget
  observability/         traces, audit store, Prometheus exporter
  api/                   FastAPI backend for the UI, /api/v1 for agents, /api/eval
  mcp_server/            MCP server exposing the v1 API as tools
  eval/                  evaluation framework, suites, systems, graders
scripts/ddl/             PostgreSQL DDL: base, layer_0, mv, audit, eval
scripts/eval/            suite builders, run_suite, compare_runs
configs/                 pipeline, LLM, eval and monitoring configuration
ui/                      React + Vite UI (business and technical consoles)
prompts/                 versioned prompt templates
```

## Conventions used in these guides

- Commands run from the **repo root** unless stated. Local commands are written for
  PowerShell with the project's conda environment (`conda run -n py312 python …`); commands
  that run on the Linux ingest runner or inside a container are POSIX `sh`.
- `PROJECT_ROOT` is derived from the package location ([`src/__init__.py`](../../src/__init__.py));
  override it with `XBRLRAG_PROJECT_ROOT`. Data roots come from `SEC_EDGAR_ROOT` and
  `XBRLRAG_NORMALIZED_ROOT`, which win over the pipeline YAMLs, whose path keys are
  deliberately blank.
- Absolute Windows paths (`D:/…`) are examples from a local machine, never values to put in
  config: on Linux such a literal is a *relative* path and lands under `/app/D:/…` in a
  container.
- ⚠️ marks a **sharp edge**: an ordering requirement or a step that fails silently.

## Related

- [Design docs](../design/README.md): how each subsystem works and why.
- [Harness](../harness/README.md): the dev-time guards and the feature manifest.
- [`configs/monitoring/README.md`](../../configs/monitoring/README.md): Prometheus metrics,
  Grafana dashboards and alert rules.
- Module READMEs: [API](../../src/api/README.md), [MCP server](../../src/mcp_server/README.md),
  [UI](../../ui/README.md), [LLM layer](../../src/llm/README.md).
