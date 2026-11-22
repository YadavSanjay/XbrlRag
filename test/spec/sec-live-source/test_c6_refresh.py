"""C6: refresh refetches, replaces the URL's record and keeps every earlier payload.

Oracle: requirement 9 of docs/specs/sec-live-source.md; hashlib SHA-256 of the bodies.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

_OLD = b'{"cik": 900001, "facts": {"v": 1}}'
_NEW = b'{"cik": 900001, "facts": {"v": 2}}'


@pytest.mark.criterion("C6")
def test_refresh_replaces_record_and_keeps_old_payload(kit):
    # oracle: requirement 9 — a refresh requests again; the new body's hash and wall time
    # become the record; the old hash still resolves; a later plain source sees the new body
    first = kit.make(respond=kit.always(200, _OLD))
    old = first.source.fetch_company_facts(kit.CIK)["record"]
    wall2 = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
    second = kit.make(store=first.store, respond=kit.always(200, _NEW), wall=wall2,
                      refresh=True)
    new = second.source.fetch_company_facts(kit.CIK)["record"]
    assert len(second.transport.calls) == 1
    assert old["sha256"] == hashlib.sha256(_OLD).hexdigest()
    assert new == {"url": kit.CF_URL, "sha256": hashlib.sha256(_NEW).hexdigest(),
                   "fetched_at": "2026-02-03T04:05:06Z"}
    assert second.source.payload(old["sha256"]) == _OLD
    third = kit.make(store=first.store, respond=kit.offline)
    assert third.source.company_facts(kit.CIK) == {"cik": 900001, "facts": {"v": 2}}
    assert third.source.fetch_company_facts(kit.CIK)["record"] == new
    assert third.transport.calls == []


@pytest.mark.criterion("C6")
def test_refresh_requests_every_call(kit):
    # oracle: requirement 9 — with refresh=True every call requests its URL
    parts = kit.make(respond=kit.always(200, _NEW), refresh=True)
    parts.source.company_facts(kit.CIK)
    parts.source.company_facts(kit.CIK)
    assert parts.transport.urls == [kit.CF_URL, kit.CF_URL]


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(value=st.integers(min_value=-10**15, max_value=10**15))
def test_refresh_with_same_bytes_keeps_hash(kit, value):
    # oracle: requirement 9 — same bytes, same SHA-256 (content addressing); a request is made
    body = json.dumps({"cik": 900001, "facts": {"v": value}}).encode()
    first = kit.make(respond=kit.always(200, body))
    a = first.source.fetch_company_facts(kit.CIK)["record"]
    again = kit.make(store=first.store, respond=kit.always(200, body), refresh=True)
    b = again.source.fetch_company_facts(kit.CIK)["record"]
    assert len(again.transport.calls) == 1
    assert a["sha256"] == b["sha256"] == hashlib.sha256(body).hexdigest()
    assert again.source.payload(b["sha256"]) == body
