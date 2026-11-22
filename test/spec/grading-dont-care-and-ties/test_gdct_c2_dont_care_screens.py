"""C2: screens with a don't-care member (docs/specs/grading-dont-care-and-ties.md, req. 3).

Ranked by hand (value descending for a top screen): DEVA 500, DEVB 400, DEVC 300, DEVD 200
(million). DEVW is a reviewed drop at 450, so the readings' top 3 are [DEVA, DEVB, DEVC]
(without DEVW) and [DEVA, DEVW, DEVB] (with it).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

M = 1e6
MEMBERS = [("DEVA", 500 * M), ("DEVB", 400 * M), ("DEVC", 300 * M), ("DEVD", 200 * M)]


def _case(kit, excluded=None, **kw):
    return kit.screen_case(MEMBERS, excluded if excluded is not None else
                           [kit.drop("DEVW", 450 * M)], **kw)


@pytest.mark.criterion("C2")
def test_either_reading_is_correct(kit):
    # oracle: requirement 3 — the with-reading's top 3 is DEVA, DEVW, DEVB; the key's is
    # DEVA, DEVB, DEVC, and a don't-care ticker need not be named
    case = _case(kit)
    with_w = grade_v2(case, kit.screen_answer([("DEVA", 500 * M), ("DEVW", 450 * M),
                                               ("DEVB", 400 * M)]))
    without = grade_v2(case, kit.screen_answer([("DEVA", 500 * M), ("DEVB", 400 * M),
                                                ("DEVC", 300 * M)]))
    assert with_w.grade is Grade.CORRECT, with_w.reason
    assert with_w.checks["ranking"] is True
    assert without.grade is Grade.CORRECT, without.reason
    assert without.checks["exclusions"] is True


@pytest.mark.criterion("C2")
def test_a_wrong_place_or_value_for_the_member_is_wrong(kit):
    # oracle: requirement 3 — DEVW first matches no reading's top 1 (DEVA in both); DEVW at
    # 300 disagrees with its reviewed value 450 (one of three rows off: agreement 2/3 < 0.9)
    case = _case(kit)
    first = grade_v2(case, kit.screen_answer([("DEVW", 450 * M), ("DEVA", 500 * M),
                                              ("DEVB", 400 * M)]))
    off = grade_v2(case, kit.screen_answer([("DEVA", 500 * M), ("DEVW", 300 * M),
                                            ("DEVB", 400 * M)]))
    right = grade_v2(case, kit.screen_answer([("DEVA", 500 * M), ("DEVW", 450 * M),
                                              ("DEVB", 400 * M)]))
    assert first.grade is Grade.INCORRECT
    assert off.grade is Grade.INCORRECT
    assert right.grade is Grade.CORRECT


@pytest.mark.criterion("C2")
def test_a_strict_exclusion_stays_strict(kit):
    # oracle: requirement 1 — no_fsds is never don't care, so ranking DEVN in fails (set
    # overlap 2/4), while the reviewed drop DEVW beside it is accepted
    case = _case(kit, [kit.drop("DEVN", 450 * M, "no_fsds"), kit.drop("DEVW", 350 * M)])
    strict = grade_v2(case, kit.screen_answer([("DEVA", 500 * M), ("DEVN", 450 * M),
                                               ("DEVB", 400 * M)], named=["DEVW"]))
    dont_care = grade_v2(case, kit.screen_answer([("DEVA", 500 * M), ("DEVB", 400 * M),
                                                  ("DEVW", 350 * M)], named=["DEVN"]))
    silent = grade_v2(case, kit.screen_answer([("DEVA", 500 * M), ("DEVB", 400 * M),
                                               ("DEVC", 300 * M)], named=["DEVW"]))
    assert strict.grade is Grade.INCORRECT
    assert dont_care.grade is Grade.CORRECT, dont_care.reason
    # a silent drop of the strict DEVN still fails the S3 exclusions check
    assert silent.checks["exclusions"] is False
    assert silent.grade is Grade.PARTIAL


@pytest.mark.criterion("C2")
def test_the_universe_count_may_include_the_member(kit):
    # oracle: requirement 3 — counts {members 6, ranked 4} gain ranked + 1 = 5; the text
    # names no universe and not every member, so only a count can state it
    case = _case(kit, [kit.drop("DEVX", None, "period_not_filed"), kit.drop("DEVW", 450 * M)])
    rows = [("DEVA", 500 * M), ("DEVW", 450 * M), ("DEVB", 400 * M)]
    five = kit.answer("Of the 5 companies ranked, the top 3 are DEVA, DEVW and DEVB. DEVX "
                      "has not filed.", rows=rows)
    seven = kit.answer("Of the 7 companies ranked, the top 3 are DEVA, DEVW and DEVB. DEVX "
                       "has not filed.", rows=rows)
    assert grade_v2(case, five).checks["universe"] is True
    assert grade_v2(case, five).grade is Grade.CORRECT
    assert grade_v2(case, seven).checks["universe"] is False


@pytest.mark.criterion("C2")
def test_bottom_screens_read_both_ways(kit):
    # oracle: requirement 3 with ascending order — bottom 2 of DEVD 200, DEVC 300 ... is
    # [DEVD, DEVC]; with DEVW at 250 it is [DEVD, DEVW]
    members = [("DEVD", 200 * M), ("DEVC", 300 * M), ("DEVB", 400 * M), ("DEVA", 500 * M)]
    case = kit.screen_case(members, [kit.drop("DEVW", 250 * M)], top_n=2,
                           shape="screen.bottom")
    with_w = grade_v2(case, kit.screen_answer([("DEVD", 200 * M), ("DEVW", 250 * M)]))
    without = grade_v2(case, kit.screen_answer([("DEVD", 200 * M), ("DEVC", 300 * M)]))
    assert with_w.grade is Grade.CORRECT, with_w.reason
    assert without.grade is Grade.CORRECT, without.reason


@pytest.mark.criterion("C2")
@given(values=st.lists(st.integers(1, 10**6), min_size=3, max_size=7, unique=True),
       extra=st.lists(st.integers(1, 10**6), min_size=1, max_size=2, unique=True),
       top_n=st.integers(1, 3), include=st.lists(st.booleans(), min_size=2, max_size=2))
def test_any_subset_of_dont_care_members_ranked_in_is_correct(kit, values, extra, top_n,
                                                              include):
    # oracle: requirement 3 — the expected top N of the ranked members plus the chosen
    # subset, sorted here by value descending; values are distinct so no tie arises
    if set(extra) & set(values):
        return
    members = sorted(((f"DR{i}", float(v)) for i, v in enumerate(values)),
                     key=lambda m: -m[1])
    dc = [(f"DW{i}", float(v)) for i, v in enumerate(extra)]
    case = kit.screen_case(members, [kit.drop(t, v) for t, v in dc], top_n=top_n)
    chosen = [m for m, keep in zip(dc, include, strict=False) if keep]
    expected = sorted(members + chosen, key=lambda m: -m[1])[:top_n]
    g = grade_v2(case, kit.screen_answer(expected))
    assert g.grade is Grade.CORRECT, g.reason
