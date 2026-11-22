"""Moto-backed tests for S3FilingStore — real boto3 client, mocked AWS S3.

The hand-rolled fake in test_filing_store.py checks the store's logic; these
check it against **actual boto3/botocore semantics** (ClientError shape on a
missing head_object, list_objects_v2 pagination, put/get body handling) so the
S3 ingest path is trustworthy before it backs live ingest (CL #4).
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from src.ingest.sec_edgar.storage import FilingStore, LocalFilingStore, S3FilingStore

BUCKET = "xbrlrag-test-filings"


@pytest.fixture
def s3_client():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def store(s3_client):
    return S3FilingStore(bucket=BUCKET, prefix="filings", client=s3_client)


def test_write_read_roundtrip(store, s3_client):
    store.write_text("NVDA/2026/10-K/manifest.yaml", "ticker: NVDA\n")
    store.write_bytes("NVDA/2026/10-K/primary.htm", b"<html/>")

    assert store.read_text("NVDA/2026/10-K/manifest.yaml") == "ticker: NVDA\n"
    assert store.read_bytes("NVDA/2026/10-K/primary.htm") == b"<html/>"
    # landed under the XBRLRAG_S3_PREFIX
    body = s3_client.get_object(
        Bucket=BUCKET, Key="filings/NVDA/2026/10-K/manifest.yaml"
    )
    assert body["Body"].read() == b"ticker: NVDA\n"


def test_exists_true_false(store):
    store.write_text("AAPL/2024/10-K/manifest.yaml", "x")
    assert store.exists("AAPL/2024/10-K/manifest.yaml")
    # real botocore raises ClientError(404) on a missing head_object
    assert not store.exists("AAPL/2024/10-K/missing.yaml")


def test_iter_manifest_keys_strips_prefix(store):
    store.write_text("NVDA/2026/10-K/manifest.yaml", "x")
    store.write_text("AAPL/2024/10-K/manifest.yaml", "x")
    store.write_bytes("NVDA/2026/10-K/primary.htm", b"x")  # not a manifest
    keys = sorted(store.iter_manifest_keys())
    assert keys == ["AAPL/2024/10-K/manifest.yaml", "NVDA/2026/10-K/manifest.yaml"]


def test_iter_manifest_keys_scoped_prefix(store):
    store.write_text("NVDA/2026/10-K/manifest.yaml", "x")
    store.write_text("AAPL/2024/10-K/manifest.yaml", "x")
    assert sorted(store.iter_manifest_keys("NVDA")) == ["NVDA/2026/10-K/manifest.yaml"]


def test_iter_manifest_keys_paginates(store):
    # 2500 objects > the 1000-key list_objects_v2 page size exercises real
    # continuation-token pagination through moto.
    for i in range(1200):
        store.write_bytes(f"BULK/{i:05d}/filler.txt", b"x")
    store.write_text("BULK/manifest.yaml", "x")
    keys = list(store.iter_manifest_keys("BULK"))
    assert keys == ["BULK/manifest.yaml"]


def test_localize_dir_downloads_and_cleans_up(store):
    store.write_text("NVDA/2026/10-K/manifest.yaml", "m")
    store.write_bytes("NVDA/2026/10-K/primary.htm", b"<html/>")
    with store.localize_dir("NVDA/2026/10-K") as d:
        assert (d / "manifest.yaml").read_text() == "m"
        assert (d / "primary.htm").read_bytes() == b"<html/>"
        tmp_root = d.parent.parent.parent
        assert tmp_root.exists()
    assert not tmp_root.exists()


def test_from_env_builds_real_client(monkeypatch):
    monkeypatch.setenv("XBRLRAG_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("XBRLRAG_S3_BUCKET", BUCKET)
    monkeypatch.setenv("XBRLRAG_S3_PREFIX", "filings")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        store = FilingStore.from_env()
        assert isinstance(store, S3FilingStore)
        store.write_text("x/manifest.yaml", "ok")
        assert store.read_text("x/manifest.yaml") == "ok"


def test_from_env_local_when_backend_local(monkeypatch, tmp_path):
    for k in ("XBRLRAG_DEPLOY_MODE", "XBRLRAG_STORAGE_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SEC_EDGAR_ROOT", str(tmp_path))
    assert isinstance(FilingStore.from_env(), LocalFilingStore)
