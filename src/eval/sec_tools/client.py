# Public SEC HTTP for the ``frontier_sec_tools`` arm: identity, pacing and a dated snapshot.
#
# Every response the arm sees comes through a :class:`SecSnapshot`, so a URL read once gives
# the same bytes on every later run, and a run can name the data it saw.
#
# Spec: docs/specs/frontier-sec-tools.md.

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.eval.keys.base import HttpTransport
from src.llm.run_budget import RunHalt
from src.logging_config import get_logger

logger = get_logger(__name__)

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MANIFEST = "manifest.json"
_BODIES = "bodies"
# The SEC's fair-access limit is 10 requests per second.
MAX_RPS = 10.0
_TIMEOUT_S = 30.0
_ANSWERS = frozenset({200, 404})
_REPLACE_ATTEMPTS = 10
# www.sec.gov paths the tools read; data.sec.gov and efts.sec.gov are allowed whole.
_WWW_PREFIX = "/Archives/edgar/data/"
_WWW_TICKERS = "/files/company_tickers.json"


def check_date(value: str, what: str = "as_of") -> str:
    """Returns *value* when it is a real ``YYYY-MM-DD`` date.

    Args:
        value: The date text.
        what: Its name, for the error.

    Returns:
        The same text.

    Raises:
        ValueError: If it is not a valid ``YYYY-MM-DD`` date.
    """
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError(f"{what} must be a YYYY-MM-DD date, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{what} must be a YYYY-MM-DD date, got {value!r}") from exc
    return value


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, data: bytes) -> None:
    """Writes *data* to *path* through a temporary file and a rename."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    # On Windows a reader, indexer or virus scanner can hold the target for a moment, and
    # the rename then fails with "Access is denied"; it is retried briefly before failing.
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


class SecSnapshotMissError(LookupError):
    """A frozen snapshot does not hold the URL; no request was sent."""


class SecRequestError(RuntimeError):
    """A request failed for good: retries used up, or a status that is not retried."""


class SecSnapshot:
    """An append-only, content-addressed record of SEC responses, for one as-of date."""

    def __init__(
        self,
        root: Path,
        *,
        as_of: str,
        frozen: bool = False,
        wall_clock: Callable[[], str] | None = None,
    ) -> None:
        """Opens or creates the snapshot.

        Args:
            root: The snapshot's directory.
            as_of: The date the run treats as today, ``YYYY-MM-DD``.
            frozen: Refuse any URL the snapshot does not hold.
            wall_clock: Returns the time as ``YYYY-MM-DDTHH:MM:SSZ``; now when None.

        Raises:
            ValueError: If the date is malformed, or the directory holds a snapshot of
                another as-of date.
        """
        self.as_of = check_date(as_of)
        self.root = Path(root)
        self.frozen = bool(frozen)
        self._wall_clock = wall_clock or _now_stamp
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        manifest = self.root / _MANIFEST
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            held_as_of = data.get("as_of")
            if held_as_of != self.as_of:
                raise ValueError(
                    f"the snapshot at {self.root} is as of {held_as_of!r}, not {self.as_of!r}; "
                    "a snapshot has one as-of date"
                )
            entries = data.get("entries")
            if not isinstance(entries, dict):
                raise ValueError(f"the snapshot manifest at {manifest} has no entries")
            self._entries = {str(url): dict(entry) for url, entry in entries.items()}
        elif not self.frozen:
            (self.root / _BODIES).mkdir(parents=True, exist_ok=True)
            self._write_manifest()

    def _write_manifest(self) -> None:
        data = {"as_of": self.as_of, "entries": self._entries}
        _atomic_write(
            self.root / _MANIFEST,
            json.dumps(data, indent=1, sort_keys=True).encode("utf-8"),
        )

    def holds(self, url: str) -> bool:
        """Whether the snapshot holds a response for *url*."""
        with self._lock:
            return url in self._entries

    def lookup(self, url: str) -> tuple[int, bytes] | None:
        """The stored response for a URL.

        Args:
            url: The exact URL.

        Returns:
            The status and body, or None when the URL is not held.
        """
        with self._lock:
            entry = self._entries.get(url)
        if entry is None:
            return None
        body = (self.root / _BODIES / str(entry["sha256"])).read_bytes()
        return int(entry["status"]), body

    def store(self, url: str, status: int, body: bytes) -> None:
        """Records a response. A URL already held keeps its first response.

        Args:
            url: The exact URL.
            status: 200 or 404.
            body: The response body.

        Raises:
            SecSnapshotMissError: If the snapshot is frozen and does not hold the URL.
            ValueError: If the status is neither 200 nor 404.
        """
        if status not in _ANSWERS:
            raise ValueError(f"only 200 and 404 are held, not status {status}")
        with self._lock:
            if url in self._entries:
                return
            if self.frozen:
                raise SecSnapshotMissError(f"the frozen snapshot does not hold {url}")
            digest = hashlib.sha256(body).hexdigest()
            bodies = self.root / _BODIES
            bodies.mkdir(parents=True, exist_ok=True)
            target = bodies / digest
            if not target.exists():
                _atomic_write(target, body)
            self._entries[url] = {
                "sha256": digest, "status": int(status), "fetched_at": self._wall_clock(),
            }
            self._write_manifest()

    def provenance(self) -> dict[str, Any]:
        """What a run records about the data it saw.

        Returns:
            ``as_of``, ``snapshot_id``, ``frozen``, ``urls``, ``first_fetched`` and
            ``last_fetched``.
        """
        with self._lock:
            entries = dict(self._entries)
        lines = sorted(f"{url} {e['status']} {e['sha256']}" for url, e in entries.items())
        stamps = sorted(str(e["fetched_at"]) for e in entries.values())
        return {
            "as_of": self.as_of,
            "frozen": self.frozen,
            "urls": len(entries),
            "first_fetched": stamps[0] if stamps else None,
            "last_fetched": stamps[-1] if stamps else None,
            "snapshot_id": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
        }


def check_sec_url(url: str) -> None:
    """Raises unless *url* is an ``https`` URL on an allowed SEC host and path.

    Args:
        url: The URL.

    Raises:
        ValueError: If the URL is not allowed (requirement 9).
    """
    parts = urlsplit(url)
    host = parts.netloc
    if parts.scheme != "https" or parts.username or parts.password or parts.port:
        raise ValueError(f"not an allowed SEC URL: {url!r}")
    if host in ("data.sec.gov", "efts.sec.gov"):
        return
    if host == "www.sec.gov" and (
        parts.path.startswith(_WWW_PREFIX) or (parts.path == _WWW_TICKERS and not parts.query)
    ):
        return
    raise ValueError(f"not an allowed SEC URL: {url!r}")


class HttpxTransport(HttpTransport):
    """The production transport: one httpx GET, following redirects, with a timeout."""

    def __init__(self, timeout_s: float = _TIMEOUT_S) -> None:
        """Builds the transport.

        Args:
            timeout_s: The per-request timeout in seconds.
        """
        self._timeout_s = timeout_s

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        """Sends one GET with httpx.

        Args:
            url: The URL to fetch.
            headers: The request headers.

        Returns:
            The status code and the raw body.
        """
        import httpx

        response = httpx.get(url, headers=headers, timeout=self._timeout_s,
                             follow_redirects=False)
        return response.status_code, response.content


class SecApiClient:
    """GETs public SEC URLs through a snapshot, identified and paced across threads."""

    def __init__(
        self,
        snapshot: SecSnapshot,
        *,
        transport: HttpTransport | None = None,
        identity: str | None = None,
        max_rps: float = 8.0,
        max_retries: int = 3,
        backoff_s: float = 1.0,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """Builds the client; makes no request.

        Args:
            snapshot: Where responses are read from and recorded.
            transport: The HTTP transport; httpx when None.
            identity: The EDGAR ``User-Agent``; ``SEC_EDGAR_IDENTITY`` when None.
            max_rps: The most requests per second, in ``(0, 10]``.
            max_retries: How many times a 429 or 5xx is retried.
            backoff_s: The wait before the first retry; it doubles per retry.
            clock: A monotonic clock; ``time.monotonic`` when None.
            sleep: A sleep function; ``time.sleep`` when None.

        Raises:
            ValueError: If the identity is blank, or a limit is out of range.
        """
        if identity is None:
            identity = os.environ.get("SEC_EDGAR_IDENTITY")
        if identity is None or not identity.strip():
            raise ValueError(
                "an EDGAR identity is required (identity= or SEC_EDGAR_IDENTITY): the SEC "
                "blocks requests without a User-Agent naming the requester"
            )
        if not 0 < float(max_rps) <= MAX_RPS:
            raise ValueError(f"max_rps must be in (0, {MAX_RPS}], got {max_rps}")
        if int(max_retries) < 0 or float(backoff_s) < 0:
            raise ValueError("max_retries and backoff_s must not be negative")
        self.snapshot = snapshot
        self._transport = transport if transport is not None else HttpxTransport()
        self._headers = {"User-Agent": identity.strip(), "Accept-Encoding": "gzip, deflate"}
        self._interval = 1.0 / float(max_rps)
        self._max_retries = int(max_retries)
        self._backoff_s = float(backoff_s)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._pace_lock = threading.Lock()
        self._next_start: float | None = None
        self._count_lock = threading.Lock()
        self.fetched = 0

    def _send(self, url: str) -> tuple[int, bytes]:
        """One paced request. The pacing lock is held through the wait and while the start
        time is claimed, so starts are ``1/max_rps`` apart across threads; the request itself
        runs outside it, so a slow response does not hold back the next start."""
        with self._pace_lock:
            now = self._clock()
            if self._next_start is not None and now < self._next_start:
                self._sleep(self._next_start - now)
                now = self._clock()
            self._next_start = now + self._interval
            with self._count_lock:
                self.fetched += 1
        return self._transport.get(url, dict(self._headers))

    def get(self, url: str) -> tuple[int, bytes]:
        """The response for a URL, from the snapshot when it holds it.

        Args:
            url: A URL on an allowed SEC host.

        Returns:
            The status (200 or 404) and the body.

        Raises:
            ValueError: If the URL is not on an allowed SEC host.
            SecSnapshotMissError: If the snapshot is frozen and does not hold the URL.
            SecRequestError: If the request fails for good.
        """
        check_sec_url(url)
        held = self.snapshot.lookup(url)
        if held is not None:
            return held
        if self.snapshot.frozen:
            raise SecSnapshotMissError(f"the frozen snapshot does not hold {url}")
        attempt = 0
        while True:
            failure: Exception | None = None
            try:
                status, body = self._send(url)
            except RunHalt:
                raise
            except Exception as exc:  # a transport failure is retried like a 5xx
                status, body, failure = -1, b"", exc
            if status in _ANSWERS:
                self.snapshot.store(url, status, body)
                held = self.snapshot.lookup(url)
                return held if held is not None else (status, body)
            retryable = failure is not None or status == 429 or status >= 500
            if not retryable or attempt >= self._max_retries:
                reason = repr(failure) if failure is not None else f"status {status}"
                raise SecRequestError(f"GET {url} failed: {reason}") from failure
            wait = self._backoff_s * (2 ** attempt)
            attempt += 1
            logger.warning("SEC GET %s: %s; retry %d/%d in %.1fs", url,
                           repr(failure) if failure is not None else status,
                           attempt, self._max_retries, wait)
            self._sleep(wait)
