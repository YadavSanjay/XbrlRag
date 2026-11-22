"""C7: the SEC companyfacts client — URL, identity header, cache, pacing, errors.

Oracle: requirement 10 of docs/specs/sec-companyfacts-check.md and the SEC's fair access
policy (at most 10 requests per second, so ``min_interval_s = 0.1`` between fetch starts).
``http_get``, ``clock`` and ``sleep`` are fakes; the cache is a temporary directory.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_check.sec_client import SecCompanyFactsClient

IDENTITY = "XbrlRag Spec Tests spec-tests@example.com"
BASE = "https://data.sec.gov/api/xbrl/companyfacts/"


def _client(kit, cache_dir, http, clock, min_interval_s=0.1):
    return SecCompanyFactsClient(IDENTITY, Path(cache_dir), http_get=http,
                                 min_interval_s=min_interval_s, clock=clock.clock,
                                 sleep=clock.sleep)


@pytest.mark.criterion("C7")
def test_first_fetch_url_header_result_and_cache(kit, tmp_path):
    # oracle: requirement 10 / C7 — URL .../CIK{cik:0>10}.json, User-Agent = identity, the
    # parsed body is returned, and the body is cached as CIK0000320193.json
    clock = kit.FakeClock()
    http = kit.FakeHttp(clock, default=(200, b'{"cik": 320193}'))
    got = _client(kit, tmp_path, http, clock).company_facts("320193")
    assert got == {"cik": 320193}
    assert len(http.calls) == 1
    assert http.calls[0]["url"] == BASE + "CIK0000320193.json"
    assert http.calls[0]["headers"].get("User-Agent") == IDENTITY
    cached = tmp_path / "CIK0000320193.json"
    assert cached.exists()
    assert json.loads(cached.read_bytes()) == {"cik": 320193}


@pytest.mark.criterion("C7")
def test_second_call_for_same_cik_reads_the_cache(kit, tmp_path):
    # oracle: requirement 10 — "reads that file instead of fetching when it exists": one
    # HTTP call over two requests for the same CIK, both returning the parsed body
    clock = kit.FakeClock()
    http = kit.FakeHttp(clock, default=(200, b'{"cik": 320193}'))
    client = _client(kit, tmp_path, http, clock)
    first = client.company_facts("320193")
    clock.t += 5.0
    second = client.company_facts("0000320193")
    assert first == {"cik": 320193}
    assert second == {"cik": 320193}
    assert len(http.calls) == 1


@pytest.mark.criterion("C7")
def test_existing_cache_file_means_no_fetch(kit, tmp_path):
    # oracle: requirement 10 — a cache file written before the client exists is read, not
    # re-fetched (e.g. by a new client in a later run)
    (tmp_path / "CIK0000789019.json").write_bytes(b'{"cik": 789019, "entityName": "X"}')
    clock = kit.FakeClock()
    http = kit.FakeHttp(clock)
    got = _client(kit, tmp_path, http, clock).company_facts("789019")
    assert got == {"cik": 789019, "entityName": "X"}
    assert http.calls == []


@pytest.mark.criterion("C7")
def test_two_fetches_003_apart_sleep_007(kit, tmp_path):
    # oracle: C7 — fetches for two CIKs requested at fake times 0.00 and 0.03 with
    # min_interval_s 0.1: the second waits 0.1 - 0.03 = 0.07, and starts at 0.10
    clock = kit.FakeClock(0.0)
    http = kit.FakeHttp(clock)
    client = _client(kit, tmp_path, http, clock)
    client.company_facts("320193")
    clock.t = 0.03
    client.company_facts("789019")
    assert [c["url"] for c in http.calls] == [BASE + "CIK0000320193.json",
                                               BASE + "CIK0000789019.json"]
    # tolerance: 0.1 - 0.03 in binary floating point is 0.07000000000000001
    assert sum(clock.sleeps) == pytest.approx(0.07, abs=1e-9)
    assert http.calls[1]["t"] - http.calls[0]["t"] == pytest.approx(0.1, abs=1e-9)


@pytest.mark.criterion("C7")
def test_non_200_raises_and_writes_no_cache(kit, tmp_path):
    # oracle: requirement 10 — a non-200 status raises RuntimeError naming the URL and the
    # status, and no cache file is written
    clock = kit.FakeClock()
    http = kit.FakeHttp(clock, default=(404, b"Not Found"))
    client = _client(kit, tmp_path, http, clock)
    with pytest.raises(RuntimeError) as err:
        client.company_facts("320193")
    assert "404" in str(err.value)
    assert BASE + "CIK0000320193.json" in str(err.value)
    assert len(http.calls) == 1
    assert not (tmp_path / "CIK0000320193.json").exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.criterion("C7")
@given(
    ciks=st.lists(st.integers(min_value=1, max_value=9_999_999_999), min_size=1, max_size=8,
                  unique=True),
    gaps=st.lists(st.floats(min_value=0.0, max_value=0.3, allow_nan=False), min_size=8,
                  max_size=8),
    interval=st.sampled_from([0.1, 0.25, 1.0]),
)
def test_consecutive_fetch_starts_are_spaced(kit, ciks, gaps, interval):
    # oracle: requirement 10 / SEC fair access — over distinct CIKs requested at arbitrary
    # times, each fetch starts at least min_interval_s after the previous one; every CIK
    # is fetched exactly once
    clock = kit.FakeClock(100.0)
    http = kit.FakeHttp(clock)
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(kit, tmp, http, clock, min_interval_s=interval)
        for cik, gap in zip(ciks, gaps, strict=False):
            clock.t += gap
            assert client.company_facts(str(cik)) == {"cik": 320193}
        assert len(http.calls) == len(ciks)
        assert [c["url"] for c in http.calls] == [f"{BASE}CIK{c:010d}.json" for c in ciks]
        starts = [c["t"] for c in http.calls]
        # tolerance: the fake clock sums float sleeps; 1e-9 absorbs that round-off
        assert all(b - a >= interval - 1e-9 for a, b in zip(starts, starts[1:], strict=False))
        assert all(s >= 0 for s in clock.sleeps)
