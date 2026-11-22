"""LiveSecSource beyond its spec: transport-error retries, Retry-After, the httpx transport.

The spec (docs/specs/sec-live-source.md) covers status retries, pacing and the cache; these
tests cover the extras: connection resets and timeouts are retried within the same bound,
and a header-aware transport's ``Retry-After`` lengthens a retry's wait (capped). Fakes sit
only at the I/O boundaries: the transport, the clock and an in-memory store. No network.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.eval.keys import sec_live
from src.eval.keys.base import HttpTransport
from src.eval.keys.sec_live import (
    HttpResponse,
    HttpxTransport,
    LiveSecSource,
    ResponseTransport,
    SecFetchError,
    SecTransportError,
)
from src.ingest.sec_edgar.storage import FilingStore

BODY = b'{"cik": 900001, "facts": {}}'
WALL = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
CIK = "900001"


class Clock:
    """Monotonic fake time; ``sleep`` advances it."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.t += seconds


class MemoryStore(FilingStore):
    """In-memory FilingStore."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def write_bytes(self, key: str, data: bytes) -> None:
        self.blobs[key] = bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self.blobs[key]

    def exists(self, key: str) -> bool:
        return key in self.blobs

    def iter_manifest_keys(self, prefix: str = ""):
        return iter(())

    @contextmanager
    def localize_dir(self, prefix: str):
        raise NotImplementedError
        yield


class ScriptedTransport(HttpTransport):
    """Plain transport: each scripted item is ``(status, body)`` or an exception to raise."""

    def __init__(self, clock: Clock, script: list) -> None:
        self.clock = clock
        self.script = list(script)
        self.starts: list[float] = []

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.starts.append(self.clock.now())
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


class HeaderTransport(ResponseTransport):
    """Header-aware transport: each scripted item is an ``HttpResponse``."""

    def __init__(self, clock: Clock, script: list[HttpResponse]) -> None:
        self.clock = clock
        self.script = list(script)
        self.starts: list[float] = []

    def get_response(self, url: str, headers: dict) -> HttpResponse:
        self.starts.append(self.clock.now())
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


def _source(transport: HttpTransport, clock: Clock, **kwargs) -> LiveSecSource:
    return LiveSecSource(MemoryStore(), transport=transport, identity="Unit Test u@example.com",
                         clock=clock.now, sleep=clock.sleep, wall_clock=lambda: WALL, **kwargs)


def _gaps(starts: list[float]) -> list[float]:
    return [starts[i] - starts[i - 1] for i in range(1, len(starts))]


# -- transport-level failures --------------------------------------------------------------


@pytest.mark.parametrize("exc", [ConnectionResetError("reset"), TimeoutError("timed out"),
                                 SecTransportError("ReadTimeout")])
def test_transport_error_then_success_is_retried_with_backoff(exc):
    clock = Clock()
    transport = ScriptedTransport(clock, [exc, (200, BODY)])
    assert _source(transport, clock).company_facts(CIK) == {"cik": 900001, "facts": {}}
    assert len(transport.starts) == 2
    assert _gaps(transport.starts) == pytest.approx([1.0])  # backoff_s * 2**0


def test_persistent_transport_error_gives_up_after_bound_and_chains():
    clock = Clock()
    transport = ScriptedTransport(clock, [ConnectionResetError("reset")])
    with pytest.raises(SecFetchError, match="ConnectionResetError") as err:
        _source(transport, clock, max_retries=2).company_facts(CIK)
    assert len(transport.starts) == 3
    assert isinstance(err.value.__cause__, ConnectionResetError)
    assert _gaps(transport.starts) == pytest.approx([1.0, 2.0])


def test_transport_errors_and_5xx_share_one_retry_budget():
    clock = Clock()
    transport = ScriptedTransport(clock, [TimeoutError(), (503, b""), TimeoutError()])
    with pytest.raises(SecFetchError):
        _source(transport, clock, max_retries=2).company_facts(CIK)
    assert len(transport.starts) == 3


def test_non_transport_exception_is_not_retried():
    clock = Clock()
    transport = ScriptedTransport(clock, [KeyError("bug"), (200, BODY)])
    with pytest.raises(KeyError):
        _source(transport, clock).company_facts(CIK)
    assert len(transport.starts) == 1


def test_failed_fetch_writes_nothing_to_the_cache():
    clock = Clock()
    source = _source(ScriptedTransport(clock, [TimeoutError()]), clock, max_retries=1)
    with pytest.raises(SecFetchError):
        source.company_facts(CIK)
    assert source._store.blobs == {}


# -- Retry-After ---------------------------------------------------------------------------


def _resp(status: int, body: bytes = b"", **headers: str) -> HttpResponse:
    return HttpResponse(status=status, body=body, headers=headers)


def test_retry_after_seconds_lengthens_the_wait():
    clock = Clock()
    transport = HeaderTransport(clock, [_resp(429, **{"retry-after": "5"}), _resp(200, BODY)])
    _source(transport, clock).company_facts(CIK)
    assert _gaps(transport.starts) == pytest.approx([5.0])


def test_retry_after_shorter_than_backoff_keeps_the_backoff():
    clock = Clock()
    transport = HeaderTransport(clock, [_resp(503, **{"retry-after": "0"}), _resp(503),
                                        _resp(200, BODY)])
    _source(transport, clock).company_facts(CIK)
    assert _gaps(transport.starts) == pytest.approx([1.0, 2.0])


def test_retry_after_is_capped():
    clock = Clock()
    transport = HeaderTransport(clock, [_resp(429, **{"retry-after": "3600"}), _resp(200, BODY)])
    _source(transport, clock).company_facts(CIK)
    assert _gaps(transport.starts) == pytest.approx([sec_live._MAX_RETRY_AFTER_S])


def test_retry_after_http_date_is_relative_to_the_wall_clock():
    clock = Clock()
    when = (WALL + timedelta(seconds=7)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    transport = HeaderTransport(clock, [_resp(503, **{"retry-after": when}), _resp(200, BODY)])
    _source(transport, clock).company_facts(CIK)
    assert _gaps(transport.starts) == pytest.approx([7.0])


def test_unparseable_retry_after_is_ignored():
    clock = Clock()
    transport = HeaderTransport(clock, [_resp(503, **{"retry-after": "soon"}),
                                        _resp(200, BODY)])
    _source(transport, clock).company_facts(CIK)
    assert _gaps(transport.starts) == pytest.approx([1.0])


def test_retry_after_does_not_extend_the_attempt_bound():
    clock = Clock()
    transport = HeaderTransport(clock, [_resp(429, **{"retry-after": "2"})])
    with pytest.raises(SecFetchError, match="HTTP 429"):
        _source(transport, clock, max_retries=1).company_facts(CIK)
    assert len(transport.starts) == 2


@pytest.mark.parametrize(("value", "expected"), [
    (None, None), ("12", 12.0), (" 3 ", 3.0), ("-1", None), ("1.5", None), ("", None),
    ("Fri, 02 Jan 2026 03:04:00 GMT", 0.0),  # in the past: no extra wait
])
def test_retry_after_parsing(value, expected):
    assert sec_live._retry_after_s(value, WALL) == expected


# -- the httpx transport (httpx.get is the network boundary) --------------------------------


def test_httpx_transport_returns_status_body_and_lowercased_headers(monkeypatch):
    seen = {}

    def fake_get(url, headers, timeout, follow_redirects):
        seen.update(url=url, headers=headers, timeout=timeout, follow=follow_redirects)
        return httpx.Response(429, content=b"slow down", headers={"Retry-After": "4"})

    monkeypatch.setattr(httpx, "get", fake_get)
    response = HttpxTransport(timeout_s=9.0).get_response("https://x.test/a", {"User-Agent": "u"})
    assert response == HttpResponse(status=429, body=b"slow down",
                                    headers={"retry-after": "4", "content-length": "9"})
    assert seen == {"url": "https://x.test/a", "headers": {"User-Agent": "u"},
                    "timeout": 9.0, "follow": True}
    assert HttpxTransport().get("https://x.test/a", {}) == (429, b"slow down")


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"),
                                 httpx.RemoteProtocolError("reset")])
def test_httpx_transport_maps_transport_failures(monkeypatch, exc):
    def fake_get(*args, **kwargs):
        raise exc

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(SecTransportError) as err:
        HttpxTransport().get_response("https://x.test/a", {})
    assert err.value.__cause__ is exc
    assert isinstance(err.value, ConnectionError)


def test_default_transport_is_httpx(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Env User e@example.com")
    source = LiveSecSource(MemoryStore())
    assert isinstance(source._transport, HttpxTransport)


# -- small guards --------------------------------------------------------------------------


def test_naive_wall_clock_fails_loud():
    clock = Clock()
    source = LiveSecSource(MemoryStore(), transport=ScriptedTransport(clock, [(200, BODY)]),
                           identity="u u@example.com", clock=clock.now, sleep=clock.sleep,
                           wall_clock=lambda: datetime(2026, 1, 2))
    with pytest.raises(ValueError, match="aware"):
        source.company_facts(CIK)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "ABC",
                                 hashlib.sha256(b"x").hexdigest().upper()])
def test_payload_rejects_non_hash_keys(bad):
    clock = Clock()
    with pytest.raises(KeyError):
        _source(ScriptedTransport(clock, [(200, BODY)]), clock).payload(bad)


def test_negative_backoff_raises():
    clock = Clock()
    with pytest.raises(ValueError):
        _source(ScriptedTransport(clock, [(200, BODY)]), clock, backoff_s=-1.0)
