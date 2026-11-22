"""Rate limiter for LLM API calls."""
from __future__ import annotations

import time
from collections import deque
from threading import Lock


# TODO: Needs validation, tests, and integration with pipeline module.
class RateLimiter:
    """Token bucket rate limiter for requests and tokens per minute.

    Attributes:
        rpm: Requests per minute limit (None = unlimited)
        tpm: Tokens per minute limit (None = unlimited)
    """

    def __init__(self, rpm: int | None = None, tpm: int | None = None):
        self.rpm = rpm
        self.tpm = tpm
        self._request_times: deque[float] = deque()
        self._token_history: deque[tuple[float, int]] = deque()
        self._lock = Lock()

    def wait_if_needed(self, estimated_tokens: int = 0) -> None:
        """Block if rate limits would be exceeded.

        Args:
            estimated_tokens: Estimated tokens for this request (input + output estimate)
        """
        with self._lock:
            now = time.time()
            cutoff = now - 60.0  # 1 minute window

            # Clean old entries
            while self._request_times and self._request_times[0] < cutoff:
                self._request_times.popleft()
            while self._token_history and self._token_history[0][0] < cutoff:
                self._token_history.popleft()

            # Check RPM
            if self.rpm is not None:
                if len(self._request_times) >= self.rpm:
                    # Wait until oldest request falls out of window
                    sleep_time = 60.0 - (now - self._request_times[0])
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                        now = time.time()
                        # Re-clean after sleep
                        cutoff = now - 60.0
                        while self._request_times and self._request_times[0] < cutoff:
                            self._request_times.popleft()

            # Check TPM
            if self.tpm is not None and estimated_tokens > 0:
                current_tokens = sum(t for _, t in self._token_history)
                if current_tokens + estimated_tokens > self.tpm:
                    # Wait until enough tokens fall out of window
                    sleep_time = 60.0 - (now - self._token_history[0][0]) if self._token_history else 0
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                        now = time.time()
                        cutoff = now - 60.0
                        while self._token_history and self._token_history[0][0] < cutoff:
                            self._token_history.popleft()

            # Record this request
            self._request_times.append(now)
            if estimated_tokens > 0:
                self._token_history.append((now, estimated_tokens))
