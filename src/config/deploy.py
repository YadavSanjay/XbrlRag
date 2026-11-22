"""The single deployment-mode switch (CL #2).

``XBRLRAG_DEPLOY_MODE`` selects **backends and policies**, not connection details
(those stay in their existing per-service env vars: ``XBRL_DB_*``, ``QDRANT_*``,
``RAGINFER_URL``, ``XBRLRAG_S3_*``). This keeps "which endpoint" (env) orthogonal to
"which backend implementation" (mode).

    local  (default) -- filesystem for filings, no TLS requirement on Postgres.
    aws              -- S3 for filings, TLS required on Postgres.

Local is the default and must never regress: with ``XBRLRAG_DEPLOY_MODE`` unset,
every derived default matches today's behavior. Individual switches
(``XBRLRAG_STORAGE_BACKEND``, ``XBRL_DB_SSLMODE``) can override the mode-derived
default when set explicitly.

See ``docs/design/XbrlRag_AWS_Deployment.md``.
"""

from __future__ import annotations

import os
from enum import Enum


class DeployMode(str, Enum):
    LOCAL = "local"
    AWS = "aws"


STORAGE_BACKENDS = ("local", "s3")


def deploy_mode() -> DeployMode:
    """Resolve ``XBRLRAG_DEPLOY_MODE`` (unset or empty means ``local``).

    Returns:
        The deploy mode; the value is matched case-insensitively.

    Raises:
        ValueError: The variable is set to anything other than ``local`` or ``aws``.
            A typo must not run an AWS host with local storage and an optional-TLS
            Postgres connection, so it fails at startup instead of falling back.
    """
    raw = os.environ.get("XBRLRAG_DEPLOY_MODE", "").strip().lower()
    if not raw:
        return DeployMode.LOCAL
    try:
        return DeployMode(raw)
    except ValueError:
        allowed = ", ".join(mode.value for mode in DeployMode)
        raise ValueError(
            f"XBRLRAG_DEPLOY_MODE={raw!r} is not a deploy mode; use one of: {allowed}"
        ) from None


def is_aws() -> bool:
    return deploy_mode() is DeployMode.AWS


def is_local() -> bool:
    return deploy_mode() is DeployMode.LOCAL


def storage_backend() -> str:
    """Filing-storage backend: ``local`` or ``s3``.

    ``XBRLRAG_STORAGE_BACKEND`` overrides when set; otherwise it is derived from the
    deploy mode (``aws`` -> ``s3``, else ``local``). Lets a local box target S3
    for testing, or an AWS box stay on a mounted FS, without a mode change.

    Raises:
        ValueError: ``XBRLRAG_STORAGE_BACKEND`` is set to an unknown backend, or the
            deploy mode is invalid.
    """
    raw = os.environ.get("XBRLRAG_STORAGE_BACKEND", "").strip().lower()
    if raw in STORAGE_BACKENDS:
        return raw
    if raw:
        raise ValueError(
            f"XBRLRAG_STORAGE_BACKEND={raw!r} is not a storage backend; use one of: "
            f"{', '.join(STORAGE_BACKENDS)}"
        )
    return "s3" if is_aws() else "local"


def postgres_sslmode() -> str:
    """libpq ``sslmode`` for Postgres connections.

    ``XBRL_DB_SSLMODE`` overrides when set; otherwise derived from the deploy
    mode (``aws`` -> ``require`` for RDS, else ``prefer``). Consumed by the shared
    connection helper in :mod:`src.db.conn` (CL #6).
    """
    raw = os.environ.get("XBRL_DB_SSLMODE", "").strip().lower()
    if raw:
        return raw
    return "require" if is_aws() else "prefer"
