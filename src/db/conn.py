"""One place to build Postgres connection params for the XBRL fact store (CL #6).

Every fact-store / audit / populator module used to inline the same five-key
``psycopg2`` dict from ``XBRL_DB_*``. This helper collapses that duplication and
is the single seam for **TLS** (and, later, RDS IAM-auth token injection):

* Reads the same ``XBRL_DB_HOST/PORT/NAME/USER/PASSWORD`` env (unchanged).
* Adds ``sslmode`` from :func:`src.config.deploy.postgres_sslmode` —
  ``prefer`` in local mode (libpq's own default, so **local behavior is
  unchanged**) and ``require`` in ``deploy_mode=aws`` (RDS). ``XBRL_DB_SSLMODE``
  overrides either way.

Belt-and-suspenders for AWS: libpq also honors the process-level ``PGSSLMODE``
env, so any connection site not yet routed through this helper can still be
forced onto TLS by setting ``PGSSLMODE=require`` in the deploy environment.
"""

from __future__ import annotations

import os
from typing import Optional

from src.config.deploy import postgres_sslmode


def conn_params(sslmode: str | None = None, **overrides) -> dict:
    """Return psycopg2 connection kwargs from ``XBRL_DB_*`` env + TLS policy.

    ``**overrides`` win over the env-derived values (e.g. CLI ``--db-host``);
    ``sslmode`` defaults to :func:`postgres_sslmode` when not passed.
    """
    params = {
        "host": os.environ.get("XBRL_DB_HOST", "localhost"),
        "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
        "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
        "user": os.environ.get("XBRL_DB_USER", "postgres"),
        "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        "sslmode": sslmode or postgres_sslmode(),
    }
    params.update(overrides)
    return params


def connect(**overrides):
    """Open a psycopg2 connection using :func:`conn_params`."""
    import psycopg2

    return psycopg2.connect(**conn_params(**overrides))
