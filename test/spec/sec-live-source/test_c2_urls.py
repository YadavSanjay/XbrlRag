"""C2: the companyfacts and frames URLs, CIK normalization and JSON parsing.

Oracle: requirement 4 of docs/specs/sec-live-source.md; the URL forms of the SEC "EDGAR
Application Programming Interfaces" page.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st


@pytest.mark.criterion("C2")
def test_company_facts_url_and_document(kit):
    # oracle: SEC API docs — companyfacts/CIK##########.json, CIK zero-padded to 10;
    # the document is json.loads of the served bytes
    body = b'{"cik": 320193, "entityName": "Synthetic", "facts": {}}'
    parts = kit.make(respond=kit.always(200, body))
    doc = parts.source.company_facts("320193")
    assert parts.transport.urls == [
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"]
    assert doc == {"cik": 320193, "entityName": "Synthetic", "facts": {}}


@pytest.mark.criterion("C2")
def test_frames_url_and_document(kit):
    # oracle: SEC API docs — frames/{taxonomy}/{tag}/{unit}/{period}.json
    parts = kit.make(respond=kit.routes(kit.all_routes()))
    doc = parts.source.frames("us-gaap", "Revenues", "USD", "CY2024")
    assert parts.transport.urls == [
        "https://data.sec.gov/api/xbrl/frames/us-gaap/Revenues/USD/CY2024.json"]
    assert doc == json.loads(kit.rev_bytes())
    assert doc["data"][0]["val"] == 391_035_000_000


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("bad", ["12a4", "", "   ", "-5", "12345678901", "1.0"])
def test_bad_cik_raises_without_request(kit, bad):
    # oracle: requirement 4 — not 1 to 10 decimal digits: ValueError, no request
    parts = kit.make(respond=kit.offline)
    with pytest.raises(ValueError):
        parts.source.company_facts(bad)
    assert parts.transport.calls == []


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(n=st.integers(min_value=1, max_value=9_999_999_999))
def test_padded_and_unpadded_cik_request_the_same_url(kit, n):
    # oracle: requirement 4 — cik10 = str(int(cik)).zfill(10), computed here in closed form
    expected = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{n:010d}.json"
    seen = []
    for cik in (str(n), str(n).zfill(10)):
        parts = kit.make(respond=kit.always(200, b'{"cik": 1, "facts": {}}'))
        parts.source.company_facts(cik)
        seen.extend(parts.transport.urls)
    assert seen == [expected, expected]
