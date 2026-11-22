"""C3: ``flex_retry_policy`` follows requirement 3 (docs/specs/llm-flex-retry.md)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import LlmConfigError
from src.llm.request_options import flex_retry_policy

A = "XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS"
B = "XBRLRAG_FLEX_RETRY_BASE_S"
M = "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S"


@pytest.mark.criterion("C3")
def test_defaults(env_vars):
    # oracle: requirement 3 — (15, 5.0, 300.0); the waits 5,10,20,40,80,160 then 8 x 300
    # sum by hand to 315 + 2400 = 2715 s
    policy = flex_retry_policy({})
    assert policy == (15, pytest.approx(5.0, abs=1e-12), pytest.approx(300.0, abs=1e-12))
    assert type(policy[0]) is int
    attempts, base, cap = policy
    waits = [min(cap, base * 2 ** (k - 1)) for k in range(1, attempts)]
    assert sum(waits) == pytest.approx(2715.0, abs=1e-9)  # sums of exact binary values
    assert flex_retry_policy({A: "", B: "  ", M: ""}) == (15, 5.0, 300.0)
    with env_vars(**{A: None, B: None, M: None}):
        assert flex_retry_policy() == (15, 5.0, 300.0)


@pytest.mark.criterion("C3")
def test_values_are_read(env_vars):
    # oracle: requirement 3 — stripped values, from the mapping or from os.environ
    assert flex_retry_policy({A: " 20 ", B: "2.5", M: "600"}) == (20, 2.5, 600.0)
    with env_vars(**{A: "4", B: "1", M: "3"}):
        assert flex_retry_policy() == (4, 1.0, 3.0)
        assert flex_retry_policy({}) == (15, 5.0, 300.0)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("env", [
    {A: "0"}, {A: "-1"}, {A: "1.5"}, {A: "x"},
    {B: "0"}, {B: "-1"}, {B: "nan"}, {B: "inf"}, {B: "x"},
    {M: "inf"}, {M: "nan"}, {B: "10", M: "5"},
])
def test_invalid_values_raise(env):
    # oracle: requirement 3 — each of these is refused with LlmConfigError; a valid one beside
    # it is accepted
    with pytest.raises(LlmConfigError):
        flex_retry_policy(env)
    assert flex_retry_policy({A: "1", B: "0.25", M: "0.25"}) == (1, 0.25, 0.25)


@pytest.mark.criterion("C3")
@given(attempts=st.integers(min_value=1, max_value=1000),
       base=st.floats(min_value=0.001, max_value=1000, allow_nan=False),
       extra=st.floats(min_value=0, max_value=10_000, allow_nan=False))
def test_property(attempts, base, extra):
    # oracle: requirement 3 — any valid triple written with repr() reads back as itself
    cap = base + extra
    got = flex_retry_policy({A: str(attempts), B: repr(base), M: repr(cap)})
    assert got == (attempts, base, cap)
