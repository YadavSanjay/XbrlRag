"""C5: payloads are cached content-addressed with a (url, sha256, fetched_at) record.

Oracle: requirements 7, 8 and 10 of docs/specs/sec-live-source.md; hashlib's SHA-256
(FIPS 180-4) of the served bytes, computed independently in the test.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.storage import LocalFilingStore

_PAYLOADS = "_sec_api/payloads/"


@pytest.mark.criterion("C5")
def test_record_and_stored_payload(kit):
    # oracle: requirement 7 — sha256 is hashlib's hex digest of the exact body; fetched_at is
    # the fixed wall clock 2026-01-02 03:04:05 UTC; the body is stored byte for byte
    body = kit.cf_bytes()
    sha = hashlib.sha256(body).hexdigest()
    parts = kit.make(respond=kit.routes(kit.all_routes()))
    fetched = parts.source.fetch_company_facts(kit.CIK)
    assert fetched["record"] == {"url": kit.CF_URL, "sha256": sha, "fetched_at": kit.WALL_ISO}
    assert fetched["document"] == json.loads(body)
    assert parts.store.read_bytes(f"{_PAYLOADS}{sha}.json") == body
    assert parts.source.payload(sha) == body


@pytest.mark.criterion("C5")
def test_cache_hit_same_instance_makes_no_request(kit):
    # oracle: requirement 8 — the second call is served from the cache: one request in all,
    # the same document and the same record, and no pacing sleep
    parts = kit.make(respond=kit.routes(kit.all_routes()))
    first = parts.source.fetch_frames("us-gaap", "Revenues", "USD", "CY2024")
    second = parts.source.fetch_frames("us-gaap", "Revenues", "USD", "CY2024")
    assert parts.transport.urls == [kit.REV_URL]
    assert second == first
    assert parts.source.frames("us-gaap", "Revenues", "USD", "CY2024") == json.loads(
        kit.rev_bytes())
    assert len(parts.transport.calls) == 1
    assert [s for s in parts.clock.sleeps if s > 0] == []


@pytest.mark.criterion("C5")
def test_offline_rerun_from_local_store(kit, tmp_path):
    # oracle: requirement 8 — a new source over the same LocalFilingStore with a transport
    # that fails on any request returns the same document and the original record
    first = kit.make(store=LocalFilingStore(tmp_path), respond=kit.routes(kit.all_routes()))
    rec1 = first.source.fetch_company_facts(kit.CIK)
    later = kit.make(store=LocalFilingStore(tmp_path), respond=kit.offline,
                     wall=kit.WALL.replace(year=2030))
    rec2 = later.source.fetch_company_facts("900001")
    assert later.transport.calls == []
    assert rec2 == rec1
    assert rec2["record"]["sha256"] == hashlib.sha256(kit.cf_bytes()).hexdigest()
    assert rec2["record"]["fetched_at"] == kit.WALL_ISO
    assert (tmp_path / "_sec_api" / "payloads" / f"{rec1['record']['sha256']}.json"
            ).read_bytes() == kit.cf_bytes()


@pytest.mark.criterion("C5")
def test_tampered_payload_fails_loud(kit):
    # oracle: requirements 8 and 10 — bytes that no longer hash to the record raise
    # ValueError, on a cache hit and in payload(); never a silent refetch
    parts = kit.make(respond=kit.routes(kit.all_routes()))
    sha = parts.source.fetch_company_facts(kit.CIK)["record"]["sha256"]
    parts.store.write_bytes(f"{_PAYLOADS}{sha}.json", b'{"cik": 900001, "facts": {}}')
    later = kit.make(store=parts.store, respond=kit.offline)
    with pytest.raises(ValueError):
        later.source.company_facts(kit.CIK)
    with pytest.raises(ValueError):
        later.source.payload(sha)
    assert later.transport.calls == []


@pytest.mark.criterion("C5")
def test_unknown_hash_is_key_error(kit):
    # oracle: requirement 10 — no payload with that hash: KeyError
    parts = kit.make(respond=kit.offline)
    with pytest.raises(KeyError):
        parts.source.payload(hashlib.sha256(b"never fetched").hexdigest())


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_equal_bodies_share_one_payload(kit):
    # oracle: requirement 7 — content addressing: two URLs with equal bodies record the same
    # sha256 and the store holds one payload
    body = b'{"same": true}'
    parts = kit.make(respond=kit.always(200, body))
    a = parts.source.fetch_company_facts("1")
    b = parts.source.fetch_frames("us-gaap", "Assets", "USD", "CY2024Q4I")
    assert a["record"]["sha256"] == b["record"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert a["record"]["url"] != b["record"]["url"]
    assert [k for k in parts.store.blobs if k.startswith(_PAYLOADS)] == [
        f"{_PAYLOADS}{hashlib.sha256(body).hexdigest()}.json"]


_json = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=8)
    | st.floats(allow_nan=False, allow_infinity=False),
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=6), inner,
                                                                max_size=4),
    max_leaves=12,
)


@pytest.mark.criterion("C5")
@given(doc=st.dictionaries(st.text(max_size=6), _json, max_size=5),
       indent=st.sampled_from([None, 1, 2]))
def test_any_body_hash_and_round_trip(kit, doc, indent):
    # oracle: requirement 7 — the record's hash is hashlib's SHA-256 of the exact bytes and
    # payload() returns those bytes; the document is json.loads of them
    body = json.dumps(doc, indent=indent).encode("utf-8")
    parts = kit.make(respond=kit.always(200, body))
    fetched = parts.source.fetch_company_facts("42")
    sha = hashlib.sha256(body).hexdigest()
    assert fetched["record"]["sha256"] == sha
    assert fetched["document"] == json.loads(body)
    assert parts.source.payload(sha) == body
