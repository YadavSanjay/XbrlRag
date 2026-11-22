"""Spec tests for period-year-length-answers C3: one definition
(docs/specs/period-year-length-answers.md, Requirement 1).

The note and the day counts are period-disclosure's `year_length_disclosure` (locked and
graduated, C3 there), and the note states no number the citation-integrity reader would hold
against the statement.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.numerics.stated_numbers import read_stated_numbers
from src.retriever.xbrl.period_disclosure import year_length_disclosure
from src.retriever.xbrl.year_length_answers import compared_year_lengths

SHARED = ("days_current", "days_prior", "current_period", "prior_period", "note")


def _input(step_id, fy, start: dt.date, days: int) -> dict:
    return {"step_id": step_id, "op": "lookup", "status": "OK", "cik": 7,
            "concept_family": "NetIncome", "fiscal_period": "FY", "ytd": False, "fy": fy,
            "fact_id": 100 + fy, "period_start": start.isoformat(),
            "period_end": (start + dt.timedelta(days=days)).isoformat()}


@pytest.mark.criterion("C3")
def test_aapl_entry_is_period_disclosures_note(kit):
    # oracle: period-disclosure C3 for 14262 against 12096: 364, 371, its note; ISO dates and
    # "<n> days" are exempt in the stated-number reader, "53-week" is a compound word
    cur, pri = kit.lookup_input("s1", 14262, 2024), kit.lookup_input("s2", 12096, 2023)
    (entry,) = compared_year_lengths([cur, pri])
    ref = year_length_disclosure(cur, pri)
    assert ref["differs"] is True
    assert {k: (tuple(entry[k]) if k.endswith("_period") else entry[k]) for k in SHARED} == {
        k: ref[k] for k in SHARED}
    assert "differs" not in entry
    assert read_stated_numbers(entry["note"]) == []


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(s1=st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31)),
       d1=st.integers(min_value=340, max_value=390),
       s2=st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31)),
       d2=st.integers(min_value=340, max_value=390))
def test_every_entry_matches_year_length_disclosure(s1, d1, s2, d2):
    # oracle: Requirement 1, one rule: the entry repeats year_length_disclosure(current, prior)
    cur, pri = _input("c", 2025, s1, d1), _input("p", 2024, s2, d2)
    ref = year_length_disclosure(cur, pri)
    out = compared_year_lengths([cur, pri])
    assert len(out) == (1 if ref["differs"] else 0)
    for entry in out:
        assert {k: (tuple(entry[k]) if k.endswith("_period") else entry[k]) for k in SHARED} == {
            k: ref[k] for k in SHARED}
        assert read_stated_numbers(entry["note"]) == []
