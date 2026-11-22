"""Cross-cutting deployment configuration (deploy mode + backend selection).

See :mod:`src.config.deploy` for the ``XBRLRAG_DEPLOY_MODE`` switch that selects
local vs AWS backends (local FS vs S3 for filings, TLS policy for Postgres).
"""

from src.config.deploy import (
    DeployMode,
    deploy_mode,
    is_aws,
    is_local,
    postgres_sslmode,
    storage_backend,
)

__all__ = [
    "DeployMode",
    "deploy_mode",
    "is_aws",
    "is_local",
    "postgres_sslmode",
    "storage_backend",
]
