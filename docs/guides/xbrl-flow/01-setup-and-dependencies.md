# XBRL Flow — 1. Setup & Dependencies

What a machine needs before it can run the XBRL flow: the Python environment, the external
services (Postgres, Arelle, Qdrant, optionally RagInference), and the environment variables
every stage reads. Read it once when setting up a new box; the later guides assume it is done.

## 1.1 Python environment

Use the `py312` conda environment for everything in this repo; the bare `python` on PATH
lacks the dependencies. From the repo root (PowerShell):

```powershell
conda run -n py312 pip install -r requirements.txt
```

`requirements.txt` is the full development set (edgartools, psycopg2, the LangChain provider
packages, FlagEmbedding/sentence-transformers for BGE-M3, FastAPI/uvicorn, qdrant-client,
boto3). The images use narrower sets: `requirements-serve.txt` (torch-free serving) and
`requirements-ingest.txt` (adds edgartools, `arelle-release` and the `pg8000` driver Arelle's
xbrlDB plugin needs); see [guide 10](./10-packaging-images.md).

## 1.2 External services and tools

| Tool | Purpose | Install / default |
|------|---------|-------------------|
| **PostgreSQL 14+** | `xbrl_db`: facts, Layer-0 registries, materialized views, `audit` and `eval` schemas | local server on `:5432`; [guide 2](./02-postgres-setup.md) |
| **Arelle** | XBRL fact ingest, plus label / dimension / geography export | `ARELLE_CMD`, default per platform ([`src/ingest/util/arelle_cmd.py`](../../../src/ingest/util/arelle_cmd.py)): on Windows `arelleCmdLine.exe` on `PATH`, so set `ARELLE_CMD` to the frozen build's absolute path when its folder is not on `PATH`; on Linux the console script `arelleCmdLine` from `arelle-release==2.41.0`, baked into [`Dockerfile.ingest`](../../../infra/docker/Dockerfile.ingest). Every consumer resolves it through `resolve_arelle_cmd()`, so a bare name and an absolute path both work. |
| **Qdrant** | vector search for narrative chunks | `docker run -d --name qdrant -p 6333:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant:v1.18.2`. Use a **named volume**, never a Windows bind mount ([why](./08-troubleshooting.md#qdrant-wont-start-corrupted-collection)). |
| **RagInference** (optional) | shared HTTP embedder (BGE-M3) and reranker | set `RAGINFER_URL`; see below |
| **Node 24** | the React UI | [guide 7](./07-api-ui-observability.md) |

**Arelle** is an open-source XBRL processor. XbrlRag runs `arelleCmdLine` with its bundled
`xbrlDB` plugin to load facts into Postgres, plus two custom plugins
(`src/ingest/sec_edgar/ingest/arelle_plugins/`) that patch a `ModelInlineFact` bug and export
concept labels and dimension members as JSON. Its SEC transforms (`EDGAR/transform`) are
required; [`src/ingest/sec_edgar/CLAUDE.md`](../../../src/ingest/sec_edgar/CLAUDE.md) explains why.

**RagInference** (`RAGINFER_URL`). Without it, XbrlRag loads BGE-M3 (about 2.5 GB) and the Jina
reranker (about 0.9 GB) in-process. With it, `src/embeddings/remote_embedder.py` and
`src/retriever/remote_reranker.py` call the shared service, so the models load once for
several projects. Related: `RAGINFER_TIMEOUT_S` (default 300), `RAGINFER_EMBED_MODEL`,
`RAGINFER_RERANK_MODEL`, and `RAGINFER_FALLBACK_LOCAL` (default off: a network failure raises
instead of silently loading local models).

## 1.3 `.env`

Variables load from `.env` through `python-dotenv`. The committed
[`.env.example`](../../../.env.example) is the source of truth for the full set; never commit
real secrets. The essentials for this flow:

```ini
# LLM providers (the active profile in configs/llm/llm.yaml decides which are needed)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...          # optional
DEEPSEEK_API_KEY=sk-...               # optional: development profile only
XBRLRAG_LLM_PROFILE=                  # empty = openai

# PostgreSQL (xbrl_db)
XBRL_DB_HOST=localhost
XBRL_DB_PORT=5432
XBRL_DB_NAME=xbrl_db
XBRL_DB_USER=postgres
XBRL_DB_PASSWORD=postgres

# Qdrant
QDRANT_HOST=localhost
QDRANT_PORT=6333

# EDGAR: SEC requires a contactable "Name email@domain" User-Agent
SEC_EDGAR_IDENTITY=Your Name you@example.com

# Storage roots and Arelle (local example values: use your own paths)
SEC_EDGAR_ROOT=<repo>/data/storage/sec_edgar
XBRLRAG_NORMALIZED_ROOT=<repo>/data/storage/xbrl/normalized
ARELLE_CMD=<Arelle install>/arelleCmdLine.exe
HF_HOME=<hf-cache>    # model cache for in-process BGE / Jina

RAGINFER_URL=                         # optional shared inference service
LOG_LEVEL=INFO
```

> ⚠️ **Set the roots in the environment, not in the pipeline YAMLs.** `SEC_EDGAR_ROOT` and
> `XBRLRAG_NORMALIZED_ROOT` win over the path keys in `configs/pipelines/**`, which ship blank
> on purpose: an absolute Windows path in a config file is a *relative* path on POSIX and
> resolves to `/app/D:/...` in a container. Every stage resolves its root through
> `storage.local_root(role)` and fails on zero discovered filings rather than reporting `ok`.

**Local vs AWS.** `XBRLRAG_DEPLOY_MODE` (`local` default, or `aws`) selects backends and
policies, not endpoints: filings on local disk or S3 (`XBRLRAG_STORAGE_BACKEND`,
`XBRLRAG_S3_BUCKET`, `XBRLRAG_S3_PREFIX`), Postgres `sslmode` (`XBRL_DB_SSLMODE`, else `prefer`
locally and `require` under `aws`), and `QDRANT_API_KEY` / `QDRANT_HTTPS` / `QDRANT_VERIFY` for
a secured Qdrant. Design: [local / AWS deployment](../../design/XbrlRag_AWS_Deployment.md).

Defaults that rarely need setting:

| Variable | Default | Effect |
|---|---|---|
| `XBRLRAG_EDGAR_READ_TIMEOUT_S` | `120` | EDGAR read timeout (seconds) |
| `ARELLE_TIMEOUT` | `600` | per-filing Arelle timeout (seconds) |
| `ARELLE_MAX_ATTEMPTS` / `ARELLE_RETRY_BACKOFF` | `2` / `10` | retries of a timed-out Arelle run |
| `ARELLE_HEARTBEAT_SECS` | `60` | "still running" log line interval; `0` silences it |
| `ARELLE_INTERNET_CONNECTIVITY` | unset | `offline` makes Arelle use only its web cache: a cache gap fails at once instead of stalling. Required where egress is restricted ([guide 4 §4.13.2a](./04-ingest-and-populate.md#4132a-seed-the-arelle-web-cache-before-the-first-ingest)) |
| `XBRL_DB_TIMEOUT` | `2000` | timeout passed to Arelle's xbrlDB connection |
| `XBRLRAG_LOG_FILE` | the API process's own log | structured log the API tails. Every process writes `logs/xbrlrag_<process>_<pid>.log`; files older than 7 days are pruned at startup |
| `XBRLRAG_UI_ORIGINS` | `localhost:5173` | API CORS origins |
| `VITE_BFF_TARGET` | `http://localhost:8000` | UI dev-server proxy target |

## 1.4 Stores and connection defaults

The flow needs two stores, both reachable:

1. **PostgreSQL `xbrl_db`.** The Arelle "public schema" (`fact`, `context`, `accession`,
   `element`, `qname`, ...) plus this repo's Layer-0 tables, materialized views, and the
   `audit` and `eval` schemas ([guide 2](./02-postgres-setup.md)). Lookups, computes, screens
   and breakdowns read only Postgres.
2. **Qdrant.** Collections `xbrl_sec10k_text` and `xbrl_sec10k_metadata`
   ([guide 5](./05-vectordb-load.md)). Only the narrative step reads Qdrant.

Every component reads the same Postgres variables with the same defaults, almost all through
`src/db/conn.py` `conn_params()`:

| Variable | Default |
|-----|---------|
| `XBRL_DB_HOST` / `XBRL_DB_PORT` | `localhost` / `5432` |
| `XBRL_DB_NAME` | `xbrl_db` |
| `XBRL_DB_USER` / `XBRL_DB_PASSWORD` | `postgres` / `postgres` |
| `SEC_EDGAR_ROOT` | a Windows dev-box literal in `storage.default_local_root("sec_edgar")`; **set it explicitly on any other machine** |
| `XBRLRAG_NORMALIZED_ROOT` | the same, for `"normalized"` |

> ⚠️ `scripts/rebuild_mvs.py` and `scripts/eval/build_golden_set.py` connect directly and
> ignore `XBRL_DB_SSLMODE`; set `PGSSLMODE=require` for them against RDS.

Qdrant comes from `QDRANT_HOST` / `QDRANT_PORT` (default `localhost:6333`); the vector-load
YAML's `qdrant:` block holds the same default, and env wins.

## 1.5 Checklist

- [ ] `requirements.txt` installed in `py312`
- [ ] PostgreSQL running; schema applied with `bootstrap_db.py` ([guide 2](./02-postgres-setup.md))
- [ ] Arelle installed; `ARELLE_CMD` resolves
- [ ] Qdrant running on `:6333` (named volume)
- [ ] `.env` filled: provider key(s), `XBRL_DB_*`, `SEC_EDGAR_IDENTITY`, both storage roots
- [ ] (UI only) Node 24 available

Next: [2. PostgreSQL setup](./02-postgres-setup.md).
