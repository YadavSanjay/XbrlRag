"""C1: a new grade stores the values the system produced, before any grader filter
(docs/specs/regrade-raw-values.md, requirement 1).

The real case is int-bd7ee7bd1bce-0231b50a (fixture; dev split, public SEC XBRL values).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import extract_stated_numbers
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.system import SystemAnswer

ROWS = [("DEVA", 100.0), ("DEVB", 200.0), ("DEVC", 300.0)]
LISTING = "DEVA had 100, DEVB had 200, DEVC had 300."
MU = 21_244_000_000.0


@pytest.mark.criterion("C1")
def test_the_real_answer_stores_the_values_its_text_states(kit):
    # oracle: requirement 1 by hand — raw_numeric_values is the answer's numeric_values as
    # grade_case received them, which the system (xbrlrag_full) builds with
    # extract_stated_numbers over its text; the text states MU's 21,244,000,000 (fixture)
    received = kit.real_answer()
    stated = [n.value for n in extract_stated_numbers(received.text)]
    produced = grade_case(kit.real_case(), received).produced
    raw = produced.get("raw_numeric_values")
    assert raw == stated
    # rel 1e-12: a parsed amount compared with the literal, no arithmetic
    assert any(v == pytest.approx(MU, rel=1e-12) for v in raw)


@pytest.mark.criterion("C1")
def test_a_declined_listing_keeps_the_row_values_raw_and_drops_them_graded(kit):
    # oracle: requirement 1 and grading-dont-care-and-ties requirement 4 by hand — a hedged
    # answer that only lists the rows 100/200/300 under a median-200 key: every value matches
    # a row, so the graded numeric_values drop all three (200 among them); the raw list keeps
    # what the answer carried, [100, 200, 300]
    case = kit.agg_case("screen.median", ["DEVA", "DEVB", "DEVC"], 200.0)
    g = grade_case(case, kit.answer(LISTING, ROWS, hedged=True))
    assert g.produced.get("raw_numeric_values") == [100.0, 200.0, 300.0]
    assert 200.0 not in g.produced["numeric_values"]
    assert g.grade is Grade.REFUSED


@pytest.mark.criterion("C1")
def test_an_error_grade_stores_raw_values_too(kit):
    # oracle: requirement 1 by hand — the key is present on every grade, ERROR included; an
    # erroring answer carrying 100/200/300 stores them raw
    case = kit.agg_case("screen.median", ["DEVA", "DEVB", "DEVC"], 200.0)
    g = grade_case(case, kit.answer(LISTING, ROWS, error="TimeoutError('read')"))
    assert g.grade is Grade.ERROR
    assert g.produced.get("raw_numeric_values") == [100.0, 200.0, 300.0]


_VALUES = st.lists(st.integers(1, 10**9).map(float), min_size=1, max_size=6)


@pytest.mark.criterion("C1")
@given(values=_VALUES, hedged=st.booleans(), refused=st.booleans(),
       error=st.sampled_from([None, "Boom('x')"]), listed=st.booleans(),
       shape=st.sampled_from(["screen.median", "screen.sum"]))
def test_raw_values_are_a_copy_of_the_received_values(kit, values, hedged, refused, error,
                                                     listed, shape):
    # oracle: requirement 1 — raw_numeric_values equals the received numeric_values (the
    # input list, compared by value) and is a new list, whatever the grade
    rows = [(f"DEV{chr(65 + i)}", v) for i, v in enumerate(values)] if listed else []
    case = kit.agg_case(shape, ["DEVA", "DEVB", "DEVC"], 12345.0)
    received = SystemAnswer(text="t", numeric_values=list(values),
                            rows=[{"ticker": t, "value": v} for t, v in rows], fact_ids=[1],
                            hedged=hedged, refused=refused, error=error)
    raw = grade_case(case, received).produced.get("raw_numeric_values")
    assert raw == values
    assert raw is not received.numeric_values
