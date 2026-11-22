"""Tiny Postgres access layer for the observability endpoints.

Everything degrades gracefully: if the DB is unreachable or ``psycopg2`` is not
installed, queries return empty results and :func:`db_available` reports the
reason, so the UI can render an honest "observability store not configured"
state instead of 500ing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.api.config import db_conn_params
from src.logging_config import get_logger

logger = get_logger(__name__)

_LAST_ERROR: str | None = None


def _connect():
    import psycopg2  # imported lazily so the app boots without a DB

    return psycopg2.connect(**db_conn_params())


def db_available() -> tuple[bool, str | None]:
    """Return ``(ok, error)`` — whether the audit DB is reachable."""
    global _LAST_ERROR
    try:
        conn = _connect()
        conn.close()
        _LAST_ERROR = None
        return True, None
    except Exception as exc:  # pragma: no cover - environment dependent
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        logger.warning("audit DB not reachable: %s", _LAST_ERROR)
        return False, _LAST_ERROR


def query(sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    """Run *sql* and return a list of dict rows. Empty list on any failure."""
    try:
        conn = _connect()
    except Exception as exc:
        logger.warning("audit DB query skipped (no connection): %s", exc)
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                if cur.description is None:
                    return []
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("audit DB query failed: %s", exc)
        return []
    finally:
        conn.close()


def query_one(sql: str, params: tuple | None = None) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None
