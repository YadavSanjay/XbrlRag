"""EDGAR filing indexes for the A' draw: the submissions API and the ticker map.

Reads filing *indexes* only (form, dates, accession, inline-XBRL flag), never financial
data. SEC's fair access policy allows at most 10 requests per second and requires a
contactable ``User-Agent``; every document is cached on disk, so a rerun of a draw makes
no request at all and sees exactly what the first run saw.

Spec: docs/specs/aprime-draw.md (requirement 11).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from src.eval.aprime.base import FilingIndexSource
from src.logging_config import get_logger

logger = get_logger(__name__)

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_TIMEOUT_S = 60.0

# Columns of a submissions page -> the filing-index record keys of requirement 8.
_COLUMNS = {
    "form": "form",
    "filingDate": "filing_date",
    "reportDate": "report_date",
    "accessionNumber": "accession_no",
    "isInlineXBRL": "is_inline_xbrl",
}


def _httpx_get(url: str, headers: dict) -> tuple[int, bytes]:
    """``GET url`` with httpx; returns the status and the body."""
    import httpx

    response = httpx.get(url, headers=headers, timeout=_TIMEOUT_S, follow_redirects=True)
    return response.status_code, response.content


def page_records(page: dict) -> list[dict]:
    """Turns one columnar submissions page into filing-index records.

    Args:
        page: ``filings.recent`` of the main document, or an older page document.

    Returns:
        One record per filing, with ``is_inline_xbrl`` as a bool.
    """
    n = len(page.get("form") or [])
    records = []
    for i in range(n):
        rec = {}
        for src_key, key in _COLUMNS.items():
            column = page.get(src_key) or []
            rec[key] = column[i] if i < len(column) else None
        rec["is_inline_xbrl"] = bool(rec["is_inline_xbrl"])
        records.append(rec)
    return records


class EdgarSubmissionsSource(FilingIndexSource):
    """Every filing-index record of a filer, from ``data.sec.gov/submissions``."""

    def __init__(
        self,
        identity: str,
        cache_dir: Path,
        http_get: Callable | None = None,
        min_interval_s: float = 0.15,
        clock: Callable | None = None,
        sleep: Callable | None = None,
    ) -> None:
        """Builds the source; makes no request.

        Args:
            identity: The ``User-Agent`` SEC requires ("Name email").
            cache_dir: Where each fetched document is cached.
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

    def filings(self, cik: str) -> list[dict]:
        """Returns every filing-index record of a filer, recent and older pages merged.

        Args:
            cik: The filer's CIK.

        Returns:
            Records with the keys in requirement 8 of the spec.

        Raises:
            RuntimeError: If SEC answers with a non-200 status.
        """
        cik10 = str(int(str(cik).strip())).zfill(10)
        main = json.loads(self._fetch(f"CIK{cik10}.json"))
        filings = main.get("filings") or {}
        records = page_records(filings.get("recent") or {})
        for extra in filings.get("files") or []:
            records.extend(page_records(json.loads(self._fetch(extra["name"]))))
        return records

    def company_tickers(self) -> bytes:
        """Returns SEC's ``company_tickers.json`` as fetched (cached like the rest).

        Returns:
            The raw bytes, so the caller can hash exactly what it parsed.

        Raises:
            RuntimeError: If SEC answers with a non-200 status.
        """
        return self._fetch("company_tickers.json", url=_TICKERS_URL)

    def _fetch(self, name: str, url: str | None = None) -> bytes:
        """Returns one document's bytes, from the cache when present."""
        cache_file = self._cache_dir / name
        if cache_file.exists():
            return cache_file.read_bytes()
        url = url or _SUBMISSIONS_URL.format(name=name)
        self._pace()
        logger.info("aprime: fetching %s", url)
        status, body = self._http_get(url, {"User-Agent": self._identity})
        if status != 200:
            raise RuntimeError(f"EDGAR request failed: GET {url} -> HTTP {status}")
        self._write_cache(cache_file, body)
        return body

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
