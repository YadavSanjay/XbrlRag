"""C5: a declined aggregate is not matched on a listed row
(docs/specs/grading-dont-care-and-ties.md, requirement 4).

The median text is shortened from the stored dev answer to int-d599aa9e659a-02802349 ("the
median ... is not available"), with fictional members whose middle value is the key.
"""

from __future__ import annotations

import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

M = 1e6
ROWS = [("DEVA", 300 * M), ("DEVB", 200 * M), ("DEVC", 100 * M)]
DECLINE = ("The median was not computed by the pipeline, so the median for the retailers is "
           "not available. The screen ranked DEVA at 300,000,000 USD, DEVB at 200,000,000 USD "
           "and DEVC at 100,000,000 USD.")


def _median_case(kit):
    return kit.agg_case("screen.median", ["DEVA", "DEVB", "DEVC"], 200 * M)


@pytest.mark.criterion("C5")
def test_a_hedged_median_that_lists_rows_is_refused_not_correct(kit):
    # oracle: requirement 4 — 200e6 is DEVB's listed value, so it is not read as the
    # median; no value remains, and the answer declined: REFUSED
    g = grade_v2(_median_case(kit), kit.answer(kit.text_for([], DECLINE), rows=ROWS,
                                               hedged=True))
    assert g.grade is Grade.REFUSED
    assert g.value_correct is False
    assert g.checks["value"] is False


@pytest.mark.criterion("C5")
def test_a_refused_sum_that_lists_rows_is_refused_not_incorrect(kit):
    # oracle: requirement 4 — the sum (600e6) is in no row; every produced value is a listed
    # row value, so none remains: REFUSED instead of a refused-but-INCORRECT silent-wrong
    case = kit.agg_case("screen.sum", ["DEVA", "DEVB", "DEVC"], 600 * M)
    g = grade_v2(case, kit.answer(kit.text_for([], DECLINE.replace("median", "total")),
                                  rows=ROWS, refused=True))
    assert g.grade is Grade.REFUSED
    assert g.silent_wrong is False


@pytest.mark.criterion("C5")
def test_a_stated_aggregate_is_still_credited(kit):
    # oracle: requirement 4 — an answer that neither refuses nor hedges is graded as today,
    # so stating the median 200e6 (DEVB's value) is CORRECT; a hedged answer that states a
    # non-row amount keeps today's hedged grading (HEDGED_MISS, not REFUSED)
    case = _median_case(kit)
    stated = grade_v2(case, kit.answer(
        kit.text_for([], "The median is 200,000,000 USD (DEVB)."), rows=ROWS))
    hedged = grade_v2(case, kit.answer(kit.text_for([], DECLINE + " Roughly 150,000,000."),
                                       values=[300 * M, 200 * M, 100 * M, 150 * M],
                                       rows=ROWS, hedged=True))
    declined = grade_v2(case, kit.answer(kit.text_for([], DECLINE), rows=ROWS, hedged=True))
    assert stated.grade is Grade.CORRECT
    assert hedged.grade is Grade.HEDGED_MISS
    assert declined.grade is Grade.REFUSED


@pytest.mark.criterion("C5")
@given(values=st.lists(st.integers(1, 10**5), min_size=1, max_size=4, unique=True),
       refused=st.booleans())
def test_a_declining_answer_listing_only_rows_is_never_value_correct(kit, values, refused):
    # oracle: requirement 4 — an odd-count median is one member's value; every produced
    # value is a listed row value, so the declining answer is REFUSED whatever the rows
    vals = sorted({v * M for v in values} | {(10**5 + 1) * M, (10**5 + 2) * M}, reverse=True)
    vals = vals[: len(vals) - (1 - len(vals) % 2)]  # an odd count
    rows = [(f"DR{i}", v) for i, v in enumerate(vals)]
    case = kit.agg_case("screen.median", [t for t, _ in rows], statistics.median(vals))
    ans = kit.answer(kit.text_for([], "The median is not available."), rows=rows,
                     refused=refused, hedged=not refused)
    g = grade_v2(case, ans)
    assert g.value_correct is False
    assert g.grade is Grade.REFUSED
