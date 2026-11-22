"""C5: the SEC client: identity, hosts, retries and pacing."""

from __future__ import annotations

import threading

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sec_payloads import ARCHIVES

from src.eval.sec_tools.client import SecApiClient, SecRequestError, SecSnapshot

URL = "https://data.sec.gov/submissions/CIK0000000001.json"
IDENTITY = "Spec Tester spec@example.com"


class ClockedSec:
    """A transport that records the fake clock at each request's start."""

    def __init__(self, time_source) -> None:
        self.time = time_source
        self.starts: list[float] = []
        self._lock = threading.Lock()

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        with self._lock:
            self.starts.append(self.time.clock())
        return 200, b"{}"


@pytest.mark.criterion("C5")
def test_requests_carry_the_identity(make_client):
    # oracle: requirement 9 and the SEC's fair-access policy
    client, _, sec = make_client(identity="Some Analyst a@example.com")
    status, _ = client.get("https://www.sec.gov/files/company_tickers.json")
    assert status == 200
    assert sec.calls[0][1]["User-Agent"] == "Some Analyst a@example.com"


@pytest.mark.criterion("C5")
def test_the_identity_comes_from_the_environment(make_client, monkeypatch):
    # oracle: requirement 9
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Env Analyst e@example.com")
    client, _, sec = make_client(identity=None)
    client.get("https://www.sec.gov/files/company_tickers.json")
    assert sec.calls[0][1]["User-Agent"] == "Env Analyst e@example.com"


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("identity", ["", "   "])
def test_a_blank_identity_is_refused(make_client, identity):
    # oracle: requirement 9 — the SEC blocks anonymous clients
    with pytest.raises(ValueError, match="identity"):
        make_client(identity=identity)


@pytest.mark.criterion("C5")
def test_a_missing_identity_is_refused(make_client, monkeypatch):
    # oracle: requirement 9
    monkeypatch.delenv("SEC_EDGAR_IDENTITY", raising=False)
    with pytest.raises(ValueError, match="identity"):
        make_client(identity=None)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("max_rps", [0, -1, 10.5])
def test_a_rate_outside_the_limit_is_refused(make_client, max_rps):
    # oracle: requirement 9 — the SEC's limit is 10 per second
    with pytest.raises(ValueError):
        make_client(max_rps=max_rps)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/x.json",
        "http://data.sec.gov/submissions/CIK0000000001.json",
        "https://data.sec.gov.example.com/x.json",
        "https://www.sec.gov/cgi-bin/browse-edgar",
        "https://attacker@example.com/data.sec.gov/x",
    ],
)
def test_other_urls_are_refused_without_a_request(make_client, url):
    # oracle: requirement 9's allow list
    client, _, sec = make_client()
    with pytest.raises(ValueError):
        client.get(url)
    assert sec.calls == []


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    "url",
    [
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
        "https://efts.sec.gov/LATEST/search-index?q=x",
        f"{ARCHIVES}/1/000000000124000001/index.json",
        "https://www.sec.gov/files/company_tickers.json",
    ],
)
def test_the_allowed_hosts_are_reached(make_client, url):
    # oracle: requirement 9's allow list
    client, _, sec = make_client()
    status, _ = client.get(url)
    assert status in (200, 404)
    assert sec.urls == [url]


@pytest.mark.criterion("C5")
def test_429_and_5xx_are_retried_with_a_doubling_backoff(make_client, fake_sec):
    # oracle: requirement 9 — two failures, waits of at least 1 s then 2 s
    sec = fake_sec()
    sec.script[URL] = [(429, b""), (503, b""), (200, b"ok")]
    waits: list[float] = []
    client, _, _ = make_client(sec, sleep=waits.append, clock=lambda: 0.0, max_rps=10)
    assert client.get(URL) == (200, b"ok")
    assert len(sec.calls) == 3
    long_waits = [w for w in waits if w >= 1.0]
    # tolerance: a pacing wait may be added to a backoff
    assert long_waits[0] == pytest.approx(1.0, abs=0.2)
    assert long_waits[1] == pytest.approx(2.0, abs=0.2)


@pytest.mark.criterion("C5")
def test_a_404_is_an_answer(make_client):
    # oracle: requirement 9
    client, _, sec = make_client()
    status, _ = client.get("https://data.sec.gov/submissions/CIK0000000404.json")
    assert status == 404
    assert len(sec.calls) == 1


@pytest.mark.criterion("C5")
def test_retries_used_up_raise(make_client, fake_sec):
    # oracle: requirement 9 — one try plus max_retries
    sec = fake_sec()
    sec.script[URL] = [(503, b"")] * 9
    client, _, _ = make_client(sec, max_retries=2, backoff_s=0.0)
    with pytest.raises(SecRequestError):
        client.get(URL)
    assert len(sec.calls) == 3


@pytest.mark.criterion("C5")
def test_another_status_is_not_retried(make_client, fake_sec):
    # oracle: requirement 9
    sec = fake_sec()
    sec.script[URL] = [(403, b""), (200, b"ok")]
    client, _, _ = make_client(sec)
    with pytest.raises(SecRequestError):
        client.get(URL)
    assert len(sec.calls) == 1


class _Time:
    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def clock(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


@pytest.mark.criterion("C5")
@given(max_rps=st.floats(1.0, 10.0), threads=st.integers(2, 6))
def test_starts_are_paced_across_threads(tmp_path_factory, max_rps, threads):
    # oracle: requirement 9 — one limit for the process, whatever the concurrency
    time_source = _Time()
    sec = ClockedSec(time_source)
    snapshot = SecSnapshot(tmp_path_factory.mktemp("paced"), as_of="2024-06-30")
    client = SecApiClient(snapshot, transport=sec, identity=IDENTITY, max_rps=max_rps,
                          clock=time_source.clock, sleep=time_source.sleep)
    barrier = threading.Barrier(threads)

    def worker(index: int) -> None:
        barrier.wait()
        client.get(f"https://data.sec.gov/submissions/CIK000000000{index}.json")

    pool = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    starts = sorted(sec.starts)
    assert len(starts) == threads
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    # tolerance: float rounding of the fake clock's additions
    assert min(gaps) >= 1.0 / max_rps - 1e-9
