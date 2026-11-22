"""WS-9 / E1 — the corpus fingerprint.

An accuracy number is meaningless without the corpus state it was measured on: a
run that "improved" may simply have had three more filings loaded. Every run records
a fingerprint, and the comparison view refuses to present a delta across two
different fingerprints without an explicit override (design doc §5.3).

Derived from WS-1's ``publish_manifest`` — the content-addressed record of which
filings actually passed the trust gates and are visible to queries — so the
fingerprint tracks *servable* data, not merely downloaded data. Falls back to a fact
census when the manifest is absent, and to ``"unknown"`` when Postgres is
unreachable: a missing fingerprint must degrade the comparison, not break the run.
"""

from __future__ import annotations

import hashlib
import subprocess
from typing import Any, List, Optional, Tuple

from src.logging_config import get_logger

logger = get_logger(__name__)

UNKNOWN = "unknown"

_MANIFEST_SQL = """
SELECT accession_no, is_published
FROM   publish_manifest
ORDER  BY accession_no
"""

_CENSUS_SQL = """
SELECT ticker, fiscal_year, count(*)
FROM   mv_financial_facts
WHERE  ticker IS NOT NULL
GROUP  BY ticker, fiscal_year
ORDER  BY ticker, fiscal_year
"""


def _digest(rows: list[tuple], prefix: str) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update("|".join("" if v is None else str(v) for v in row).encode("utf-8"))
        h.update(b"\n")
    return f"{prefix}:{h.hexdigest()[:16]}"


def compute_fingerprint(conn: Any = None) -> str:
    """A stable short id for the current servable corpus state.

    ``pm:<hash>`` from the publish manifest, else ``facts:<hash>`` from a fact census,
    else ``"unknown"``.
    """
    close_after = False
    try:
        if conn is None:
            from src.db.conn import connect

            conn = connect()
            close_after = True

        with conn.cursor() as cur:
            try:
                cur.execute(_MANIFEST_SQL)
                rows = cur.fetchall()
                if rows:
                    return _digest(rows, "pm")
            except Exception:
                # publish_manifest is WS-1; a corpus predating it still needs a
                # fingerprint, so fall through to the census.
                conn.rollback()

            cur.execute(_CENSUS_SQL)
            rows = cur.fetchall()
            return _digest(rows, "facts") if rows else UNKNOWN
    except Exception:
        logger.info("corpus fingerprint unavailable (Postgres unreachable)")
        return UNKNOWN
    finally:
        if close_after and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def git_sha(short: bool = True) -> str | None:
    """The code revision a run was produced by. ``None`` outside a git checkout."""
    cmd = (
        ["git", "rev-parse", "--short", "HEAD"]
        if short
        else ["git", "rev-parse", "HEAD"]
    )
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=False
        )
        sha = out.stdout.strip()
        return sha or None
    except Exception:
        return None
