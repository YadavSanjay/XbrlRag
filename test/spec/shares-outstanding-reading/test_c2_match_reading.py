"""C2: match_reading applies the tolerance to the key and to each reading separately.

Oracle: requirement 6 of docs/specs/shares-outstanding-reading.md, with relative distances
computed by hand (|a - b| / |expected|).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.numeric import match_reading

KEY = 593_245_884.0     # Union Pacific FY2025, outstanding at 2025-12-31
COVER = 593_391_460.0   # its 10-K cover count, 2026-01-30 (2.45e-4 from the key)
TOL = 1e-4              # the v2 default rel_tol
COVER_READING = {"reading": "cover_page", "value": COVER}


def _within(value, expected, tol):
    """The tolerance rule restated: relative distance to the expected value."""
    return abs(value - expected) / abs(expected) <= tol


@pytest.mark.criterion("C2")
def test_key_and_cover_each_match_their_own_reading():
    # oracle: requirement 6 — the key matches the key; the cover is 2.45e-4 from the key,
    # outside 1e-4, and matches its own reading exactly
    assert match_reading(KEY, [KEY], [COVER_READING], TOL) == "key"
    assert match_reading(COVER, [KEY], [COVER_READING], TOL) == "cover_page"


@pytest.mark.criterion("C2")
def test_midpoint_matches_nothing():
    # oracle: hand calculation — 593,318,672 is 72,788 from each, 1.23e-4 relative: outside
    # 1e-4 of both, and the tolerance is never widened; the cover itself still matches
    midpoint = (KEY + COVER) / 2
    assert midpoint == pytest.approx(593_318_672, abs=0.5)  # integer midpoint, exact in float
    assert match_reading(midpoint, [KEY], [COVER_READING], TOL) is None
    assert match_reading(COVER, [KEY], [COVER_READING], TOL) == "cover_page"


@pytest.mark.criterion("C2")
def test_key_wins_when_both_match():
    # oracle: requirement 6 — 1.00004 x the key is within 1e-4 of the key and of a reading
    # 1.00008 x the key; the key is preferred
    value = KEY * 1.00004
    near = {"reading": "cover_page", "value": KEY * 1.00008}
    assert match_reading(value, [KEY], [near], TOL) == "key"
    assert match_reading(KEY * 1.00012, [KEY], [near], TOL) == "cover_page"


@pytest.mark.criterion("C2")
def test_first_matching_reading_in_order_and_none_values_skipped():
    # oracle: requirement 6 — a reading whose value is None never matches; of two matching
    # readings the first in order is named
    readings = [{"reading": "blank", "value": None},
                {"reading": "first", "value": COVER},
                {"reading": "second", "value": COVER * (1 + 5e-5)}]
    assert match_reading(COVER, [KEY], readings, TOL) == "first"
    assert match_reading(KEY, [], readings, TOL) is None


@pytest.mark.criterion("C2")
@given(value=st.floats(min_value=5.9e8, max_value=5.97e8),
       readings=st.lists(st.floats(min_value=5.9e8, max_value=5.97e8), max_size=3))
def test_result_follows_the_separate_tolerances(value, readings):
    # oracle: requirement 6 restated — "key" iff the key is within tolerance; else the first
    # reading within tolerance; else None
    named = [{"reading": f"r{i}", "value": v} for i, v in enumerate(readings)]
    got = match_reading(value, [KEY], named, TOL)
    if _within(value, KEY, TOL):
        expected = "key"
    else:
        expected = next((r["reading"] for r in named if _within(value, r["value"], TOL)), None)
    assert got == expected
    # each reading matches itself unless the key already does
    for r in named:
        own = match_reading(r["value"], [KEY], named, TOL)
        if _within(r["value"], KEY, TOL):
            assert own == "key"
        else:
            assert own is not None and own.startswith("r")
    assert match_reading(KEY, [KEY], named, TOL) == "key"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(value=st.floats(min_value=5.9e8, max_value=5.97e8),
       extra=st.lists(st.floats(min_value=5.9e8, max_value=5.97e8), max_size=3))
def test_appending_readings_never_changes_an_earlier_match(value, extra):
    # oracle: requirement 6 — the scan is in order, so readings appended after the cover
    # cannot change a "key" or "cover_page" result
    base = [COVER_READING]
    longer = base + [{"reading": f"x{i}", "value": v} for i, v in enumerate(extra)]
    for probe in (value, KEY, COVER):
        before = match_reading(probe, [KEY], base, TOL)
        after = match_reading(probe, [KEY], longer, TOL)
        if before is not None:
            assert after == before
    assert match_reading(COVER, [KEY], longer, TOL) == "cover_page"
