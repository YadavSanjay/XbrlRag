"""The NIST Randomness Beacon 2.0 source for the A' draw.

One request for the pulse of one pre-registered minute. The pulse is public and signed
by NIST, so anyone can fetch the same minute and recompute the draw.

Spec: docs/specs/aprime-draw.md (requirement 11).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable

from src.eval.aprime.base import BeaconSource
from src.logging_config import get_logger

logger = get_logger(__name__)

_PULSE_URL = "https://beacon.nist.gov/beacon/2.0/pulse/time/{ms}"
_TIMEOUT_S = 30.0


def _httpx_get(url: str, headers: dict) -> tuple[int, bytes]:
    """``GET url`` with httpx; returns the status and the body."""
    import httpx

    response = httpx.get(url, headers=headers, timeout=_TIMEOUT_S, follow_redirects=True)
    return response.status_code, response.content


def epoch_ms(timestamp_utc: str) -> int:
    """Milliseconds since the Unix epoch of an ISO 8601 time with an offset.

    Args:
        timestamp_utc: E.g. ``2026-11-02T15:00Z``.

    Returns:
        The epoch milliseconds.

    Raises:
        ValueError: If the time carries no offset.
    """
    value = dt.datetime.fromisoformat(timestamp_utc)
    if value.tzinfo is None:
        raise ValueError(f"{timestamp_utc!r} carries no UTC offset")
    return int(value.timestamp() * 1000)


class NistBeaconSource(BeaconSource):
    """Reads one pulse from ``beacon.nist.gov``."""

    def __init__(self, user_agent: str = "xbrlrag-aprime-draw",
                 http_get: Callable | None = None) -> None:
        """Builds the source; makes no request.

        Args:
            user_agent: The ``User-Agent`` header.
            http_get: ``(url, headers) -> (status, body)``; httpx when None.
        """
        self._user_agent = user_agent
        self._http_get = http_get or _httpx_get

    def pulse(self, timestamp_utc: str) -> dict:
        """Returns the pulse object for one minute.

        Args:
            timestamp_utc: The pulse time, ISO 8601 UTC, on a whole minute.

        Returns:
            The ``pulse`` member of the beacon's JSON.

        Raises:
            RuntimeError: If the beacon answers with a non-200 status or no pulse.
        """
        url = _PULSE_URL.format(ms=epoch_ms(timestamp_utc))
        logger.info("aprime: fetching %s", url)
        status, body = self._http_get(url, {"User-Agent": self._user_agent})
        if status != 200:
            raise RuntimeError(f"beacon request failed: GET {url} -> HTTP {status}")
        document = json.loads(body)
        if not isinstance(document, dict) or not isinstance(document.get("pulse"), dict):
            raise RuntimeError(f"beacon answer from {url} has no pulse object")
        return document["pulse"]
