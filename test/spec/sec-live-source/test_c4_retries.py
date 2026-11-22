"""C4: 429/5xx retried with doubling backoff up to a bound; 404 is a typed not-found.

Oracle: requirement 6 of docs/specs/sec-live-source.md. Attempts and their start times are
read off the fake transport and clock.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError
from src.eval.keys.sec_live import SecFetchError, SecNotFoundError

_BODY = b'{"cik": 900001, "facts": {"dei": {}}}'
_DOC = {"cik": 900001, "facts": {"dei": {}}}


@pytest.mark.criterion("C4")
def test_503_twice_then_success_with_backoff(kit):
    # oracle: requirement 6 — retry k starts >= backoff_s * 2**(k-1) after attempt k:
    # 1.0 s, then 2.0 s
    parts = kit.make(respond=kit.sequence((503, b""), (503, b""), (200, _BODY)))
    assert parts.source.company_facts(kit.CIK) == _DOC
    s = parts.transport.starts
    assert len(s) == 3
    assert s[1] - s[0] >= 1.0 - 1e-9
    assert s[2] - s[1] >= 2.0 - 1e-9


@pytest.mark.criterion("C4")
def test_429_is_retried(kit):
    # oracle: requirement 6 — 429 is retryable
    parts = kit.make(respond=kit.sequence((429, b""), (200, _BODY)))
    assert parts.source.company_facts(kit.CIK) == _DOC
    assert len(parts.transport.calls) == 2


@pytest.mark.criterion("C4")
def test_persistent_500_gives_up_after_bound(kit):
    # oracle: requirement 6 — max_retries 4 (default): 5 attempts, then SecFetchError
    parts = kit.make(respond=kit.always(500))
    with pytest.raises(SecFetchError) as err:
        parts.source.company_facts(kit.CIK)
    assert isinstance(err.value, RuntimeError)
    assert len(parts.transport.calls) == 5


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(("status", "body"), [(403, b"forbidden"), (200, b"<html>not json")])
def test_non_retryable_failures(kit, status, body):
    # oracle: requirement 6 — another non-200, or a 200 that is not JSON: SecFetchError after
    # one attempt, and nothing cached (a second call requests again)
    parts = kit.make(respond=kit.always(status, body))
    with pytest.raises(SecFetchError):
        parts.source.company_facts(kit.CIK)
    assert len(parts.transport.calls) == 1
    with pytest.raises(SecFetchError):
        parts.source.company_facts(kit.CIK)
    assert len(parts.transport.calls) == 2


@pytest.mark.criterion("C4")
def test_404_is_typed_not_found_and_not_cached(kit):
    # oracle: requirement 6 — 404 raises SecNotFoundError (a KeyNotFoundError, a LookupError)
    # after one attempt; it is never cached, so a second call requests again
    parts = kit.make(respond=kit.always(404, b'{"error": "nope"}'))
    with pytest.raises(SecNotFoundError) as err:
        parts.source.frames("us-gaap", "Revenues", "USD", "CY1999")
    assert isinstance(err.value, KeyNotFoundError)
    assert isinstance(err.value, LookupError)
    assert len(parts.transport.calls) == 1
    with pytest.raises(SecNotFoundError):
        parts.source.frames("us-gaap", "Revenues", "USD", "CY1999")
    assert len(parts.transport.calls) == 2


@pytest.mark.criterion("C4")
def test_negative_max_retries_raises(kit):
    # oracle: requirement 6 — max_retries >= 0, else ValueError at construction
    with pytest.raises(ValueError):
        kit.make(respond=kit.offline, max_retries=-1)


@pytest.mark.criterion("C4")
@given(max_retries=st.integers(min_value=0, max_value=5),
       status=st.sampled_from([429, 500, 502, 503, 504, 599]),
       k=st.integers(min_value=0, max_value=7),
       backoff=st.sampled_from([0.0, 0.25, 1.0]))
def test_bounded_retries(kit, max_retries, status, k, backoff):
    # oracle: requirement 6 — k failures then a 200: success with k+1 attempts when
    # k <= max_retries, else SecFetchError after max_retries+1 attempts; every retry waits
    # at least its backoff
    parts = kit.make(respond=kit.sequence(*([(status, b"")] * k), (200, _BODY)),
                     max_retries=max_retries, backoff_s=backoff)
    if k <= max_retries:
        assert parts.source.company_facts(kit.CIK) == _DOC
        assert len(parts.transport.calls) == k + 1
    else:
        with pytest.raises(SecFetchError):
            parts.source.company_facts(kit.CIK)
        assert len(parts.transport.calls) == max_retries + 1
    s = parts.transport.starts
    for i in range(1, len(s)):
        assert s[i] - s[i - 1] >= backoff * 2 ** (i - 1) - 1e-9
