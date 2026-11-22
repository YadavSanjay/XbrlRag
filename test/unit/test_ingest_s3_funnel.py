"""Moto-backed smoke tests for the ingest -> FilingStore funnel (CL #4).

These drive real funnel entry points against a mocked-AWS S3 store so the S3
ingest path has automated coverage, not just code review. Local behavior is
covered by the modules' own tests (unchanged path).
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from src.ingest.sec_edgar.storage import S3FilingStore

BUCKET = "arag-ingest-test"


@pytest.fixture
def s3_store():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3FilingStore(bucket=BUCKET, prefix="sec_edgar", client=client)


def test_downloader_publishes_staged_filing_to_s3(s3_store, tmp_path):
    """The downloader's stage-then-publish step uploads a fully-assembled filing
    dir to S3 under its {TICKER}/{FY}/{FORM} key and drops the local staging."""
    from src.ingest.sec_edgar.downloader.sec_filing_downloader import (
        SECFilingDownloader,
    )

    dl = SECFilingDownloader(store=s3_store, identity="Test test@example.com", max_attempts=1)
    assert dl._staging is True  # S3 store => temp staging mode

    # Simulate a fully-downloaded filing on the staging root.
    target = dl.output_root / "NVDA" / "2026" / "10-K"
    target.mkdir(parents=True)
    # write bytes to avoid Windows text-mode \n -> \r\n translation in the test
    (target / "primary.htm").write_bytes(b"<html/>")
    (target / "nvda_htm.xml").write_bytes(b"<xbrl/>")
    (target / "manifest.yaml").write_bytes(b"ticker: NVDA\n")

    rel_key = target.relative_to(dl.output_root).as_posix()
    dl._publish_dir_to_store(target, rel_key)

    # Objects landed in S3 under the prefix, keyed by the on-disk layout...
    assert s3_store.read_text("NVDA/2026/10-K/manifest.yaml") == "ticker: NVDA\n"
    assert s3_store.read_bytes("NVDA/2026/10-K/primary.htm") == b"<html/>"
    assert s3_store.exists("NVDA/2026/10-K/nvda_htm.xml")
    # ...and the discovery walk finds the published manifest.
    assert list(s3_store.iter_manifest_keys("NVDA")) == ["NVDA/2026/10-K/manifest.yaml"]
    # ...and the local staging copy was removed.
    assert not target.exists()
