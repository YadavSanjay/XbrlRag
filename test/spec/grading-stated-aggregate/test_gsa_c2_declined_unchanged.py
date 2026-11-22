"""C2: the declined-aggregate rule's unchanged parts
(docs/specs/grading-stated-aggregate.md, requirement 2; grading-dont-care-and-ties
requirement 4).

Each declining case also checks that its text has no aggregate sentence (requirement 1), the
reason the declined rule still applies to it.
"""

from __future__ import annotations

import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import aggregate_sentences
from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

ROWS = [("DEVA", 100.0), ("DEVB", 200.0), ("DEVC", 300.0)]
LISTING = "DEVA had 100, DEVB had 200, DEVC had 300."


def _case(kit, shape="screen.median", key=200.0):
    return kit.agg_case(shape, ["DEVA", "DEVB", "DEVC"], key)


@pytest.mark.criterion("C2")
def test_declining_answers_that_list_rows_are_refused(kit):
    # red-exempt: empty no declining text here has an aggregate sentence; grades pin the rule
    # oracle: grading-dont-care-and-ties requirement 4 by hand — every produced value is a
    # listed row value and no sentence states the aggregate, so none remains: REFUSED
    median = _case(kit)
    hedged = grade_v2(median, kit.answer(LISTING, ROWS, hedged=True))
    declined_text = "The median could not be determined; DEVB had 200."
    declined = grade_v2(median, kit.answer(declined_text, ROWS, hedged=True))
    refused_sum = grade_v2(_case(kit, "screen.sum", 600.0),
                           kit.answer(LISTING, ROWS, refused=True))
    assert hedged.grade is Grade.REFUSED and hedged.value_correct is False
    assert declined.grade is Grade.REFUSED and declined.value_correct is False
    assert refused_sum.grade is Grade.REFUSED and refused_sum.silent_wrong is False
    assert aggregate_sentences(LISTING, "median") == []
    assert aggregate_sentences(declined_text, "median") == []
    assert aggregate_sentences(LISTING, "sum") == []


@pytest.mark.criterion("C2")
def test_an_answer_that_neither_refuses_nor_hedges_is_graded_as_before(kit):
    # oracle: grading-dont-care-and-ties requirement 4 by hand — not declining, so no value is
    # removed: the stated 200 is the key, CORRECT; "The median is 200." is the one aggregate
    # sentence (18 characters)
    text = "The median is 200. " + LISTING
    g = grade_v2(_case(kit), kit.answer(text, ROWS))
    assert g.grade is Grade.CORRECT
    assert aggregate_sentences(text, "median") == [(0, 18)]


@pytest.mark.criterion("C2")
@given(values=st.lists(st.integers(1, 9999), min_size=3, max_size=9, unique=True),
       refused=st.booleans(), declining_sentence=st.booleans())
def test_a_declining_answer_listing_only_rows_is_never_value_correct(
        kit, values, refused, declining_sentence):
    # red-exempt: empty rows and a declined median have no aggregate sentence; grades pin it
    # oracle: grading-dont-care-and-ties requirement 4 — an odd-count median is a member's
    # value; every produced value is a listed row and no sentence states the median: REFUSED
    vals = sorted((v * 1e6 for v in values), reverse=True)
    vals = vals[: len(vals) - (1 - len(vals) % 2)]  # an odd count
    tickers = [f"DEV{chr(65 + i)}" for i in range(len(vals))]
    rows = list(zip(tickers, vals, strict=True))
    text = ", ".join(f"{t} had {int(v):,} USD" for t, v in rows) + "."
    if declining_sentence:
        text = "The median could not be computed. " + text
    case = kit.agg_case("screen.median", tickers, statistics.median(vals))
    g = grade_v2(case, kit.answer(text, rows, refused=refused, hedged=not refused))
    assert g.value_correct is False
    assert g.grade is Grade.REFUSED
    assert aggregate_sentences(text, "median") == []
