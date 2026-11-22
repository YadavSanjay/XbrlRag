# Deployment — Docker / Compose / Helm / CI

Packages XbrlRag's **serving surfaces** — the BFF (`api`), the MCP server (`mcp`), and
the React `ui` — from one slim image, plus a local compose stack and a Helm chart.

> **Scope.** This is the *packaging*. Auth / SSO / multi-tenancy and an ingest DAG / EDGAR
> feed polling are not built (by decision); ingest stays operator-triggered from the CLI.
> Bringing services up is separate from **loading data**: the fact store still needs
> its schema + an ingest run (see [`docs/guides/xbrl-flow/`](../docs/guides/xbrl-flow/README.md)).

## The image is slim on purpose

The serving path reaches BGE-M3 / Jina over HTTP via `RAGINFER_URL`
([`remote_embedder.py`](../src/embeddings/remote_embedder.py) /
[`remote_reranker.py`](../src/retriever/remote_reranker.py)), so the app image
installs **no torch / transformers / faiss** — see
[`requirements-serve.txt`](../requirements-serve.txt). Result: ~0.5 GB instead of
multi-GB. `RAGINFER_URL` **must** be set in any real deploy (the image cannot load
models in-process). Use the full [`requirements.txt`](../requirements.txt) for local
dev / ingest / in-process models.

`fastapi==0.162.0` / `starlette>=1.3.1,<2` / `pydantic<2.12` / `mcp>=1.30,<2` are pinned
together — a clean install otherwise pulls a pydantic that breaks FastMCP's tool
inspector (see the notes in `requirements.txt`).

## Local vs AWS (dual-mode)

The same code runs locally or on AWS via `XBRLRAG_DEPLOY_MODE` (`local` default |
`aws`), which selects **backends/policies**, not endpoints — see
[`docs/design/XbrlRag_AWS_Deployment.md`](../docs/design/XbrlRag_AWS_Deployment.md).
With it unset, everything runs in local mode. Relevant env (all in
[`.env.example`](../.env.example)):

- **Qdrant** — `QDRANT_HOST`/`QDRANT_PORT` (+ `QDRANT_API_KEY`/`QDRANT_HTTPS`/`QDRANT_VERIFY`)
  are read by the client, so an in-VPC Qdrant works without code edits.
- **Postgres** — `XBRL_DB_SSLMODE` (else derived: `prefer` local / `require` aws) via the
  shared `src/db/conn.py`. `PGSSLMODE=require` is a process-level safety net.
- **Filings (ingest only)** — `XBRLRAG_STORAGE_BACKEND` (`local`|`s3`), `XBRLRAG_S3_BUCKET`,
  `XBRLRAG_S3_PREFIX`; `deploy_mode=aws` ⇒ S3. Query-time serving is filesystem-free.
- **Ingest image** — `infra/docker/Dockerfile.ingest` is the **torch-free** on-demand ingest
  runner for AWS (`requirements-ingest.txt` = serving subset + edgartools + `arelle-release` +
  lxml + boto3; embeds via `RAGINFER_URL`). The slim serving image can't ingest (no
  edgartools/Arelle).

## Docker Compose (local stack)

The Compose file lives in [`infra/docker/`](../infra/docker/); run it from the repo
root. `-f` selects the file and `--env-file .env` points Compose at the repo-root
`.env` (it would otherwise look next to the Compose file, in `infra/docker/`):

```bash
cp .env.example .env          # set OPENAI_API_KEY, RAGINFER_URL, …
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d          # postgres, qdrant, api(:8000), mcp(:8765), ui(:8080)
curl localhost:8000/api/health

docker compose --env-file .env -f infra/docker/docker-compose.yml --profile monitoring up -d     # + prometheus(:9090) + grafana(:3000), wired to /metrics
docker compose --env-file .env -f infra/docker/docker-compose.yml --profile raginference up -d   # + the sibling-repo inference image (set RAGINFER_IMAGE)
```

Postgres first-boot applies [`scripts/ddl/`](../scripts/ddl/) from
`docker-entrypoint-initdb.d`; the materialized views + data still come from an ingest
run. Volumes `pgdata` / `qdrantdata` persist state.

## Helm (Kubernetes)

Postgres, Qdrant, and RagInference are treated as **external managed services** —
point the chart at them in values.

```bash
helm lint deploy/helm/xbrlrag
helm template xbrlrag deploy/helm/xbrlrag         # render/inspect

helm install xbrlrag deploy/helm/xbrlrag \
  --set image.tag=<your-tag> --set ui.image.tag=<your-tag> \
  --set externalServices.postgres.host=<pg-host> \
  --set externalServices.qdrant.host=<qdrant-host> \
  --set externalServices.raginferenceUrl=http://raginference:8799 \
  --set-string secrets.openaiApiKey=<key> \
  --set-string secrets.postgresPassword=<pw>
```

Toggles: `mcp.enabled`, `ui.enabled`, `ingress.enabled` (routes `/`→ui, `/api`+`/metrics`→api).
Provide secrets via `--set-string` / an `existingSecret` — never commit real values.

## CI

[`.github/workflows/docker.yml`](../.github/workflows/docker.yml) on every push/PR
touching the deploy surface: builds the app image (+ a smoke test asserting API+MCP
import with **no** heavy ML libs), builds the UI image, validates `docker compose
config`, and `helm lint` + `helm template`s the chart. CI builds and lints only; images
are pushed to ECR by [`scripts/build_push_images.py`](../scripts/build_push_images.py)
(build → import-smoke gate → push; [guide 10 §10.3](../docs/guides/xbrl-flow/10-packaging-images.md)),
and the `aws-genai-iac` repo deploys them with
[`infra/docker/docker-compose.aws.yml`](../infra/docker/docker-compose.aws.yml). The
existing [`ci.yml`](../.github/workflows/ci.yml) still runs the unit suite.

## Verified locally

- `docker build -f infra/docker/Dockerfile .` → **arag:local ~509 MB**;
  `import src.api.main` / `src.mcp_server.server` load **no** torch/transformers/faiss.
- Container serves `GET /api/health` → 200 (graceful: `audit_db:false` with no DB) and
  the `GET /metrics` exporter.
- `docker compose config` valid (5 services + `monitoring`/`raginference` profiles).
- `helm lint` clean; `helm template` renders default + toggled paths.
