"""CL #4c — Arelle ingestor funnel: store-based discovery + localize_dir.

Arelle is an external subprocess needing real local files, so these tests cover
the funnel *around* it (discovery, localize, key-derivation, dry-run
orchestration) without invoking Arelle — dry_run returns before the subprocess.
"""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import ArelleXBRLIngestor
from src.ingest.sec_edgar.storage import LocalFilingStore, S3FilingStore

MANIFEST = b"primary_document: nvda.htm\naccession_no: 0001-25-000001\nform: 10-K\n"
REL = "NVDA/2026/10-K"
BUCKET = "arag-arelle-test"


def _ingestor(store):
    return ArelleXBRLIngestor(
        arelle_cmd="arelle",
        sec_root=Path("."),
        db_conn_params={},
        persist_audit=False,
        store=store,
    )


class _DummyConn:
    def close(self):
        pass


# --------------------------------------------------------------------------
# discovery + localize
# --------------------------------------------------------------------------
def test_discovery_and_localize_local(tmp_path):
    filing = tmp_path / REL
    filing.mkdir(parents=True)
    (filing / "manifest.yaml").write_bytes(MANIFEST)
    (filing / "nvda.htm").write_bytes(b"<xbrl/>")

    ing = _ingestor(LocalFilingStore(tmp_path))
    assert ing._discover_filing_keys() == [REL]
    with ing._resolve_local_dir(REL) as d:
        assert d == (tmp_path / REL)  # real dir, no temp copy
        assert (d / "manifest.yaml").exists() and (d / "nvda.htm").exists()


def test_discovery_and_localize_s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        store = S3FilingStore(bucket=BUCKET, prefix="data/sec_edgar", client=client)
        store.write_bytes(f"{REL}/manifest.yaml", MANIFEST)
        store.write_bytes(f"{REL}/nvda.htm", b"<xbrl/>")
        # a sibling linkbase — Arelle needs the whole dir localized, not just the htm
        store.write_bytes(f"{REL}/nvda_lab.xml", b"<linkbase/>")

        ing = _ingestor(store)
        assert ing._discover_filing_keys() == [REL]
        with ing._resolve_local_dir(REL) as d:
            assert (d / "manifest.yaml").read_bytes() == MANIFEST
            assert (d / "nvda.htm").exists() and (d / "nvda_lab.xml").exists()
            tmp_root = d.parent.parent.parent
            assert tmp_root.exists()
        assert not tmp_root.exists()  # temp cleaned up after the context


def test_to_rel_key_variants(tmp_path):
    ing = _ingestor(LocalFilingStore(tmp_path))
    ing.sec_root = tmp_path
    assert ing._to_rel_key(tmp_path / REL) == REL  # absolute under root
    assert ing._to_rel_key(REL) == REL  # already a key
    assert ing._to_rel_key("NVDA\\2026\\10-K") == REL  # windows-sep key


# --------------------------------------------------------------------------
# end-to-end dry-run (no Arelle subprocess)
# --------------------------------------------------------------------------
def test_dry_run_end_to_end_local(tmp_path, monkeypatch):
    filing = tmp_path / REL
    filing.mkdir(parents=True)
    (filing / "manifest.yaml").write_bytes(MANIFEST)
    (filing / "nvda.htm").write_bytes(b"<xbrl/>")

    ing = _ingestor(LocalFilingStore(tmp_path))
    ing.sec_root = tmp_path
    monkeypatch.setattr(ing, "_connect_db", lambda: _DummyConn())
    monkeypatch.setattr(ing, "_is_already_ingested", lambda conn, htm, accession_no=None: False)

    stats = ing.ingest_all(dry_run=True)
    assert stats.total_discovered == 1
    assert stats.dry_run_would_ingest == 1
    assert stats.results[0].ticker == "NVDA"
    assert stats.results[0].year == "2026"
    assert stats.results[0].form_type == "10-K"


def test_dry_run_end_to_end_s3(monkeypatch):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        store = S3FilingStore(bucket=BUCKET, prefix="data/sec_edgar", client=client)
        store.write_bytes(f"{REL}/manifest.yaml", MANIFEST)
        store.write_bytes(f"{REL}/nvda.htm", b"<xbrl/>")

        ing = _ingestor(store)
        monkeypatch.setattr(ing, "_connect_db", lambda: _DummyConn())
        monkeypatch.setattr(ing, "_is_already_ingested", lambda conn, htm, accession_no=None: False)

        stats = ing.ingest_all(dry_run=True)
        assert stats.total_discovered == 1
        assert stats.dry_run_would_ingest == 1
        # identity derived from the store key, not a local path
        assert (stats.results[0].ticker, stats.results[0].year) == ("NVDA", "2026")
