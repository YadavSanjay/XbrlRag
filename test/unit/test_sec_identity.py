"""SEC EDGAR identity: no built-in default; --identity > SEC_EDGAR_IDENTITY > YAML key."""
from __future__ import annotations

import pytest

from src.ingest.sec_edgar.downloader.sec_filing_downloader import (
    SECFilingDownloader,
    resolve_sec_identity,
)

YAML_IDENTITY = 'identity: "Yaml User yaml@example.com"'


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_EDGAR_IDENTITY", raising=False)
    # edgartools' set_identity writes EDGAR_IDENTITY; registering it restores it after the test.
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.setenv("SEC_EDGAR_ROOT", str(tmp_path / "sec_edgar"))


def _config(tmp_path, identity_line: str):
    path = tmp_path / "downloads.yaml"
    path.write_text(f"{identity_line}\noutput_root:\ndownloads: []\n", encoding="utf-8")
    return path


def test_unset_identity_fails_loudly():
    with pytest.raises(ValueError, match="SEC_EDGAR_IDENTITY"):
        resolve_sec_identity()


def test_blank_identity_fails_loudly(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "   ")
    with pytest.raises(ValueError, match="SEC_EDGAR_IDENTITY"):
        resolve_sec_identity("")


def test_env_identity_used_when_no_explicit(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Env User env@example.com")
    assert resolve_sec_identity() == "Env User env@example.com"


def test_explicit_identity_wins_over_env(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Env User env@example.com")
    assert resolve_sec_identity(" Cli User cli@example.com ") == "Cli User cli@example.com"


def test_downloader_without_identity_fails_at_construction(tmp_path):
    with pytest.raises(ValueError, match="SEC_EDGAR_IDENTITY"):
        SECFilingDownloader(output_root=tmp_path)


def test_from_config_blank_yaml_key_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Env User env@example.com")
    dl = SECFilingDownloader.from_config(_config(tmp_path, "identity:"))
    assert dl.identity == "Env User env@example.com"


def test_from_config_env_wins_over_yaml_key(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Env User env@example.com")
    dl = SECFilingDownloader.from_config(_config(tmp_path, YAML_IDENTITY))
    assert dl.identity == "Env User env@example.com"


def test_from_config_explicit_wins_over_env_and_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Env User env@example.com")
    dl = SECFilingDownloader.from_config(
        _config(tmp_path, YAML_IDENTITY),
        identity="Cli User cli@example.com",
    )
    assert dl.identity == "Cli User cli@example.com"


def test_from_config_yaml_key_used_when_env_unset(tmp_path):
    dl = SECFilingDownloader.from_config(_config(tmp_path, YAML_IDENTITY))
    assert dl.identity == "Yaml User yaml@example.com"


def test_from_config_all_unset_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="SEC_EDGAR_IDENTITY"):
        SECFilingDownloader.from_config(_config(tmp_path, "identity:"))
