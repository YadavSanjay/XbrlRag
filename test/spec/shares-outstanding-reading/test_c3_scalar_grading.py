"""C3: a shares-outstanding lookup accepts the cover reading, marked in the grade record.

Oracle: requirements 7, 9 and 10 of docs/specs/shares-outstanding-reading.md; distances
computed by hand. Figures are Union Pacific FY2025 (spec Notes): outstanding 593,245,884,
cover 593,391,460 (2.45e-4 apart), issued 1,113,161,191.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.system import SystemAnswer

KEY = 593_245_884.0
COVER = 593_391_460.0
ISSUED = 1_113_161_191.0
TOL = 1e-4
READING = {"reading": "cover_page", "value": COVER,
           "concept": "dei:EntityCommonStockSharesOutstanding", "period_end": "2026-01-30",
           "accession": "0000900201-26-000030"}


def _case(with_reading: bool = True) -> EvalCase:
    record = {
        "id": "v2-sss-shares-2025", "question": "Sigma shares outstanding fiscal 2025",
        "suite": "xbrl_v2_dev", "kind": "lookup", "expected_value": KEY, "rel_tol": TOL,
        "ticker": "SSS", "cik": 900201, "fy": 2025,
        "concept": "us-gaap:CommonStockSharesOutstanding", "unit": "shares",
        "origin": "v2:lookup", "tags": ["split:dev", "intent:int-sss", "family:terse",
                                        "template:lk-terse", "key_source:sec_companyfacts",
                                        "key_status:single-source", "shape:lookup"],
    }
    if with_reading:
        record["alt_readings"] = [dict(READING)]
    return EvalCase.from_dict(record)


def _answer(value: float, cited: bool = True) -> SystemAnswer:
    return SystemAnswer(text=f"{value:,.0f} shares", numeric_values=[value],
                        fact_ids=[7001] if cited else [])


def _record(grade):
    return grade.to_dict()


@pytest.mark.criterion("C3")
def test_cover_value_is_correct_and_marked():
    # oracle: requirement 7 — the cover reading is accepted under the ordinary tolerance and
    # the record names it
    grade = grade_case(_case(), _answer(COVER))
    assert grade.grade is Grade.CORRECT
    assert grade.matched_value == COVER
    assert _record(grade).get("matched_readings") == {"value": "cover_page"}


@pytest.mark.criterion("C3")
def test_key_value_is_correct_and_marked_key():
    # oracle: requirement 7 — the key match is marked "key"
    grade = grade_case(_case(), _answer(KEY))
    assert grade.grade is Grade.CORRECT
    assert _record(grade).get("matched_readings") == {"value": "key"}


@pytest.mark.criterion("C3")
def test_issued_minus_treasury_counts_only_when_it_equals_a_reading():
    # oracle: requirement 10 — 1,113,161,191 - 519,915,307 = 593,245,884, the key: CORRECT,
    # "key". A derived 593,600,000 is 5.97e-4 from the key and 3.51e-4 from the cover:
    # INCORRECT, no mark. The cover, given alongside, would still be accepted.
    derived = ISSUED - 519_915_307
    assert derived == KEY
    ok = grade_case(_case(), _answer(derived))
    assert ok.grade is Grade.CORRECT
    assert _record(ok).get("matched_readings") == {"value": "key"}
    wrong = grade_case(_case(), _answer(593_600_000.0))
    assert wrong.grade is Grade.INCORRECT
    assert _record(wrong).get("matched_readings") is None
    assert grade_case(_case(), _answer(COVER)).grade is Grade.CORRECT


@pytest.mark.criterion("C3")
def test_uncited_cover_is_partial_but_value_correct():
    # oracle: requirement 7 — the citation rule is unchanged: no fact_id gives PARTIAL, and
    # the matched value is still recorded
    grade = grade_case(_case(), _answer(COVER, cited=False))
    assert grade.grade is Grade.PARTIAL
    assert grade.value_correct
    assert _record(grade).get("matched_readings") == {"value": "cover_page"}


@pytest.mark.criterion("C3")
def test_readings_round_trip_through_the_case_dict():
    # oracle: requirement 5 — from_dict keeps alt_readings, to_dict writes them back; a case
    # without readings writes none, and targets() still lists only the key
    case = _case()
    assert case.to_dict().get("alt_readings") == [READING]
    assert case.targets() == [KEY]
    assert "alt_readings" not in _case(with_reading=False).to_dict()


@pytest.mark.criterion("C3")
@given(value=st.floats(min_value=5.925e8, max_value=5.94e8))
def test_correct_exactly_within_tolerance_of_key_or_reading(value):
    # oracle: requirement 7 restated — CORRECT iff within 1e-4 of the key or of the cover,
    # each separately; the mark names the one matched (the key first)
    def within(expected):
        return abs(value - expected) / abs(expected) <= TOL

    grade = grade_case(_case(), _answer(value))
    if within(KEY):
        assert grade.grade is Grade.CORRECT
        assert _record(grade).get("matched_readings") == {"value": "key"}
    elif within(COVER):
        assert grade.grade is Grade.CORRECT
        assert _record(grade).get("matched_readings") == {"value": "cover_page"}
    else:
        assert grade.grade is Grade.INCORRECT
        assert _record(grade).get("matched_readings") is None
    assert grade_case(_case(), _answer(COVER)).grade is Grade.CORRECT


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(value=st.floats(min_value=5.925e8, max_value=5.94e8))
def test_removing_readings_never_changes_a_key_match(value):
    # oracle: requirements 7 and 9 — a case without readings grades as before, with no mark;
    # adding the cover reading never changes the grade of an answer that matched the key
    plain = grade_case(_case(with_reading=False), _answer(value))
    marked = grade_case(_case(), _answer(value))
    assert _record(plain).get("matched_readings") is None
    if plain.grade is Grade.CORRECT:
        assert marked.grade is Grade.CORRECT
        assert _record(marked).get("matched_readings") == {"value": "key"}
    assert grade_case(_case(), _answer(COVER)).grade is Grade.CORRECT
