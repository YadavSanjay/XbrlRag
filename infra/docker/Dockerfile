# syntax=docker/dockerfile:1
#
# XbrlRag serving image (WS-6 / B7) — runs BOTH the BFF (src.api.main:app) and the
# MCP server (src.mcp_server.server) from one slim image; the compose/Helm command
# selects which. Slim by design: the serving path reaches BGE-M3 / Jina over HTTP
# via RAGINFER_URL, so no torch/transformers/faiss are installed (see
# requirements-serve.txt). Build from the repo root (context = repo root):
#   docker build -f infra/docker/Dockerfile -t arag:local .

FROM python:3.12-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
WORKDIR /app

# Installed packages from the deps stage (keeps the final image free of pip caches).
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Application code + resources needed to serve (data/ and models are external).
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY configs/ ./configs/

# Non-root runtime user. Create the logs/ dir the rotating file handler writes to
# (configs/logging.yaml → logs/xbrlrag_<process>_<pid>.log); it is excluded from the build context.
RUN useradd --create-home --uid 10001 arag \
    && mkdir -p /app/logs \
    && chown -R arag:arag /app
USER arag

# Sensible in-container defaults; override via compose/Helm env.
ENV XBRL_DB_HOST=postgres \
    XBRL_DB_PORT=5432 \
    XBRL_DB_NAME=xbrl_db \
    QDRANT_HOST=qdrant \
    QDRANT_PORT=6333 \
    XBRLRAG_MCP_TRANSPORT=streamable-http \
    XBRLRAG_MCP_PORT=8765

EXPOSE 8000 8765

# BFF health endpoint (degrades gracefully; 200 even without DB/models).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status==200 else 1)" || exit 1

# Default = BFF. MCP service overrides the command in compose/Helm.
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
