"""C1: the trace-cap setting (requirement 4)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import LlmConfigError
from src.llm.request_options import flex_trace_wait_cap_s

CAP = "XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S"


@pytest.mark.criterion("C1")
def test_cap_default_and_values(env_vars):
    # oracle: requirement 4 — default 3600.0; blank takes it; stripped values are read; the
    # process environment is read when environ is None
    assert flex_trace_wait_cap_s({}) == pytest.approx(3600.0, abs=1e-12)  # exact literal
    assert flex_trace_wait_cap_s({CAP: ""}) == pytest.approx(3600.0, abs=1e-12)  # exact
    assert flex_trace_wait_cap_s({CAP: "   "}) == pytest.approx(3600.0, abs=1e-12)  # exact
    assert flex_trace_wait_cap_s({CAP: " 90 "}) == pytest.approx(90.0, abs=1e-12)  # exact
    assert flex_trace_wait_cap_s({CAP: "0.5"}) == pytest.approx(0.5, abs=1e-12)  # exact
    with env_vars(**{CAP: "120"}):
        assert flex_trace_wait_cap_s() == pytest.approx(120.0, abs=1e-12)  # exact literal
        assert flex_trace_wait_cap_s({}) == pytest.approx(3600.0, abs=1e-12)  # mapping wins
    with env_vars(**{CAP: None}):
        assert flex_trace_wait_cap_s() == pytest.approx(3600.0, abs=1e-12)  # exact literal


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("bad", ["0", "-5", "nan", "inf", "-inf", "x", "1e999"])
def test_cap_rejects_bad_values(bad):
    # oracle: requirement 4 — not a number, not finite or not above 0 raises; a good value
    # in the same test reads back
    assert flex_trace_wait_cap_s({CAP: "7"}) == pytest.approx(7.0, abs=1e-12)  # exact
    with pytest.raises(LlmConfigError):
        flex_trace_wait_cap_s({CAP: bad})


@pytest.mark.criterion("C1")
@given(value=st.floats(min_value=1e-3, max_value=1e7, allow_nan=False, allow_infinity=False),
       pad=st.sampled_from(["", " ", "  ", "\t"]))
def test_cap_property_reads_back(value, pad):
    # oracle: requirement 4 — a positive finite number written as text reads back as itself
    assert math.isfinite(value) and value > 0
    got = flex_trace_wait_cap_s({CAP: f"{pad}{value!r}{pad}"})
    assert got == pytest.approx(value, rel=1e-15)  # repr round-trips a float exactly
