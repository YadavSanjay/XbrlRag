"""Unit tests for QdrantVectorDBClient connection-param resolution (CL #1).

The client must read QDRANT_HOST / QDRANT_PORT / QDRANT_API_KEY / QDRANT_HTTPS /
QDRANT_VERIFY from the environment when the ctor args are not passed, so a
non-localhost Qdrant (in-VPC on AWS, or the ``qdrant`` service in the local
compose stack) is reachable at the two zero-arg call sites without editing them.
Env unset must preserve the localhost:6333 default (local behavior unchanged),
and QDRANT_VERIFY=false must skip TLS verification for the self-signed in-VPC cert.
"""

from __future__ import annotations

import pytest

from src.vectorstores import qdrant_vectordb_client as qmod


@pytest.fixture
def captured_client(monkeypatch):
    """Patch QdrantClient so we can inspect the kwargs it was built with."""
    captured = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(qmod, "QdrantClient", _FakeClient)
    return captured


def _clear_qdrant_env(monkeypatch):
    for key in (
        "QDRANT_HOST",
        "QDRANT_PORT",
        "QDRANT_API_KEY",
        "QDRANT_HTTPS",
        "QDRANT_VERIFY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_defaults_to_localhost_when_env_unset(monkeypatch, captured_client):
    _clear_qdrant_env(monkeypatch)
    qmod.QdrantVectorDBClient()
    assert captured_client["host"] == "localhost"
    assert captured_client["port"] == 6333
    assert "api_key" not in captured_client
    assert "https" not in captured_client


def test_reads_host_and_port_from_env(monkeypatch, captured_client):
    _clear_qdrant_env(monkeypatch)
    monkeypatch.setenv("QDRANT_HOST", "qdrant.internal")
    monkeypatch.setenv("QDRANT_PORT", "7000")
    qmod.QdrantVectorDBClient()
    assert captured_client["host"] == "qdrant.internal"
    assert captured_client["port"] == 7000


def test_explicit_args_override_env(monkeypatch, captured_client):
    monkeypatch.setenv("QDRANT_HOST", "qdrant.internal")
    monkeypatch.setenv("QDRANT_PORT", "7000")
    qmod.QdrantVectorDBClient(host="explicit", port=6543)
    assert captured_client["host"] == "explicit"
    assert captured_client["port"] == 6543


def test_api_key_and_https_from_env(monkeypatch, captured_client):
    _clear_qdrant_env(monkeypatch)
    monkeypatch.setenv("QDRANT_API_KEY", "secret-key")
    monkeypatch.setenv("QDRANT_HTTPS", "true")
    qmod.QdrantVectorDBClient()
    assert captured_client["api_key"] == "secret-key"
    assert captured_client["https"] is True


def test_https_falsey_values_not_passed(monkeypatch, captured_client):
    _clear_qdrant_env(monkeypatch)
    monkeypatch.setenv("QDRANT_HTTPS", "0")
    qmod.QdrantVectorDBClient()
    # https=False resolved -> still passed so the client doesn't assume TLS
    assert captured_client.get("https") is False


def test_verify_false_from_env_skips_tls_check(monkeypatch, captured_client):
    _clear_qdrant_env(monkeypatch)
    monkeypatch.setenv("QDRANT_HTTPS", "true")
    monkeypatch.setenv("QDRANT_VERIFY", "false")
    qmod.QdrantVectorDBClient()
    # TLS stays on, but the self-signed cert is not verified.
    assert captured_client["https"] is True
    assert captured_client["verify"] is False


def test_verify_not_passed_when_env_unset(monkeypatch, captured_client):
    _clear_qdrant_env(monkeypatch)
    monkeypatch.setenv("QDRANT_HTTPS", "true")
    qmod.QdrantVectorDBClient()
    # No QDRANT_VERIFY -> don't pass verify -> qdrant-client's default (verify on).
    assert "verify" not in captured_client


def test_xbrl_subclass_inherits_env_resolution(monkeypatch, captured_client):
    _clear_qdrant_env(monkeypatch)
    monkeypatch.setenv("QDRANT_HOST", "qdrant.vpc")
    from src.vectorstores.xbrl_qdrant_vectordb_client import XBRLQdrantVectorDBClient

    XBRLQdrantVectorDBClient()
    assert captured_client["host"] == "qdrant.vpc"
