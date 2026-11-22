"""Unit tests for the deploy-mode switch (CL #2).

Local is the default and must never regress: with XBRLRAG_DEPLOY_MODE unset, every
derived default matches today's local behavior.
"""

from __future__ import annotations

import pytest

from src.config import deploy as d


def _clear(monkeypatch):
    for key in ("XBRLRAG_DEPLOY_MODE", "XBRLRAG_STORAGE_BACKEND", "XBRL_DB_SSLMODE"):
        monkeypatch.delenv(key, raising=False)


def test_default_is_local(monkeypatch):
    _clear(monkeypatch)
    assert d.deploy_mode() is d.DeployMode.LOCAL
    assert d.is_local() and not d.is_aws()
    assert d.storage_backend() == "local"
    assert d.postgres_sslmode() == "prefer"


def test_aws_mode(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", "aws")
    assert d.deploy_mode() is d.DeployMode.AWS
    assert d.is_aws() and not d.is_local()
    assert d.storage_backend() == "s3"
    assert d.postgres_sslmode() == "require"


@pytest.mark.parametrize("typo", ["gcp", "aws-prod", "AWS ", "locl"])
def test_unrecognized_mode_raises(monkeypatch, typo):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", typo)
    if typo.strip().lower() == "aws":
        assert d.is_aws()  # surrounding whitespace and case are not typos
        return
    with pytest.raises(ValueError, match="XBRLRAG_DEPLOY_MODE"):
        d.deploy_mode()
    # Every mode-derived default fails the same way: none silently picks local.
    for derived in (d.is_aws, d.is_local, d.storage_backend, d.postgres_sslmode):
        with pytest.raises(ValueError, match="XBRLRAG_DEPLOY_MODE"):
            derived()


def test_empty_mode_is_local(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", "  ")
    assert d.deploy_mode() is d.DeployMode.LOCAL


def test_mode_is_case_insensitive(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", "AWS")
    assert d.is_aws()


def test_storage_backend_override_wins_over_mode(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", "aws")
    monkeypatch.setenv("XBRLRAG_STORAGE_BACKEND", "local")
    assert d.storage_backend() == "local"

    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", "local")
    monkeypatch.setenv("XBRLRAG_STORAGE_BACKEND", "s3")
    assert d.storage_backend() == "s3"


def test_invalid_storage_backend_raises(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_STORAGE_BACKEND", "azure-blob")
    with pytest.raises(ValueError, match="XBRLRAG_STORAGE_BACKEND"):
        d.storage_backend()


def test_sslmode_override_wins_over_mode(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("XBRLRAG_DEPLOY_MODE", "local")
    monkeypatch.setenv("XBRL_DB_SSLMODE", "verify-full")
    assert d.postgres_sslmode() == "verify-full"
