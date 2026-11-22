"""C4: a shares-outstanding trend accepts each year's own cover reading, row by row.

Oracle: requirement 8 of docs/specs/shares-outstanding-reading.md; Union Pacific FY2023-25
year-end counts and covers from the spec's Notes, distances by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.system import SystemAnswer

YEARS = (2023, 2024, 2025)
KEY = {2023: 609_703_814.0, 2024: 604_241_260.0, 2025: 593_245_884.0}
COVER = {2023: 609_777_914.0, 2024: 604_286_378.0, 2025: 593_391_460.0}
COVER_DATE = {2023: "2024-02-02", 2024: "2025-01-31", 2025: "2026-01-30"}
TOL = 1e-4


def _case() -> EvalCase:
    rows = [{"entity": f"FY{y}", "fiscal_year": y, "value": KEY[y], "period_end": f"{y}-12-31",
             "alt_readings": [{"reading": "cover_page", "value": COVER[y],
                               "concept": "dei:EntityCommonStockSharesOutstanding",
                               "period_end": COVER_DATE[y], "accession": f"acc-{y}"}]}
            for y in YEARS]
    return EvalCase.from_dict({
        "id": "v2-sss-shares-trend", "question": "Sigma shares outstanding FY2023-FY2025",
        "suite": "xbrl_v2_dev", "kind": "breakdown", "expected_rows": rows, "rel_tol": TOL,
        "order_sensitive": False, "ticker": "SSS", "fy": 2025, "unit": "shares",
        "origin": "v2:trend", "tags": ["split:dev", "intent:int-sss-t", "family:terse",
                                       "template:trend-conv", "key_source:sec_companyfacts",
                                       "key_status:single-source", "shape:trend"],
    })


def _answer(values) -> SystemAnswer:
    return SystemAnswer(text="share counts", numeric_values=list(values), fact_ids=[7001])


def _marks(grade):
    return grade.to_dict().get("matched_readings")


@pytest.mark.criterion("C4")
def test_three_covers_grade_correct_each_row_marked():
    # oracle: requirement 8 — FY2023 cover is 1.2e-4 and FY2025 cover 2.45e-4 from their keys,
    # outside 1e-4; each matches its own row's reading
    grade = grade_case(_case(), _answer([COVER[y] for y in YEARS]))
    assert grade.grade is Grade.CORRECT
    assert grade.checks == {"FY2023": True, "FY2024": True, "FY2025": True}
    marks = _marks(grade)
    assert marks["FY2023"] == "cover_page" and marks["FY2025"] == "cover_page"
    # FY2024's cover is 7.5e-5 from its key, inside 1e-4: the key is preferred
    assert marks["FY2024"] == "key"


@pytest.mark.criterion("C4")
def test_mixed_readings_grade_correct():
    # oracle: requirement 8 — rows are graded separately: keys for FY2023 and FY2024, the
    # cover for FY2025
    grade = grade_case(_case(), _answer([KEY[2023], KEY[2024], COVER[2025]]))
    assert grade.grade is Grade.CORRECT
    assert _marks(grade) == {"FY2023": "key", "FY2024": "key", "FY2025": "cover_page"}


@pytest.mark.criterion("C4")
def test_a_row_never_takes_another_years_reading():
    # oracle: requirement 8 — FY2023's cover (609,777,914) is about 9e-3 from FY2024's key and
    # from FY2024's cover: FY2024's row stays unmatched
    values = [KEY[2023], COVER[2023], KEY[2025]]
    grade = grade_case(_case(), _answer(values))
    assert grade.grade is Grade.INCORRECT
    assert grade.checks["FY2024"] is False
    assert grade.checks["FY2023"] is True and grade.checks["FY2025"] is True
    assert _marks(grade) == {"FY2023": "key", "FY2025": "key"}


@pytest.mark.criterion("C4")
@given(picks=st.tuples(*[st.sampled_from(["key", "cover", "off"]) for _ in YEARS]))
def test_marks_name_a_reading_within_tolerance(picks):
    # oracle: requirement 8 restated — a row given its key or its cover is matched; an "off"
    # value (2% away) is not; each mark names a reading within tolerance of a produced value
    values = []
    for y, pick in zip(YEARS, picks, strict=True):
        values.append({"key": KEY[y], "cover": COVER[y], "off": KEY[y] * 1.02}[pick])
    grade = grade_case(_case(), _answer(values))
    marks = _marks(grade)
    assert marks is not None  # rows carry readings: the record has marks, {} if none matched
    for y, pick in zip(YEARS, picks, strict=True):
        assert grade.checks[f"FY{y}"] is (pick != "off")
        if pick == "off":
            assert f"FY{y}" not in marks
        else:
            target = KEY[y] if marks[f"FY{y}"] == "key" else COVER[y]
            assert any(abs(v - target) / target <= TOL for v in values)
    assert (grade.grade is Grade.CORRECT) is all(p != "off" for p in picks)
    assert _marks(grade_case(_case(), _answer([COVER[y] for y in YEARS]))) is not None
