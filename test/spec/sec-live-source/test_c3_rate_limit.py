"""C3: requests start at least 1 / max_rps apart, with no needless sleep.

Oracle: requirement 5 of docs/specs/sec-live-source.md (SEC fair access: at most 10
requests per second). Start times are read off the fake clock.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

_BODY = b'{"cik": 1, "facts": {}}'


def _fetch_distinct(parts, n):
    for i in range(1, n + 1):
        parts.source.company_facts(str(i))


@pytest.mark.criterion("C3")
def test_default_ten_per_second(kit):
    # oracle: requirement 5 — interval 1/10 s; instant transport, so starts 0.0, 0.1, 0.2
    parts = kit.make(respond=kit.always(200, _BODY))
    _fetch_distinct(parts, 3)
    # tolerance: the fake clock adds floats
    assert parts.transport.starts == pytest.approx([0.0, 0.1, 0.2], abs=1e-9)


@pytest.mark.criterion("C3")
def test_two_per_second(kit):
    # oracle: requirement 5 — interval 1/2 s gives starts 0.0, 0.5, 1.0
    parts = kit.make(respond=kit.always(200, _BODY), max_rps=2)
    _fetch_distinct(parts, 3)
    # tolerance: the fake clock adds floats
    assert parts.transport.starts == pytest.approx([0.0, 0.5, 1.0], abs=1e-9)


@pytest.mark.criterion("C3")
def test_slow_transport_never_sleeps(kit):
    # oracle: requirement 5 — each request takes 0.3 s > 0.1 s, so the interval has always
    # passed: no positive sleep, starts 0.0, 0.3, 0.6
    parts = kit.make(respond=kit.always(200, _BODY), latency=0.3)
    _fetch_distinct(parts, 3)
    assert [s for s in parts.clock.sleeps if s > 0] == []
    # tolerance: the fake clock adds floats
    assert parts.transport.starts == pytest.approx([0.0, 0.3, 0.6], abs=1e-9)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("bad", [0, -1.0, 10.5, 100])
def test_out_of_range_max_rps_raises(kit, bad):
    # oracle: requirement 5 — 0 < max_rps <= 10, else ValueError at construction
    with pytest.raises(ValueError):
        kit.make(respond=kit.offline, max_rps=bad)


@pytest.mark.criterion("C3")
@given(max_rps=st.floats(min_value=0.1, max_value=10.0),
       latencies=st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=1, max_size=8))
def test_starts_are_paced_exactly(kit, max_rps, latencies):
    # oracle: requirement 5 — start_0 = 0; start_i = max(start_{i-1} + 1/max_rps, the time
    # the call was made, i.e. start_{i-1} + latency_{i-1}); computed here in closed form
    parts = kit.make(respond=kit.always(200, _BODY), latency=latencies, max_rps=max_rps)
    _fetch_distinct(parts, len(latencies))
    expected = [0.0]
    for lat in latencies[:-1]:
        prev = expected[-1]
        expected.append(max(prev + 1.0 / max_rps, prev + lat))
    # tolerance: the fake clock adds floats
    assert parts.transport.starts == pytest.approx(expected, abs=1e-9)
    s = parts.transport.starts
    gaps = [s[i] - s[i - 1] for i in range(1, len(s))]
    assert all(g >= 1.0 / max_rps - 1e-9 for g in gaps)
