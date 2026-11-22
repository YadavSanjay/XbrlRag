"""The SEC companyfacts API client: identity header, on-disk cache, request pacing.

The SEC's fair access policy allows at most 10 requests per second and requires a
contactable ``User-Agent``; each filer's document is fetched once and cached, so a rerun
makes no request at all.

Spec: docs/specs/sec-companyfacts-check.md (requirement 10).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from src.eval.sec_check.base import CompanyFactsSource
from src.logging_config import get_logger

logger = get_logger(__name__)

_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_TIMEOUT_S = 60.0


def _httpx_get(url: str, headers: dict) -> tuple[int, bytes]:
    """``GET url`` with httpx; returns the status and the body."""
    import httpx

    response = httpx.get(url, headers=headers, timeout=_TIMEOUT_S, follow_redirects=True)
    return response.status_code, response.content


class SecCompanyFactsClient(CompanyFactsSource):
    """Fetches ``data.sec.gov`` companyfacts once per filer, caching each document."""

    def __init__(
        self,
        identity: str,
        cache_dir: Path,
        http_get: Callable | None = None,
        min_interval_s: float = 0.1,
        clock: Callable | None = None,
        sleep: Callable | None = None,
    ) -> None:
        """Builds the client; makes no request.

        Args:
            identity: The ``User-Agent`` the SEC requires ("Name email").
            cache_dir: Where each filer's document is cached.
            http_get: ``(url, headers) -> (status, body)``; httpx when None.
            min_interval_s: The least time between the starts of two fetches.
            clock: A monotonic clock; ``time.monotonic`` when None.
            sleep: A sleep function; ``time.sleep`` when None.
        """
        self._identity = identity
        self._cache_dir = Path(cache_dir)
        self._http_get = http_get or _httpx_get
        self._min_interval_s = min_interval_s
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._last_start: float | None = None

    def company_facts(self, cik: str) -> dict:
        """Returns a filer's companyfacts, from the cache when present.

        Args:
            cik: The filer's CIK.

        Returns:
            The parsed companyfacts document.

        Raises:
            RuntimeError: If the SEC answers with a non-200 status.
        """
        cik10 = str(int(str(cik).strip())).zfill(10)
        cache_file = self._cache_dir / f"CIK{cik10}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_bytes())

        url = _URL.format(cik=cik10)
        self._pace()
        logger.info("sec_check: fetching %s", url)
        status, body = self._http_get(url, {"User-Agent": self._identity})
        if status != 200:
            raise RuntimeError(f"SEC companyfacts request failed: GET {url} -> HTTP {status}")
        document = json.loads(body)
        self._write_cache(cache_file, body)
        return document

    def _pace(self) -> None:
        """Sleeps so this fetch starts at least ``min_interval_s`` after the last one."""
        now = self._clock()
        if self._last_start is not None:
            wait = self._last_start + self._min_interval_s - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_start = now

    def _write_cache(self, cache_file: Path, body: bytes) -> None:
        """Writes the body atomically: a temp file in the cache dir, then a replace."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._cache_dir, prefix=cache_file.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            os.replace(tmp, cache_file)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
