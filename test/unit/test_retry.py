"""Unit tests for the ingestion retry helper."""
import pytest

from src.ingest.util.retry import backoff_seconds, retry_call


def test_returns_on_first_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert retry_call(fn, sleep_fn=lambda _s: None) == "ok"
    assert calls["n"] == 1


def test_retries_transient_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("blip")
        return "recovered"

    waits = []
    result = retry_call(
        fn, max_attempts=5, base_backoff=1.0,
        sleep_fn=lambda s: waits.append(s),
        on_retry=lambda a, e, w: None,
    )
    assert result == "recovered"
    assert calls["n"] == 3
    # exponential backoff: 1, 2
    assert waits == [1.0, 2.0]


def test_exhausts_and_reraises():
    def fn():
        raise TimeoutError("still down")

    with pytest.raises(TimeoutError):
        retry_call(fn, max_attempts=3, sleep_fn=lambda _s: None)


def test_non_retryable_fails_fast():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise LookupError("no such filing")

    with pytest.raises(LookupError):
        retry_call(
            fn, max_attempts=5,
            non_retryable=(LookupError,),
            sleep_fn=lambda _s: None,
        )
    assert calls["n"] == 1  # not retried


def test_on_retry_callback_receives_attempts():
    seen = []

    def fn():
        if len(seen) < 2:
            raise OSError("x")
        return "done"

    retry_call(
        fn, max_attempts=5, base_backoff=2.0, max_backoff=10.0,
        sleep_fn=lambda _s: None,
        on_retry=lambda attempt, exc, wait: seen.append((attempt, wait)),
    )
    assert seen == [(1, 2.0), (2, 4.0)]


def test_backoff_is_capped():
    assert backoff_seconds(1, 1.0, 30.0) == 1.0
    assert backoff_seconds(5, 1.0, 30.0) == 16.0
    assert backoff_seconds(10, 1.0, 30.0) == 30.0  # capped
