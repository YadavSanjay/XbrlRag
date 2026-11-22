"""Unit tests for the shared Postgres conn helper (CL #6).

Local mode must be unchanged: same 5 env-driven keys, sslmode=prefer (libpq's
own default). AWS mode adds sslmode=require for RDS. XBRL_DB_SSLMODE overrides.
"""

from __future__ import annotations

from src.db.conn import conn_params


def _clear(monkeypatch):
    for k in (
        "XBRLRAG_DEPLOY_MODE",
        "XBRL_DB_SSLMODE",
        "XBRL_DB_HOST",
        "XBRL_DB_PORT",
        "XBRL_DB_NAME",
        "XBRL_DB_USER",
        "XBRL_DB_PASSWORD",
    ):
        monkeypatch.delenv(k, raising=False)


def test_local_defaults(monkeypatch):
    _clear(monkeypatch)
    p = conn_params()
    assert p == {
        "host": "localhost",
        "port": 5432,
        "dbname": "xbrl_db",
        "user": "postgres",
        "password": "postgres",
        "sslmode": "prefer",
    }


def test_reads_env(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRL_DB_HOST", "rds.internal")
    monkeypatch.setenv("XBRL_DB_PORT", "6000")
    monkeypatch.setenv("XBRL_DB_NAME", "prod")
    monkeypatch.setenv("XBRL_DB_USER", "app")
    monkeypatch.setenv("XBRL_DB_PASSWORD", "s3cret")
    p = conn_params()
    assert p["host"] == "rds.internal"
    assert p["port"] == 6000
    assert p["dbname"] == "prod"
    assert p["user"] == "app"
    assert p["password"] == "s3cret"


def test_aws_mode_requires_ssl(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", "aws")
    assert conn_params()["sslmode"] == "require"


def test_sslmode_env_override(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", "aws")
    monkeypatch.setenv("XBRL_DB_SSLMODE", "verify-full")
    assert conn_params()["sslmode"] == "verify-full"


def test_overrides_win(monkeypatch):
    _clear(monkeypatch)
    p = conn_params(host="cli-host", sslmode="disable")
    assert p["host"] == "cli-host"
    assert p["sslmode"] == "disable"
