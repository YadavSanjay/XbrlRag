"""Spec tests for period-year-length-answers C5: a growth screen's year-length sentence and
its append (docs/specs/period-year-length-answers.md, Requirements 4-5).

Expected sentences are written out by hand from Requirement 4's template.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.numerics.stated_numbers import read_stated_numbers
from src.retriever.xbrl.year_length_answers import (
    append_year_length_disclosures,
    screen_year_length_text,
)

AAPL = {"ticker": "AAPL", "fact_ids": [14262, 12096], "days_current": 364, "days_prior": 371,
        "current_period": ("2023-10-01", "2024-09-28"),
        "prior_period": ("2022-09-25", "2023-09-30"), "note": "(C3 note)"}
QTR = {"ticker": "BRK.B", "fact_ids": [9001, 9002], "days_current": 98, "days_prior": 91,
       "current_period": ("2022-09-25", "2022-12-31"),
       "prior_period": ("2021-09-26", "2021-12-25"), "note": "(C3 note)"}

AAPL_TEXT = (
    "Growth compares fiscal years of different lengths: AAPL 2023-10-01 to 2024-09-28 "
    "(364 days) against 2022-09-25 to 2023-09-30 (371 days), one is a 53-week year. Growth is "
    "on the reported basis, not adjusted for the different number of days."
)
TAIL = ". Growth is on the reported basis, not adjusted for the different number of days."
HEAD = "Growth compares fiscal years of different lengths: "


@pytest.mark.criterion("C5")
def test_aapl_sentence_exactly():
    # oracle: Requirement 4 template, written out by hand
    assert screen_year_length_text([AAPL]) == AAPL_TEXT
    assert read_stated_numbers(AAPL_TEXT) == []


@pytest.mark.criterion("C5")
def test_two_entries_and_a_quarter_part():
    # oracle: Requirement 4: parts in the given order joined by "; "; 98 and 91 are not
    # 370-372, so no 53-week clause
    qtr_part = ("BRK.B 2022-09-25 to 2022-12-31 (98 days) against 2021-09-26 to 2021-12-25 "
                "(91 days)")
    aapl_part = ("AAPL 2023-10-01 to 2024-09-28 (364 days) against 2022-09-25 to 2023-09-30 "
                 "(371 days), one is a 53-week year")
    assert screen_year_length_text([QTR, AAPL]) == HEAD + qtr_part + "; " + aapl_part + TAIL
    assert screen_year_length_text([QTR]) == HEAD + qtr_part + TAIL
    assert screen_year_length_text([]) == ""


_entry = st.builds(
    lambda t, dc, dp: {"ticker": t, "fact_ids": [1, 2], "days_current": dc, "days_prior": dp,
                       "current_period": ("2024-01-01", "2024-12-30"),
                       "prior_period": ("2023-01-01", "2023-12-31"), "note": "n"},
    st.from_regex(r"[A-Z]{1,4}(\.[A-Z])?", fullmatch=True),
    st.integers(60, 400), st.integers(60, 400),
)


@pytest.mark.criterion("C5")
@given(entries=st.lists(_entry, min_size=1, max_size=6))
def test_sentence_shape_and_no_stated_numbers(entries):
    # oracle: Requirement 4: one part per entry in order; "53-week" exactly when a count is
    # 370-372; dates and "<n> days" are exempt in the citation-integrity reader
    text = screen_year_length_text(entries)
    assert text.startswith(HEAD) and text.endswith(TAIL)
    parts = text[len(HEAD):-len(TAIL)].split("; ")
    assert len(parts) == len(entries)
    for part, e in zip(parts, entries, strict=True):
        assert part.startswith(f"{e['ticker']} 2024-01-01 to 2024-12-30 ({e['days_current']} days)"
                               f" against 2023-01-01 to 2023-12-31 ({e['days_prior']} days)")
        long_year = e["days_current"] in (370, 371, 372) or e["days_prior"] in (370, 371, 372)
        assert part.endswith(", one is a 53-week year") is long_year
    assert read_stated_numbers(text) == []


def _screen(entries, op="screen"):
    return {"step_id": "s1", "op": op, "status": "OK", "rows": [], "year_lengths": entries}


@pytest.mark.criterion("C5")
def test_append_once_after_the_summary():
    # oracle: Requirement 5, mirroring append_screen_disclosures
    summary = "AAPL ranked 12th with revenue growth of 2.02%.  "
    steps = [_screen([AAPL])]
    out = append_year_length_disclosures(summary, steps)
    assert out == "AAPL ranked 12th with revenue growth of 2.02%. " + AAPL_TEXT
    assert append_year_length_disclosures(out, steps) == out
    assert append_year_length_disclosures("", steps) == AAPL_TEXT


@pytest.mark.criterion("C5")
def test_append_only_screen_steps_in_step_order():
    # oracle: Requirement 5: compute steps' year_lengths are stated by the synthesizer, not
    # appended; empty or missing year_lengths add nothing; two screens in step order
    qtr_text = screen_year_length_text([QTR])
    steps = [
        _screen([AAPL], op="compute"),
        _screen([QTR]),
        _screen([]),
        {"step_id": "x", "op": "screen", "status": "OK", "rows": []},
        _screen([AAPL]),
        _screen([QTR]),
    ]
    assert append_year_length_disclosures("S.", steps) == "S. " + qtr_text + " " + AAPL_TEXT
    assert append_year_length_disclosures("S.", [_screen([AAPL], op="compute")]) == "S."


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(summary=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=60),
       n=st.integers(1, 3))
def test_append_is_idempotent_and_keeps_the_summary(summary, n):
    # oracle: Requirement 5: the summary (trailing whitespace stripped) is a prefix; appending
    # again adds nothing
    steps = [_screen([AAPL])] * n
    out = append_year_length_disclosures(summary, steps)
    assert out.startswith(summary.rstrip())
    assert AAPL_TEXT in out
    assert out.count(AAPL_TEXT) == max(1, summary.count(AAPL_TEXT))
    assert append_year_length_disclosures(out, steps) == out
