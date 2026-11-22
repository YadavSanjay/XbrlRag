"""C4: sums and medians with a don't-care member (docs/specs/grading-dont-care-and-ties.md,
requirement 3).

The sum case echoes the K1 retailers' interest sum: key 4.727e9 (AMZN + HD in the real case),
WMT's reviewed drop 2.259e9, so the with-reading is 6.986e9. The median case: members 100, 200,
300, 400 (median 250); with a reviewed drop at 500 the median of five is 300.
"""

from __future__ import annotations

import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

M = 1e6
RANKED4 = ["DEVA", "DEVB", "DEVC", "DEVD"]


def _say(kit, value):
    return kit.answer(kit.text_for([], f"The total is {value:,.0f} USD."), values=[value])


@pytest.mark.criterion("C4")
def test_sum_either_reading(kit):
    # oracle: 4.727e9 + 2.259e9 = 6.986e9 (hand sum); 7.155e9 adds a strict member too
    case = kit.agg_case("screen.sum", ["DEVA", "DEVB"], 4727 * M, [kit.drop("DEVW", 2259 * M)])
    assert grade_v2(case, _say(kit, 6986 * M)).grade is Grade.CORRECT
    assert grade_v2(case, _say(kit, 4727 * M)).grade is Grade.CORRECT
    assert grade_v2(case, _say(kit, 7155 * M)).grade is Grade.INCORRECT


@pytest.mark.criterion("C4")
def test_median_needs_inputs_that_reproduce_the_key(kit):
    # oracle: median(100, 200, 300, 400) = 250; median(100, ..., 500) = 300 (hand); without
    # inputs, or with inputs whose median (275) is not the key, only the key reading exists
    excluded = [kit.drop("DEVW", 500 * M)]
    good = kit.agg_case("screen.median", RANKED4, 250 * M, excluded,
                        input_values=[400 * M, 300 * M, 200 * M, 100 * M])
    none = kit.agg_case("screen.median", RANKED4, 250 * M, excluded)
    wrong = kit.agg_case("screen.median", RANKED4, 250 * M, excluded,
                         input_values=[450 * M, 300 * M, 250 * M, 100 * M])
    short = kit.agg_case("screen.median", RANKED4, 250 * M, excluded,
                         input_values=[300 * M, 200 * M])
    assert grade_v2(good, _say(kit, 300 * M)).grade is Grade.CORRECT
    assert grade_v2(good, _say(kit, 250 * M)).grade is Grade.CORRECT
    assert grade_v2(none, _say(kit, 300 * M)).grade is Grade.INCORRECT
    assert grade_v2(wrong, _say(kit, 300 * M)).grade is Grade.INCORRECT
    assert grade_v2(short, _say(kit, 300 * M)).grade is Grade.INCORRECT


@pytest.mark.criterion("C4")
@given(values=st.lists(st.integers(1, 10**4), min_size=2, max_size=7),
       extra=st.lists(st.integers(1, 10**4), min_size=1, max_size=2),
       include=st.lists(st.booleans(), min_size=2, max_size=2),
       median=st.booleans())
def test_every_reading_is_accepted_and_nothing_else(kit, values, extra, include, median):
    # oracle: requirement 3 — the reading values are computed here with statistics.median
    # and sum over the chosen subset; any value off every reading by more than rel_tol fails
    ranked = [f"DR{i}" for i in range(len(values))]
    vals = [v * M for v in values]
    dc = {f"DW{i}": v * M for i, v in enumerate(extra)}
    excluded = [kit.drop(t, v) for t, v in dc.items()]
    chosen = [v for (_, v), keep in zip(dc.items(), include, strict=False) if keep]
    if median:
        key = statistics.median(vals)
        case = kit.agg_case("screen.median", ranked, key, excluded, input_values=vals)
        target = statistics.median(vals + chosen)
        readings = [statistics.median(vals + s) for s in ([], *[[v] for v in dc.values()],
                                                          list(dc.values()))]
    else:
        key = sum(vals)
        case = kit.agg_case("screen.sum", ranked, key, excluded)
        target = key + sum(chosen)
        readings = [key + sum(s) for s in ([], *[[v] for v in dc.values()],
                                           list(dc.values()))]
    assert grade_v2(case, _say(kit, target)).grade is Grade.CORRECT
    off = max(readings) * 2 + 7 * M  # far from every reading
    assert grade_v2(case, _say(kit, off)).grade is Grade.INCORRECT
