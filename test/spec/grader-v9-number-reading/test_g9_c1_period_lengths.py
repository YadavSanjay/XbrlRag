"""C1: counts of days and weeks are period lengths, never stated values
(docs/specs/grader-v9-number-reading.md, requirements 1 and 3)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import (
    extract_numbers,
    extract_stated_numbers,
    period_length_spans,
)


def _read(text: str) -> list[float]:
    return [n.value for n in extract_stated_numbers(text)]


@pytest.mark.criterion("C1")
def test_the_year_length_note_reads_no_number(kit):
    # oracle: requirement 1 — the note states two ISO date ranges, two day counts and the
    # compound "53-week"; none is an amount
    assert _read(kit.NOTE) == []
    assert extract_numbers(kit.NOTE) == []
    text = "Revenue grew 2.02% to $391.0 billion. " + kit.NOTE
    # tolerance: 391.0 billion scales exactly; 2.02 is the literal
    assert _read(text) == pytest.approx([2.02, 391.0e9], rel=1e-12)


@pytest.mark.criterion("C1")
def test_the_screen_sentence_reads_no_number(kit):
    # oracle: requirement 1 — "(364 days)" in parentheses is a period length, not a negative
    assert _read(kit.SCREEN_PART) == []


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "Fiscal 2024 had 53 weeks.",
    "The company has a 52- or 53-week fiscal year.",
    "Its fiscal year has 52/53 weeks.",
    "The quarter had 13 or 14 weeks.",
    "The three years span 1,092 days.",
    "The note is due in 1 day.",
    "FY2023 (371 days) against FY2022 (364 days).",
])
def test_free_standing_period_lengths_read_nothing(text):
    # oracle: requirement 1 — each is a count of days or weeks
    assert _read(text) == []


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text,expected", [
    ("Days sales outstanding were 45.5 days.", [45.5]),
    ("The plan pays out after 5 months.", [5.0]),
    ("A fee of $5 days later.", [5.0]),
    ("It closed on day 5 of the quarter.", [5.0]),
])
def test_amounts_and_other_counts_still_read(text, expected):
    # oracle: requirement 1 — a decimal, a month count, a currency amount and "day 5" are not
    # period-length phrases; the period length after them is
    # tolerance: literal values, exact
    assert _read(text + " The year had 53 weeks.") == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C1")
def test_period_length_spans_are_the_digit_spans(kit):
    # oracle: requirement 1 — positions found by hand with str.index; "53-week" is a phrase
    # too (a period length joined by "-")
    text = kit.NOTE
    w, a, b = text.index("53-week"), text.index("364"), text.index("371")
    assert period_length_spans(text) == [(w, w + 2), (a, a + 3), (b, b + 3)]
    t2 = "a 52- or 53-week year and 1,092 days"
    assert period_length_spans(t2) == [(2, 4), (9, 11), (26, 31)]
    assert period_length_spans("Revenue was $5 million.") == []


INTS = st.integers(min_value=1, max_value=9999)
UNITS = st.sampled_from(["day", "days", "week", "weeks", "Days", "WEEKS"])
DATES = st.dates().map(lambda d: d.isoformat()).filter(lambda s: len(s) == 10)


@pytest.mark.criterion("C1")
@given(n=INTS, unit=UNITS, d1=DATES, d2=DATES,
       x=st.integers(min_value=1, max_value=999_999))
def test_a_dated_period_length_reads_nothing(n, unit, d1, d2, x):
    # oracle: requirement 1 — "D1 to D2 has N <unit>" states no amount; an amount before it
    # reads exactly as written
    phrase = f"{d1} to {d2} has {n} {unit}."
    assert _read(phrase) == []
    # tolerance: an integer times 1e6, exact in floating point at this size
    assert _read(f"Revenue was ${x} million. {phrase}") == pytest.approx([x * 1e6], rel=1e-12)


PROSE = st.sampled_from([
    "Revenue was $300 million and margin 12.5%.",
    "DEVA reported $1.2 billion; DEVB reported $900 million.",
    "Net income fell 3.4% to $45 million.",
])


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(prose=PROSE, n=INTS, unit=UNITS)
def test_inserting_a_period_length_changes_nothing_else(prose, n, unit):
    # oracle: requirement 3 — the other numbers read exactly as before
    with_phrase = f"{prose} The fiscal year had {n} {unit}."
    assert _read(with_phrase) == _read(prose)
    assert len(period_length_spans(with_phrase)) == 1
