"""Small, dependency-free retry helper for transient ingestion failures.

Phase 3 / §4.8 (reliability) of
docs/design/XbrlRag_Observability_Tracing.md.

Used by the SEC EDGAR downloader (network calls) and available to any other
ingestion step that wants bounded exponential-backoff retries on *transient*
errors while letting permanent errors (e.g. "no such filing") fail fast.

The Arelle ingestor does NOT use this directly — its transient/permanent split
is keyed on the subprocess *return code*, not an exception — but it follows the
same backoff policy (see ``ArelleXBRLIngestor._run_arelle_with_retry``).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Optional, Tuple, Type

from src.logging_config import get_logger

logger = get_logger(__name__)


def backoff_seconds(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff for a 1-based *attempt* number, capped at *cap*."""
    return min(cap, base * (2 ** (attempt - 1)))


def retry_call(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    base_backoff: float = 1.0,
    max_backoff: float = 30.0,
    retryable: tuple[type[BaseException], ...] = (Exception,),
    non_retryable: tuple[type[BaseException], ...] = (),
    sleep_fn: Callable[[float], None] = time.sleep,
    label: str = "",
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> Any:
    """Call *fn* with bounded exponential-backoff retries on transient errors.

    Retries when *fn* raises an instance of *retryable* (default: any
    ``Exception``) that is NOT an instance of *non_retryable* (checked first, so
    permanent errors fail fast). Re-raises the last error once *max_attempts* is
    reached. ``on_retry(attempt, exc, wait)`` fires before each backoff sleep —
    handy for recording retry counts into run lineage.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except non_retryable:
            raise
        except retryable as exc:
            if attempt >= max_attempts:
                logger.error(
                    "%s failed after %d attempt(s): %s", label or "call", attempt, exc
                )
                raise
            wait = backoff_seconds(attempt, base_backoff, max_backoff)
            logger.warning(
                "%s transient error (attempt %d/%d), retrying in %.1fs: %s",
                label or "call", attempt, max_attempts, wait, exc,
            )
            if on_retry is not None:
                on_retry(attempt, exc, wait)
            sleep_fn(wait)
