"""BFF configuration — DB connection params and CORS origins.

Reads the same ``XBRL_DB_*`` env vars as the rest of the system (see
``src/observability/audit_store.py`` and ``EntityExtractor.from_env``).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import List

from src.logging_config import current_log_file

# Load .env once at import so the BFF's own DB reads / log path / CORS pick up
# the same config the rest of the system uses (EntityExtractor / audit_store
# also call load_dotenv). Best-effort: absent python-dotenv is non-fatal.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional
    pass


def db_conn_params() -> dict:
    """Postgres connection params for the audit/fact-store database.

    Delegates to the shared :func:`src.db.conn.conn_params` so TLS policy
    (``sslmode``, ``require`` under deploy_mode=aws) is applied consistently.
    """
    from src.db.conn import conn_params

    return conn_params()


def cors_origins() -> list[str]:
    """Allowed CORS origins for the UI dev server.

    Override with ``XBRLRAG_UI_ORIGINS`` (comma-separated). In dev, Vite proxies
    ``/api`` so CORS is not strictly needed, but we allow localhost by default.
    """
    raw = os.environ.get("XBRLRAG_UI_ORIGINS")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


@lru_cache(maxsize=1)
def log_file_path() -> str:
    """Path to the structured JSON log file tailed by GET /api/logs.

    ``XBRLRAG_LOG_FILE`` when set; else this (serving) process's own log file, since
    every process now logs to its own (docs/specs/per-process-log-file.md).
    """
    override = os.environ.get("XBRLRAG_LOG_FILE")
    if override:
        return override
    own = current_log_file()
    return str(own) if own else os.path.join("logs", "xbrlrag_app.log")
