"""Fakes and fixtures for the sec-live-source spec tests.

Spec: docs/specs/sec-live-source.md. Fakes sit only at I/O boundaries: ``FakeTransport`` (the
``HttpTransport`` seam: scripted responses, records each request and its start time),
``FakeClock`` (monotonic time; its ``sleep`` advances it), a fixed wall clock, and
``MemoryStore`` (an in-memory ``FilingStore``). Payloads are the committed synthetic files in
``fixtures/`` (SEC API shapes, values chosen by hand, made-up CIKs). Everything is exposed
through one session-scoped ``kit`` fixture so hypothesis tests can use it. No network, no data
directory, no current date.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.eval.keys.base import HttpTransport
from src.eval.keys.sec_live import LiveSecSource
from src.ingest.sec_edgar.storage import FilingStore

FIXTURES = Path(__file__).parent / "fixtures"

CIK = "0000900001"
ACCN_FY24 = "0000900001-24-000030"
ACCN_Q4I = "0000900001-25-000005"
IDENTITY = "Spec Tester spec@example.com"
WALL = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
WALL_ISO = "2026-01-02T03:04:05Z"
BASE = "https://data.sec.gov/api/xbrl"
CF_URL = f"{BASE}/companyfacts/CIK{CIK}.json"
REV_URL = f"{BASE}/frames/us-gaap/Revenues/USD/CY2024.json"
ASSETS_URL = f"{BASE}/frames/us-gaap/Assets/USD/CY2024Q4I.json"


def fixture_bytes(name: str) -> bytes:
    """The exact bytes of one committed fixture file."""
    return (FIXTURES / name).read_bytes()


def cf_bytes() -> bytes:
    """The companyfacts payload of the fixture filer."""
    return fixture_bytes(f"companyfacts_CIK{CIK}.json")


def rev_bytes() -> bytes:
    """The CY2024 us-gaap Revenues USD frame."""
    return fixture_bytes("frames_us-gaap_Revenues_USD_CY2024.json")


def assets_bytes() -> bytes:
    """The CY2024Q4I us-gaap Assets USD frame."""
    return fixture_bytes("frames_us-gaap_Assets_USD_CY2024Q4I.json")


class FakeClock:
    """A monotonic clock starting at 0.0; ``sleep`` records the wait and advances it."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, f"negative sleep {seconds}"
        self.sleeps.append(seconds)
        self.t += seconds


class FakeTransport(HttpTransport):
    """Answers from ``respond(url) -> (status, body)``; records (url, headers, start time).

    ``latency`` seconds (one value, or one per request in order) pass on the clock during
    each request.
    """

    def __init__(self, clock: FakeClock, respond: Callable[[str], tuple[int, bytes]],
                 latency: float | list[float] = 0.0) -> None:
        self.clock = clock
        self.respond = respond
        self.latency = latency
        self.calls: list[tuple[str, dict, float]] = []

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.calls.append((url, dict(headers), self.clock.now()))
        lat = self.latency
        if isinstance(lat, list):
            lat = lat[len(self.calls) - 1]
        self.clock.t += lat
        return self.respond(url)

    @property
    def urls(self) -> list[str]:
        return [c[0] for c in self.calls]

    @property
    def starts(self) -> list[float]:
        return [c[2] for c in self.calls]


def routes(mapping: dict[str, bytes]) -> Callable[[str], tuple[int, bytes]]:
    """200 with the mapped body for a known URL, 404 otherwise."""
    def respond(url: str) -> tuple[int, bytes]:
        if url in mapping:
            return 200, mapping[url]
        return 404, b'{"error": "not found"}'
    return respond


def sequence(*responses: tuple[int, bytes]) -> Callable[[str], tuple[int, bytes]]:
    """The given responses in order, whatever the URL; the last one repeats."""
    queue = list(responses)

    def respond(url: str) -> tuple[int, bytes]:
        return queue.pop(0) if len(queue) > 1 else queue[0]
    return respond


def always(status: int, body: bytes = b"") -> Callable[[str], tuple[int, bytes]]:
    """The same response for every request."""
    return lambda url: (status, body)


def offline(url: str) -> tuple[int, bytes]:
    """A transport answer that fails the test: no request may be made."""
    raise AssertionError(f"unexpected request: {url}")


class MemoryStore(FilingStore):
    """An in-memory ``FilingStore``: the storage seam without a disk."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def write_bytes(self, key: str, data: bytes) -> None:
        self.blobs[str(key).replace("\\", "/").strip("/")] = bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self.blobs[str(key).replace("\\", "/").strip("/")]

    def exists(self, key: str) -> bool:
        return str(key).replace("\\", "/").strip("/") in self.blobs

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        return iter([k for k in self.blobs if k.endswith("manifest.yaml")])

    @contextmanager
    def localize_dir(self, prefix: str):
        raise NotImplementedError("MemoryStore has no local directories")
        yield


def make(store: FilingStore | None = None, respond: Callable | None = None,
         latency: float | list[float] = 0.0, wall: datetime = WALL,
         **kwargs) -> SimpleNamespace:
    """A source over a fresh fake clock and transport; returns all the parts."""
    clock = FakeClock()
    store = MemoryStore() if store is None else store
    transport = FakeTransport(clock, respond or routes({}), latency)
    kwargs.setdefault("identity", IDENTITY)
    source = LiveSecSource(store, transport=transport, clock=clock.now, sleep=clock.sleep,
                           wall_clock=lambda: wall, **kwargs)
    return SimpleNamespace(source=source, store=store, transport=transport, clock=clock)


def all_routes() -> dict[str, bytes]:
    """The three fixture payloads at their SEC URLs."""
    return {CF_URL: cf_bytes(), REV_URL: rev_bytes(), ASSETS_URL: assets_bytes()}


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The fakes, payloads and constants, as one namespace."""
    return SimpleNamespace(
        make=make, routes=routes, sequence=sequence, always=always, offline=offline,
        all_routes=all_routes, MemoryStore=MemoryStore, FakeClock=FakeClock,
        FakeTransport=FakeTransport, cf_bytes=cf_bytes, rev_bytes=rev_bytes,
        assets_bytes=assets_bytes, CIK=CIK, ACCN_FY24=ACCN_FY24, ACCN_Q4I=ACCN_Q4I,
        IDENTITY=IDENTITY, WALL=WALL, WALL_ISO=WALL_ISO, BASE=BASE, CF_URL=CF_URL,
        REV_URL=REV_URL, ASSETS_URL=ASSETS_URL,
    )
