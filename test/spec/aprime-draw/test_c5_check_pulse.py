"""C5: ``check_pulse``.

Oracle: requirement 9 of docs/specs/aprime-draw.md. Pulses are synthetic NIST Beacon 2.0
``pulse`` objects (``kit.pulse``) stamped in NIST's format, e.g.
``2026-11-02T15:00:00.000Z``. Every happy-path assertion is paired with a mismatching pulse
that must raise, so a stub returning None cannot pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.core import check_pulse

STAMP = "2026-11-02T15:00:00.000Z"


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("requested", ["2026-11-02T15:00:00Z", "2026-11-02T15:00:00+00:00"])
def test_matching_pulse_passes_and_mismatch_raises(kit, requested):
    # oracle: requirement 9 — same instant, Z or +00:00, whole minute -> None; the pulse
    # one minute later (15:01) is a different instant -> ValueError
    assert check_pulse(kit.pulse(stamp=STAMP), requested) is None
    with pytest.raises(ValueError):
        check_pulse(kit.pulse(stamp="2026-11-02T15:01:00.000Z"), requested)


@pytest.mark.criterion("C5")
def test_one_minute_mismatch_raises(kit):
    # oracle: requirement 9 — timeStamp 15:00 vs requested 15:01 is not the same instant
    with pytest.raises(ValueError):
        check_pulse(kit.pulse(stamp=STAMP), "2026-11-02T15:01:00Z")


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("ov", [
    "a" * 127,                     # 127 characters
    "a" * 129,                     # 129 characters
    "g" + "a" * 127,               # a non-hex character, 128 long
    "0x" + "a" * 126,              # a prefix is not hex
    "",
])
def test_malformed_output_value_raises(kit, ov):
    # oracle: requirement 9 — outputValue must be exactly 128 hexadecimal characters
    with pytest.raises(ValueError):
        check_pulse(kit.pulse(output_value=ov, stamp=STAMP), "2026-11-02T15:00:00Z")


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("field,value", [
    ("timeStamp", None),           # missing
    ("outputValue", None),         # missing
    ("timeStamp", 1793718000000),  # not a string
    ("outputValue", 12345),        # not a string
])
def test_missing_or_non_string_field_raises(kit, field, value):
    # oracle: requirement 9 — pulse must have a string timeStamp and outputValue
    p = kit.pulse(stamp=STAMP)
    if value is None:
        del p[field]
    else:
        p[field] = value
    with pytest.raises(ValueError):
        check_pulse(p, "2026-11-02T15:00:00Z")


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("stamp,requested", [
    ("2026-11-02T15:00:30.000Z", "2026-11-02T15:00:30Z"),          # seconds, same instant
    ("2026-11-02T15:00:00.500Z", "2026-11-02T15:00:00.500Z"),      # a fraction
    ("2026-11-02T15:00:00.000Z", "2026-11-02T16:00:00+01:00"),     # not UTC (same instant)
    ("2026-11-02T15:00:00.000Z", "2026-11-02T15:00:00"),           # no offset at all
    ("2026-11-02T15:00:00.000Z", "next tuesday"),                  # not ISO 8601
])
def test_request_not_utc_whole_minute_raises(kit, stamp, requested):
    # oracle: requirement 9 — requested_utc must be ISO 8601 UTC (Z or +00:00) with seconds
    # and fractions zero
    with pytest.raises(ValueError):
        check_pulse(kit.pulse(stamp=stamp), requested)


@pytest.mark.criterion("C5")
@given(
    minutes=st.integers(0, 60 * 24 * 366 * 80),
    ov=st.text(alphabet="0123456789abcdefABCDEF", min_size=128, max_size=128),
    plus=st.booleans(),
)
def test_random_whole_minute_pulse_passes_against_same_instant(kit, minutes, ov, plus):
    # oracle: requirement 9 — a NIST-format stamp (YYYY-MM-DDTHH:MM:00.000Z) of a whole
    # minute with a 128-hex outputValue (either case) passes for the same instant; the
    # same pulse checked one minute later raises
    t = datetime(2000, 1, 1) + timedelta(minutes=minutes)
    stamp = t.strftime("%Y-%m-%dT%H:%M") + ":00.000Z"
    suffix = "+00:00" if plus else "Z"
    requested = t.strftime("%Y-%m-%dT%H:%M") + ":00" + suffix
    later = (t + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M") + ":00" + suffix
    assert check_pulse(kit.pulse(output_value=ov, stamp=stamp), requested) is None
    with pytest.raises(ValueError):
        check_pulse(kit.pulse(output_value=ov, stamp=stamp), later)
