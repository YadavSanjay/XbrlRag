"""C5: a defined key's readings reach lookup and trend cases and are graded as marked readings.

Oracle: requirement 5 of docs/specs/key-definitions.md and requirements 6-9 of
docs/specs/shares-outstanding-reading.md; distances computed by hand. Figures: AMZN FY2025
Debt key 65,648, reading note_total 68,836 (hand-checked, spec Notes).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.system import SystemAnswer

KEY = 65_648e6
NOTE = 68_836e6
TOL = 1e-4  # the v2 cases' rel_tol


def _amzn(kit):
    return kit.debt("AMZN", "2025-12-31", 2025)


def _case(readings: list[dict]) -> EvalCase:
    return EvalCase.from_dict({
        "id": "v2-amzn-debt-2025", "question": "Amazon total debt fiscal 2025",
        "suite": "xbrl_v2_dev", "kind": "lookup", "expected_value": KEY, "rel_tol": TOL,
        "ticker": "AMZN", "cik": 1018724, "fy": 2025, "concept": "s4:debt_total", "unit": "USD",
        "origin": "v2:lookup", "alt_readings": readings,
        "tags": ["split:dev", "intent:int-amzn-debt", "family:terse", "template:lk-terse",
                 "key_source:sec_companyfacts", "key_status:single-source", "shape:lookup"]})


def _answer(value: float) -> SystemAnswer:
    return SystemAnswer(text=f"${value:,.0f}", numeric_values=[value], fact_ids=[7001])


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(("shape", "carries"), [
    ("lookup", True), ("trend", True), ("screen.top", False), ("screen.bottom", False),
    ("peer_relative", False), ("compute.ratio", False), ("compute.yoy_growth", False),
    ("point_in_time", False),
])
def test_only_lookups_and_trends_carry_readings(kit, shape, carries):
    # oracle: requirement 5 — readings on lookups and trend rows only
    from src.eval.keys.key_definitions import case_readings

    got = case_readings(_amzn(kit), shape)
    if carries:
        assert [(r["reading"], r["value"]) for r in got] == [("note_total", pytest.approx(NOTE))]
    else:
        assert got == []
        assert _amzn(kit)["readings"] != []


@pytest.mark.criterion("C5")
def test_note_total_answer_is_correct_and_marked(kit):
    # oracle: shares-outstanding-reading requirement 7 — 68,836 matches the reading, 65,648
    # the key; 66,500 is 1.3e-2 from the key and 3.4e-2 from the reading: INCORRECT
    from src.eval.keys.key_definitions import case_readings

    case = _case(case_readings(_amzn(kit), "lookup"))
    note = grade_case(case, _answer(NOTE))
    assert note.grade is Grade.CORRECT
    assert note.to_dict().get("matched_readings") == {"value": "note_total"}
    key = grade_case(case, _answer(KEY))
    assert key.grade is Grade.CORRECT
    assert key.to_dict().get("matched_readings") == {"value": "key"}
    wrong = grade_case(case, _answer(66_500e6))
    assert wrong.grade is Grade.INCORRECT


@pytest.mark.criterion("C5")
def test_current_presented_reading_grades_for_deferred_revenue(kit):
    # oracle: ORCL 2024-05-31 key 10,546, reading current_presented 9,313 (11.7% apart): an
    # answer of the balance-sheet line 9,313 is CORRECT and marked; 9,800 matches neither
    from src.eval.keys.key_definitions import case_readings

    d = kit.deferred("ORCL", "2024-05-31", 2024)
    case = EvalCase.from_dict({
        "id": "v2-orcl-dr-2024", "question": "Oracle deferred revenue fiscal 2024",
        "suite": "xbrl_v2_dev", "kind": "lookup", "expected_value": 10_546e6, "rel_tol": TOL,
        "ticker": "ORCL", "cik": 1341439, "fy": 2024, "concept": "s4:deferred_revenue_total",
        "unit": "USD", "origin": "v2:lookup", "alt_readings": case_readings(d, "lookup"),
        "tags": ["split:dev", "shape:lookup"]})
    g = grade_case(case, _answer(9_313e6))
    assert g.grade is Grade.CORRECT
    assert g.to_dict().get("matched_readings") == {"value": "current_presented"}
    assert grade_case(case, _answer(9_800e6)).grade is Grade.INCORRECT


@pytest.mark.criterion("C5")
@settings(max_examples=40, deadline=None)
@given(value=st.floats(min_value=6.4e10, max_value=7.0e10))
def test_correct_exactly_within_tolerance_of_key_or_reading(kit, value):
    # oracle: shares-outstanding-reading requirement 7 restated — CORRECT iff within 1e-4 of
    # the key or of the note_total reading, each separately
    from src.eval.keys.key_definitions import case_readings

    case = _case(case_readings(_amzn(kit), "lookup"))
    near = any(abs(value - t) / abs(t) <= TOL for t in (KEY, NOTE))
    assert (grade_case(case, _answer(value)).grade is Grade.CORRECT) is near
