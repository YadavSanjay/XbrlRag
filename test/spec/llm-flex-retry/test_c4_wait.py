"""C4: ``retry_wait_s`` follows requirement 4 (docs/specs/llm-flex-retry.md)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.gateway import retry_wait_s


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("args,expected", [
    ((1, 0.5, 8.0), 0.5), ((2, 0.5, 8.0), 1.0), ((3, 0.5, 8.0), 2.0), ((5, 0.5, 8.0), 8.0),
    ((6, 0.5, 8.0), 8.0),
    ((1, 5.0, 300.0), 5.0), ((6, 5.0, 300.0), 160.0), ((7, 5.0, 300.0), 300.0),
    ((15, 5.0, 300.0), 300.0),
])
def test_backoff(args, expected):
    # oracle: requirement 4 by hand — min(max, base * 2**(attempt-1)); 0.5*2**4 = 8,
    # 5*2**5 = 160, 5*2**6 = 320 > 300
    assert retry_wait_s(*args) == pytest.approx(expected, abs=1e-9)  # exact binary values
    assert retry_wait_s(*args, None) == pytest.approx(expected, abs=1e-9)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("args,expected", [
    ((1, 5.0, 300.0, 42.0), 42.0), ((1, 5.0, 300.0, 900.0), 300.0),
    ((3, 5.0, 300.0, 2.0), 20.0), ((1, 0.5, 8.0, 3.0), 3.0), ((1, 0.5, 8.0, 30.0), 8.0),
])
def test_retry_after(args, expected):
    # oracle: requirement 4 by hand — min(max, max(backoff, retry_after))
    assert retry_wait_s(*args) == pytest.approx(expected, abs=1e-9)  # exact binary values


@pytest.mark.criterion("C4")
@given(attempt=st.integers(min_value=1, max_value=60),
       base=st.floats(min_value=0.01, max_value=100, allow_nan=False),
       extra=st.floats(min_value=0, max_value=1000, allow_nan=False),
       retry_after=st.one_of(st.none(), st.floats(min_value=0.001, max_value=5000,
                                                   allow_nan=False)))
def test_closed_form(attempt, base, extra, retry_after):
    # oracle: requirement 4, the closed form computed here
    cap = base + extra
    backoff = min(cap, base * 2 ** (attempt - 1))
    expected = backoff if retry_after is None else min(cap, max(backoff, retry_after))
    assert retry_wait_s(attempt, base, cap, retry_after) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(base=st.floats(min_value=0.01, max_value=100, allow_nan=False),
       extra=st.floats(min_value=0, max_value=1000, allow_nan=False))
def test_monotone_and_capped(base, extra):
    # oracle: requirement 4 — the wait never decreases with the attempt, never exceeds the cap,
    # and starts at the base
    cap = base + extra
    waits = [retry_wait_s(k, base, cap) for k in range(1, 40)]
    assert waits[0] == pytest.approx(base, rel=1e-12)
    assert all(b >= a for a, b in zip(waits, waits[1:], strict=False))
    assert all(w <= cap for w in waits)
