"""CL #4b — canonicalizer funnel: same code over local FS and S3 (two prefixes).

Verifies the canonicalizer reads the raw iXBRL and writes canonical.htm + meta
through its FilingStores, that the transform still strips style/script/link, and
that the output-existence skip works — in both local and mocked-S3 modes.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from src.ingest.sec_edgar.xbrl.html_canonicalizer import HTMLCanonicalizer

RAW_IXBRL = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><style>p{color:red}</style><link rel="stylesheet" href="a.css"/></head>
<body><p style="color:red">Hello <b>world</b></p><script>var x=1;</script></body>
</html>"""

MANIFEST = "ticker: NVDA\nfiscal_year: 2026\nform: 10-K\nprimary_document: nvda.htm\n"
REL = "NVDA/2026/10-K"


def _assert_canonicalized(canon_bytes: bytes):
    text = canon_bytes.decode("utf-8")
    assert "Hello" in text and "world" in text  # prose preserved
    assert "<style" not in text and "<script" not in text  # dropped
    assert "stylesheet" not in text  # <link> dropped
    assert 'style="color:red"' not in text  # style attr stripped


# --------------------------------------------------------------------------
# Local FS
# --------------------------------------------------------------------------
def test_local_canonicalize(tmp_path, monkeypatch):
    for k in ("XBRLRAG_DEPLOY_MODE", "XBRLRAG_STORAGE_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    in_root = tmp_path / "sec_edgar"
    out_root = tmp_path / "normalized"
    filing = in_root / REL
    filing.mkdir(parents=True)
    (filing / "manifest.yaml").write_bytes(MANIFEST.encode())
    (filing / "nvda.htm").write_bytes(RAW_IXBRL)

    canon = HTMLCanonicalizer(input_root=in_root, output_root=out_root)
    results = canon.canonicalize_all()

    assert len(results) == 1 and not results[0].skipped and not results[0].error
    out_file = out_root / REL / "canonical.htm"
    assert out_file.exists()
    _assert_canonicalized(out_file.read_bytes())
    assert (out_root / REL / "canonicalization_meta.json").exists()

    # second run skips (output exists, no force)
    again = canon.canonicalize_all()
    assert again[0].skipped


def test_local_force_overwrites(tmp_path, monkeypatch):
    for k in ("XBRLRAG_DEPLOY_MODE", "XBRLRAG_STORAGE_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    in_root = tmp_path / "sec_edgar"
    filing = in_root / REL
    filing.mkdir(parents=True)
    (filing / "manifest.yaml").write_bytes(MANIFEST.encode())
    (filing / "nvda.htm").write_bytes(RAW_IXBRL)

    HTMLCanonicalizer(input_root=in_root, output_root=tmp_path / "n").canonicalize_all()
    forced = HTMLCanonicalizer(
        input_root=in_root, output_root=tmp_path / "n", force=True
    ).canonicalize_all()
    assert not forced[0].skipped and not forced[0].error


# --------------------------------------------------------------------------
# S3 (moto) — one bucket, two prefixes
# --------------------------------------------------------------------------
def test_s3_canonicalize(monkeypatch):
    monkeypatch.setenv("XBRLRAG_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("XBRLRAG_S3_BUCKET", "xbrlrag-canon-test")
    monkeypatch.setenv("XBRLRAG_S3_PREFIX", "data")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="xbrlrag-canon-test")
        # seed the raw filing under <prefix>/sec_edgar/<rel>/
        s3.put_object(
            Bucket="xbrlrag-canon-test",
            Key=f"data/sec_edgar/{REL}/manifest.yaml",
            Body=MANIFEST.encode(),
        )
        s3.put_object(
            Bucket="xbrlrag-canon-test",
            Key=f"data/sec_edgar/{REL}/nvda.htm",
            Body=RAW_IXBRL,
        )

        results = HTMLCanonicalizer().canonicalize_all()
        assert len(results) == 1 and not results[0].skipped and not results[0].error

        # canonical.htm landed under <prefix>/normalized/<rel>/
        obj = s3.get_object(
            Bucket="xbrlrag-canon-test", Key=f"data/normalized/{REL}/canonical.htm"
        )
        _assert_canonicalized(obj["Body"].read())
        s3.head_object(
            Bucket="xbrlrag-canon-test",
            Key=f"data/normalized/{REL}/canonicalization_meta.json",
        )

        # re-run skips (output object exists)
        again = HTMLCanonicalizer().canonicalize_all()
        assert again[0].skipped
