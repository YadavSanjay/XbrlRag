"""C7: keys built through the live source carry the record of the payload they came from.

Oracle: requirements 11-12 of docs/specs/sec-live-source.md; expected values read off the
committed fixtures by hand (FY2024 revenue 391_035_000_000, 2023-10-01 -> 2024-09-28,
accession 0000900001-24-000030; CY2024Q4I assets 364_980_000_000 at 2024-12-28).
"""

from __future__ import annotations

import hashlib
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError
from src.eval.keys.sec_live import calendar_key_with_payload, fiscal_key_with_payload


@pytest.mark.criterion("C7")
def test_fiscal_key_with_payload(kit):
    # oracle: fixture — the only fy=2024 10-K FY entry ending last is 2023-10-01..2024-09-28,
    # 391_035_000_000; the record hashes the served companyfacts bytes; one request
    parts = kit.make(respond=kit.routes(kit.all_routes()))
    out = fiscal_key_with_payload(parts.source, kit.CIK, "us-gaap:Revenues", "USD", 2024)
    assert parts.transport.urls == [kit.CF_URL]
    key = out["key"]
    assert key["value"] == 391_035_000_000
    assert (key["period_start"], key["period_end"]) == ("2023-10-01", "2024-09-28")
    assert key["accession"] == kit.ACCN_FY24
    assert key["fiscal_year"] == 2024
    assert key["key_source"] == "sec_companyfacts"
    assert out["payload"] == {"url": kit.CF_URL,
                              "sha256": hashlib.sha256(kit.cf_bytes()).hexdigest(),
                              "fetched_at": kit.WALL_ISO}


@pytest.mark.criterion("C7")
def test_calendar_key_with_payload_duration_and_instant(kit):
    # oracle: fixture — the CY2024 revenue frame's row for CIK 900001 is 391_035_000_000 from
    # 0000900001-24-000030; the CY2024Q4I assets row is 364_980_000_000 at 2024-12-28
    parts = kit.make(respond=kit.routes(kit.all_routes()))
    rev = calendar_key_with_payload(parts.source, kit.CIK, "us-gaap:Revenues", "USD", 2024)
    assert parts.transport.urls == [kit.REV_URL]
    assert rev["key"]["value"] == 391_035_000_000
    assert rev["key"]["accession"] == kit.ACCN_FY24
    assert rev["key"]["calendar_year"] == 2024
    assert rev["key"]["key_source"] == "sec_frames"
    assert rev["payload"]["sha256"] == hashlib.sha256(kit.rev_bytes()).hexdigest()
    assert rev["payload"]["url"] == kit.REV_URL
    assets = calendar_key_with_payload(parts.source, "900001", "us-gaap:Assets", "USD", 2024,
                                       instant=True)
    assert parts.transport.urls == [kit.REV_URL, kit.ASSETS_URL]
    assert assets["key"]["value"] == 364_980_000_000
    assert assets["key"]["period_start"] is None
    assert assets["key"]["period_end"] == "2024-12-28"
    assert assets["payload"]["sha256"] == hashlib.sha256(kit.assets_bytes()).hexdigest()


@pytest.mark.criterion("C7")
def test_missing_frame_is_key_not_found(kit):
    # oracle: requirement 12 — a 404 frame raises (a KeyNotFoundError), no key is returned
    parts = kit.make(respond=kit.routes(kit.all_routes()))
    with pytest.raises(KeyNotFoundError):
        calendar_key_with_payload(parts.source, kit.CIK, "us-gaap:Revenues", "USD", 2019)
    assert parts.transport.urls == [f"{kit.BASE}/frames/us-gaap/Revenues/USD/CY2019.json"]


@pytest.mark.criterion("C7")
def test_plain_methods_return_the_fetched_document(kit):
    # oracle: requirement 11 — company_facts / frames equal fetch_*(...)["document"], which is
    # json.loads of the fixture bytes
    parts = kit.make(respond=kit.routes(kit.all_routes()))
    assert parts.source.company_facts(kit.CIK) == parts.source.fetch_company_facts(
        kit.CIK)["document"] == json.loads(kit.cf_bytes())
    assert parts.source.frames("us-gaap", "Assets", "USD", "CY2024Q4I") == \
        parts.source.fetch_frames("us-gaap", "Assets", "USD", "CY2024Q4I")["document"] == \
        json.loads(kit.assets_bytes())


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(indent=st.sampled_from([None, 0, 1, 3, 8]), sort_keys=st.booleans())
def test_reformatted_payload_changes_hash_not_key(kit, indent, sort_keys):
    # oracle: requirement 12 — the hash names the bytes, the key depends only on the facts:
    # re-serializing the same document gives the fixture's key values and the new bytes' hash
    body = json.dumps(json.loads(kit.cf_bytes()), indent=indent, sort_keys=sort_keys).encode()
    parts = kit.make(respond=kit.always(200, body))
    out = fiscal_key_with_payload(parts.source, kit.CIK, "us-gaap:Revenues", "USD", 2024)
    assert out["payload"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert out["payload"]["sha256"] != hashlib.sha256(kit.cf_bytes()).hexdigest()
    assert out["key"]["value"] == 391_035_000_000
    assert (out["key"]["period_start"], out["key"]["period_end"]) == ("2023-10-01",
                                                                      "2024-09-28")
    assert out["key"]["accession"] == kit.ACCN_FY24
