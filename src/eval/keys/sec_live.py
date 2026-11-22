# A live SecSource: SEC companyfacts and frames over HTTP, cached content-addressed.
#
# Requests carry the EDGAR identity as User-Agent, are paced to at most 10 per second and
# retried with backoff on 429/5xx. Every payload is stored through the FilingStore seam under
# _sec_api/payloads/{sha256}.json with a per-URL record (URL, fetch time, SHA-256), so a
# suite build reruns offline and a key can name the exact payload it was built from.
#
# Beyond the spec: transport-level failures (connection reset, timeout) are retried within the
# same bound, and a transport that exposes response headers (ResponseTransport, e.g. the httpx
# default) has its Retry-After honoured, capped at _MAX_RETRY_AFTER_S.
#
# Spec: docs/specs/sec-live-source.md.

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypedDict

from src.eval.keys.base import HttpTransport, SecSource
from src.eval.keys.core import (
    AnswerKey,
    KeyNotFoundError,
    _split_concept,
    calendar_year_key,
    fiscal_year_key,
)
from src.ingest.sec_edgar.storage import FilingStore
from src.logging_config import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://data.sec.gov/api/xbrl"
_CACHE_PREFIX = "_sec_api"
_PAYLOAD_KEY = _CACHE_PREFIX + "/payloads/{sha256}.json"
_RECORD_KEY = _CACHE_PREFIX + "/records/{url_hash}.json"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CIK_RE = re.compile(r"[0-9]{1,10}")
_DELTA_SECONDS_RE = re.compile(r"[0-9]+")
_MAX_RPS = 10.0  # SEC fair-access limit.
_TIMEOUT_S = 60.0
# The longest server-requested Retry-After wait honoured; longer requests are clipped to it.
_MAX_RETRY_AFTER_S = 60.0


class SecFetchError(RuntimeError):
    """A request failed for good: retries used up, a non-retryable status, or a bad body."""


class SecNotFoundError(KeyNotFoundError):
    """The SEC answered 404: the filer or frame does not exist (requirement 6)."""


class SecTransportError(ConnectionError):
    """A transport-level failure (connection reset, timeout): retryable, like a 5xx."""


class FetchRecord(TypedDict):
    """Where a payload came from (requirement 7).

    Keys: url (str), sha256 (str; lowercase hex of the body), fetched_at (str;
    ``YYYY-MM-DDTHH:MM:SSZ`` UTC).
    """

    url: str
    sha256: str
    fetched_at: str


class Fetched(TypedDict):
    """A parsed payload with its record (requirement 11).

    Keys: document (dict), record (FetchRecord).
    """

    document: dict
    record: FetchRecord


class SourcedKey(TypedDict):
    """An answer key with the payload it was built from (requirement 12).

    Keys: key (AnswerKey), payload (FetchRecord).
    """

    key: AnswerKey
    payload: FetchRecord


@dataclass(frozen=True)
class HttpResponse:
    """One HTTP response: status, raw body and (case-insensitively keyed) headers.

    Attributes:
        status: The HTTP status code.
        body: The raw response body.
        headers: Response headers, names lower-cased.
    """

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class ResponseTransport(HttpTransport):
    """An ``HttpTransport`` that can also return response headers (for ``Retry-After``)."""

    @abstractmethod
    def get_response(self, url: str, headers: dict) -> HttpResponse:
        """Sends one GET request, without retrying or pacing.

        Args:
            url: The URL to fetch.
            headers: The request headers.

        Returns:
            The response, with header names lower-cased.

        Raises:
            SecTransportError: On a connection or timeout failure.
        """

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        """Sends one GET request and drops the response headers.

        Args:
            url: The URL to fetch.
            headers: The request headers.

        Returns:
            The status code and the raw response body.
        """
        response = self.get_response(url, headers)
        return response.status, response.body


class HttpxTransport(ResponseTransport):
    """The production transport: httpx, following redirects, with a timeout."""

    def __init__(self, timeout_s: float = _TIMEOUT_S) -> None:
        """Builds the transport.

        Args:
            timeout_s: The per-request timeout in seconds.
        """
        self._timeout_s = timeout_s

    def get_response(self, url: str, headers: dict) -> HttpResponse:
        """Sends one GET with httpx; maps transport failures to ``SecTransportError``.

        Args:
            url: The URL to fetch.
            headers: The request headers.

        Returns:
            The response, with header names lower-cased.

        Raises:
            SecTransportError: On a connection, protocol or timeout failure.
        """
        import httpx

        try:
            response = httpx.get(url, headers=headers, timeout=self._timeout_s,
                                 follow_redirects=True)
        except httpx.TransportError as exc:
            raise SecTransportError(f"GET {url}: {type(exc).__name__}: {exc}") from exc
        return HttpResponse(
            status=response.status_code,
            body=response.content,
            headers={k.lower(): v for k, v in response.headers.items()},
        )


def _retry_after_s(value: str | None, now: datetime) -> float | None:
    """Parses a ``Retry-After`` header: delta-seconds or an HTTP-date.

    Args:
        value: The header value, or None when absent.
        now: The current aware UTC time, for an HTTP-date.

    Returns:
        The requested wait in seconds (at least 0), or None when absent or unparseable.
    """
    if value is None:
        return None
    text = value.strip()
    if _DELTA_SECONDS_RE.fullmatch(text):
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def _utc_now() -> datetime:
    """The current aware UTC time."""
    return datetime.now(UTC)


class LiveSecSource(SecSource):
    """Fetches ``data.sec.gov`` companyfacts and frames politely, caching every payload."""

    def __init__(
        self,
        store: FilingStore,
        transport: HttpTransport | None = None,
        identity: str | None = None,
        max_rps: float = 10.0,
        max_retries: int = 4,
        backoff_s: float = 1.0,
        refresh: bool = False,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Builds the source; makes no request.

        Args:
            store: The byte store the cache lives in.
            transport: The HTTP transport; httpx when None.
            identity: The EDGAR ``User-Agent``; ``SEC_EDGAR_IDENTITY`` when None.
            max_rps: The most requests per second, in ``(0, 10]``.
            max_retries: How many times a 429/5xx is retried, at least 0.
            backoff_s: The wait before the first retry; it doubles per retry.
            refresh: Request every URL even when it is cached.
            clock: A monotonic clock; ``time.monotonic`` when None.
            sleep: A sleep function; ``time.sleep`` when None.
            wall_clock: Returns the current aware UTC time; now when None.

        Raises:
            ValueError: If the identity is missing or blank, or a limit is out of range.
        """
        raw_identity = identity if identity is not None else os.environ.get("SEC_EDGAR_IDENTITY")
        if raw_identity is None or not raw_identity.strip():
            raise ValueError(
                "an EDGAR identity is required: pass identity= or set SEC_EDGAR_IDENTITY "
                "('Name email@example.com')")
        if not 0 < max_rps <= _MAX_RPS:
            raise ValueError(f"max_rps must be in (0, {_MAX_RPS}], got {max_rps!r}")
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries!r}")
        if backoff_s < 0:
            raise ValueError(f"backoff_s must be >= 0, got {backoff_s!r}")
        self._store = store
        self._transport = transport if transport is not None else HttpxTransport()
        self._identity = raw_identity.strip()
        self._interval_s = 1.0 / max_rps
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._refresh = refresh
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._wall_clock = wall_clock or _utc_now
        self._last_start: float | None = None

    # -- SecSource ----------------------------------------------------------

    def company_facts(self, cik: str) -> dict:
        """Returns a filer's companyfacts, from the cache unless refreshing.

        Args:
            cik: The filer's CIK, padded or not.

        Returns:
            The parsed companyfacts document.

        Raises:
            ValueError: If the CIK is not 1 to 10 digits.
            SecNotFoundError: If the SEC answers 404.
            SecFetchError: If the request fails for good.
        """
        return self.fetch_company_facts(cik)["document"]

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        """Returns the frames document of one concept, unit and period.

        Args:
            taxonomy: The taxonomy key, e.g. ``us-gaap``.
            tag: The concept's local name, e.g. ``Revenues``.
            unit: The unit, e.g. ``USD``.
            period: The frame period, e.g. ``CY2024`` or ``CY2024Q4I``.

        Returns:
            The parsed frames document.

        Raises:
            SecNotFoundError: If the SEC answers 404.
            SecFetchError: If the request fails for good.
        """
        return self.fetch_frames(taxonomy, tag, unit, period)["document"]

    # -- with provenance ----------------------------------------------------

    def fetch_company_facts(self, cik: str) -> Fetched:
        """As ``company_facts``, also returning the payload's record.

        Args:
            cik: The filer's CIK, padded or not.

        Returns:
            The document and its fetch record.

        Raises:
            ValueError: If the CIK is not 1 to 10 digits.
        """
        text = str(cik).strip()
        if not _CIK_RE.fullmatch(text):
            raise ValueError(f"CIK must be 1 to 10 decimal digits, got {cik!r}")
        cik10 = str(int(text)).zfill(10)
        return self._fetch(f"{_BASE_URL}/companyfacts/CIK{cik10}.json")

    def fetch_frames(self, taxonomy: str, tag: str, unit: str, period: str) -> Fetched:
        """As ``frames``, also returning the payload's record.

        Args:
            taxonomy: The taxonomy key.
            tag: The concept's local name.
            unit: The unit.
            period: The frame period.

        Returns:
            The document and its fetch record.
        """
        return self._fetch(f"{_BASE_URL}/frames/{taxonomy}/{tag}/{unit}/{period}.json")

    def payload(self, sha256: str) -> bytes:
        """Returns the stored payload with this hash.

        Args:
            sha256: The payload's lowercase hex SHA-256.

        Returns:
            The payload's bytes.

        Raises:
            KeyError: If no payload has this hash.
            ValueError: If the stored bytes do not hash to it.
        """
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise KeyError(sha256)
        key = _PAYLOAD_KEY.format(sha256=sha256)
        if not self._store.exists(key):
            raise KeyError(sha256)
        body = self._store.read_bytes(key)
        actual = hashlib.sha256(body).hexdigest()
        if actual != sha256:
            raise ValueError(f"stored payload {key} hashes to {actual}, not {sha256}")
        return body

    # -- internals ----------------------------------------------------------

    def _fetch(self, url: str) -> Fetched:
        """Returns the URL's document and record: from the cache, or fetched and stored."""
        record_key = _RECORD_KEY.format(url_hash=hashlib.sha256(url.encode("utf-8")).hexdigest())
        if not self._refresh and self._store.exists(record_key):
            record = self._read_record(record_key, url)
            body = self.payload(record["sha256"])
            return Fetched(document=json.loads(body), record=record)

        body = self._request(url)
        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecFetchError(f"GET {url} -> HTTP 200 with a body that is not JSON") from exc
        sha256 = hashlib.sha256(body).hexdigest()
        record = FetchRecord(url=url, sha256=sha256, fetched_at=self._fetched_at())
        # Payload first, then the record, so a record never names a missing payload.
        payload_key = _PAYLOAD_KEY.format(sha256=sha256)
        if not self._store.exists(payload_key):
            self._store.write_bytes(payload_key, body)
        self._store.write_bytes(record_key, json.dumps(record, sort_keys=True).encode("utf-8"))
        return Fetched(document=document, record=record)

    def _read_record(self, record_key: str, url: str) -> FetchRecord:
        """Reads and checks one URL's stored record; a malformed record fails loud."""
        raw = json.loads(self._store.read_bytes(record_key))
        if (not isinstance(raw, dict) or raw.get("url") != url
                or not isinstance(raw.get("sha256"), str)
                or not isinstance(raw.get("fetched_at"), str)):
            raise ValueError(f"malformed cache record {record_key} for {url}")
        return FetchRecord(url=raw["url"], sha256=raw["sha256"], fetched_at=raw["fetched_at"])

    def _fetched_at(self) -> str:
        """The wall-clock time as ``YYYY-MM-DDTHH:MM:SSZ`` (UTC)."""
        now = self._wall_clock()
        if now.tzinfo is None:
            raise ValueError("wall_clock must return an aware datetime")
        return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _request(self, url: str) -> bytes:
        """GETs the URL with pacing and bounded retries; returns a 200's body.

        Raises:
            SecNotFoundError: On a 404.
            SecFetchError: On any other non-200, or when the retries are used up.
        """
        headers = {"User-Agent": self._identity}
        last_failure = ""
        retry_after: float | None = None
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0 and self._last_start is not None:
                # Retry `attempt` waits at least backoff_s * 2**(attempt-1) after the
                # previous attempt started (and longer when the server asked for it).
                wait = self._backoff_s * 2 ** (attempt - 1)
                if retry_after is not None:
                    wait = max(wait, min(retry_after, _MAX_RETRY_AFTER_S))
                remaining = self._last_start + wait - self._clock()
                if remaining > 0:
                    self._sleep(remaining)
                logger.warning("sec_live: retry %d/%d of %s after %s", attempt,
                               self._max_retries, url, last_failure)
            self._pace()
            retry_after = None
            try:
                if isinstance(self._transport, ResponseTransport):
                    response = self._transport.get_response(url, headers)
                else:
                    status, body = self._transport.get(url, headers)
                    response = HttpResponse(status=status, body=body)
            except (ConnectionError, TimeoutError) as exc:  # SecTransportError included.
                last_failure = f"{type(exc).__name__}: {exc}"
                last_exc = exc
                continue
            last_exc = None
            if response.status == 200:
                logger.info("sec_live: fetched %s (%d bytes)", url, len(response.body))
                return response.body
            if response.status == 404:
                raise SecNotFoundError(f"GET {url} -> HTTP 404")
            if response.status == 429 or 500 <= response.status <= 599:
                last_failure = f"HTTP {response.status}"
                retry_after = _retry_after_s(response.headers.get("retry-after"),
                                             self._wall_clock())
                continue
            raise SecFetchError(f"GET {url} -> HTTP {response.status}")
        raise SecFetchError(
            f"GET {url} failed after {self._max_retries + 1} attempts; last: {last_failure}"
        ) from last_exc

    def _pace(self) -> None:
        """Sleeps so this request starts at least ``1 / max_rps`` after the previous one."""
        now = self._clock()
        if self._last_start is not None:
            wait = self._last_start + self._interval_s - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_start = now


def fiscal_key_with_payload(source: LiveSecSource, cik: str, concept: str, unit: str,
                            fiscal_year: int) -> SourcedKey:
    """``fiscal_year_key`` over one fetched companyfacts payload, with its record.

    Args:
        source: The live source.
        cik: The filer's CIK.
        concept: ``"<taxonomy>:<tag>"``.
        unit: The unit.
        fiscal_year: The filer's fiscal year.

    Returns:
        The key and the record of the payload it was built from.
    """
    fetched = source.fetch_company_facts(cik)
    key = fiscal_year_key(fetched["document"], concept, unit, fiscal_year)
    return SourcedKey(key=key, payload=fetched["record"])


def calendar_key_with_payload(source: LiveSecSource, cik: str, concept: str, unit: str,
                              calendar_year: int, instant: bool = False) -> SourcedKey:
    """``calendar_year_key`` over one fetched frames payload, with its record.

    Args:
        source: The live source.
        cik: The filer's CIK.
        concept: ``"<taxonomy>:<tag>"``.
        unit: The unit.
        calendar_year: The calendar year.
        instant: Use the year-end instant frame ``CY####Q4I``.

    Returns:
        The key and the record of the payload it was built from.
    """
    taxonomy, tag = _split_concept(concept)
    period = f"CY{calendar_year}Q4I" if instant else f"CY{calendar_year}"
    fetched = source.fetch_frames(taxonomy, tag, unit, period)
    key = calendar_year_key(fetched["document"], cik, unit)
    return SourcedKey(key=key, payload=fetched["record"])
