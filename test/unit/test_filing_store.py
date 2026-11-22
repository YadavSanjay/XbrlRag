"""Unit tests for the FilingStore ingest-storage abstraction (CL #3).

LocalFilingStore is exercised against a real temp dir; S3FilingStore against an
in-memory fake S3 client (no boto3 / network). The two backends must expose the
same key-based contract so the ingest funnel (CL #4) is backend-agnostic.
"""

from __future__ import annotations

import pytest

from src.ingest.sec_edgar.storage import (
    MANIFEST_NAME,
    FilingStore,
    LocalFilingStore,
    S3FilingStore,
)


# --------------------------------------------------------------------------
# LocalFilingStore
# --------------------------------------------------------------------------
def test_local_write_read_roundtrip(tmp_path):
    store = LocalFilingStore(tmp_path)
    store.write_text("NVDA/2026/10-K/manifest.yaml", "ticker: NVDA\n")
    store.write_bytes("NVDA/2026/10-K/primary.htm", b"<html/>")

    assert store.exists("NVDA/2026/10-K/manifest.yaml")
    assert store.read_text("NVDA/2026/10-K/manifest.yaml") == "ticker: NVDA\n"
    assert store.read_bytes("NVDA/2026/10-K/primary.htm") == b"<html/>"
    # bytes actually landed on disk in the {ticker}/{year}/{form} layout
    assert (tmp_path / "NVDA" / "2026" / "10-K" / "manifest.yaml").exists()


def test_local_missing_key(tmp_path):
    store = LocalFilingStore(tmp_path)
    assert not store.exists("AAPL/2024/10-K/manifest.yaml")


def test_local_local_path_is_real(tmp_path):
    store = LocalFilingStore(tmp_path)
    assert store.local_path("a/b.txt") == (tmp_path / "a" / "b.txt")


def test_local_iter_manifest_keys(tmp_path):
    store = LocalFilingStore(tmp_path)
    store.write_text(f"NVDA/2026/10-K/{MANIFEST_NAME}", "x")
    store.write_text(f"AAPL/2024/10-K/{MANIFEST_NAME}", "x")
    store.write_text("NVDA/2026/10-K/primary.htm", "not a manifest")

    keys = sorted(store.iter_manifest_keys())
    assert keys == [
        f"AAPL/2024/10-K/{MANIFEST_NAME}",
        f"NVDA/2026/10-K/{MANIFEST_NAME}",
    ]

    # scoped to a prefix
    scoped = sorted(store.iter_manifest_keys("NVDA"))
    assert scoped == [f"NVDA/2026/10-K/{MANIFEST_NAME}"]


def test_local_iter_manifest_keys_missing_prefix(tmp_path):
    store = LocalFilingStore(tmp_path)
    assert list(store.iter_manifest_keys("DOESNOTEXIST")) == []


def test_local_localize_dir_yields_real_dir(tmp_path):
    store = LocalFilingStore(tmp_path)
    store.write_text("NVDA/2026/10-K/manifest.yaml", "x")
    with store.localize_dir("NVDA/2026/10-K") as d:
        assert d == (tmp_path / "NVDA" / "2026" / "10-K")
        assert (d / "manifest.yaml").exists()


# --------------------------------------------------------------------------
# Fake S3 client + S3FilingStore
# --------------------------------------------------------------------------
class _NotFound(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "404"}}


class FakeS3:
    """Minimal in-memory stand-in for boto3's s3 client."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803 - boto3 casing
        self.store[Key] = Body if isinstance(Body, bytes) else bytes(Body)

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.store:
            raise _NotFound()
        data = self.store[Key]
        return {"Body": _Body(data)}

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.store:
            raise _NotFound()
        return {"ContentLength": len(self.store[Key])}

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):  # noqa: N803
        keys = sorted(k for k in self.store if k.startswith(Prefix))
        # exercise pagination: one key per page
        start = int(ContinuationToken) if ContinuationToken else 0
        page = keys[start : start + 1]
        resp = {"Contents": [{"Key": k} for k in page]}
        if start + 1 < len(keys):
            resp["IsTruncated"] = True
            resp["NextContinuationToken"] = str(start + 1)
        else:
            resp["IsTruncated"] = False
        return resp


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


@pytest.fixture
def s3_store():
    return S3FilingStore(bucket="b", prefix="filings", client=FakeS3())


def test_s3_write_read_roundtrip(s3_store):
    s3_store.write_text("NVDA/2026/10-K/manifest.yaml", "ticker: NVDA\n")
    s3_store.write_bytes("NVDA/2026/10-K/primary.htm", b"<html/>")
    assert s3_store.exists("NVDA/2026/10-K/manifest.yaml")
    assert s3_store.read_text("NVDA/2026/10-K/manifest.yaml") == "ticker: NVDA\n"
    assert s3_store.read_bytes("NVDA/2026/10-K/primary.htm") == b"<html/>"
    # object keys are prefixed with XBRLRAG_S3_PREFIX
    assert "filings/NVDA/2026/10-K/manifest.yaml" in s3_store._client.store


def test_s3_missing_key_not_exists(s3_store):
    assert not s3_store.exists("AAPL/2024/10-K/manifest.yaml")


def test_s3_exists_reraises_non_404(s3_store):
    def boom(Bucket, Key):
        raise RuntimeError("throttled")

    s3_store._client.head_object = boom
    with pytest.raises(RuntimeError):
        s3_store.exists("x")


def test_s3_iter_manifest_keys_strips_prefix_and_paginates(s3_store):
    s3_store.write_text("NVDA/2026/10-K/manifest.yaml", "x")
    s3_store.write_text("AAPL/2024/10-K/manifest.yaml", "x")
    s3_store.write_text("NVDA/2026/10-K/primary.htm", "x")  # not a manifest
    keys = sorted(s3_store.iter_manifest_keys())
    assert keys == ["AAPL/2024/10-K/manifest.yaml", "NVDA/2026/10-K/manifest.yaml"]


def test_s3_no_prefix_variant():
    store = S3FilingStore(bucket="b", prefix="", client=FakeS3())
    store.write_text("NVDA/2026/10-K/manifest.yaml", "x")
    assert "NVDA/2026/10-K/manifest.yaml" in store._client.store
    assert store.read_text("NVDA/2026/10-K/manifest.yaml") == "x"


def test_s3_localize_dir_downloads_and_cleans_up(s3_store):
    s3_store.write_text("NVDA/2026/10-K/manifest.yaml", "m")
    s3_store.write_bytes("NVDA/2026/10-K/primary.htm", b"<html/>")
    with s3_store.localize_dir("NVDA/2026/10-K") as d:
        assert (d / "manifest.yaml").read_text() == "m"
        assert (d / "primary.htm").read_bytes() == b"<html/>"
        tmp_root = d.parent.parent.parent  # the mkdtemp root
        assert tmp_root.exists()
    assert not tmp_root.exists()  # cleaned up on exit


def test_s3_local_path_is_none(s3_store):
    assert s3_store.local_path("x") is None


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def test_from_env_local_default(monkeypatch, tmp_path):
    for k in ("XBRLRAG_DEPLOY_MODE", "XBRLRAG_STORAGE_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SEC_EDGAR_ROOT", str(tmp_path))
    store = FilingStore.from_env()
    assert isinstance(store, LocalFilingStore)
    assert store.root == tmp_path.resolve()


def test_from_env_s3_when_aws(monkeypatch):
    monkeypatch.setenv("XBRLRAG_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("XBRLRAG_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("XBRLRAG_S3_PREFIX", "filings")
    # avoid constructing a real boto3 client
    import src.ingest.sec_edgar.storage as smod

    created = {}

    class _Fake(smod.S3FilingStore):
        @classmethod
        def from_env(cls, role="sec_edgar"):
            created["hit"] = True
            created["role"] = role
            return cls(bucket="my-bucket", prefix=f"filings/{role}", client=FakeS3())

    monkeypatch.setattr(smod, "S3FilingStore", _Fake)
    store = FilingStore.from_env()
    assert created.get("hit")
    assert isinstance(store, smod.S3FilingStore)
