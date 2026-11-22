"""C3: peer ranks with a don't-care member (docs/specs/grading-dont-care-and-ties.md, req. 3).

Ranked by hand: DEVA 500, DEVB 400, DEVS 300, DEVC 200, DEVD 100 (million); the subject DEVS
is 3rd of 5. A reviewed drop DEVW above DEVS (350) makes it 4th of 6 in the with-reading; one
below (250) leaves it 3rd.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

M = 1e6
MEMBERS = [("DEVA", 500 * M), ("DEVB", 400 * M), ("DEVS", 300 * M), ("DEVC", 200 * M),
           ("DEVD", 100 * M)]
ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th", 8: "8th"}


def _grade(kit, w_value, stated):
    case = kit.peer_case(MEMBERS, "DEVS", 3, [kit.drop("DEVW", w_value)])
    text = f"Among the retailers, DEVS is {ORD[stated]}."
    return grade_v2(case, kit.answer(text, values=[]))


@pytest.mark.criterion("C3")
def test_a_member_above_allows_one_lower_rank(kit):
    # oracle: requirement 3 — with DEVW at 350 the with-reading ranks DEVS 4th
    assert _grade(kit, 350 * M, 4).grade is Grade.CORRECT
    assert _grade(kit, 350 * M, 4).checks["rank"] is True
    assert _grade(kit, 350 * M, 3).grade is Grade.CORRECT
    assert _grade(kit, 350 * M, 5).grade is Grade.INCORRECT


@pytest.mark.criterion("C3")
def test_a_member_below_changes_nothing(kit):
    # oracle: requirement 3 — DEVW at 250 ranks below DEVS, so 3rd in both readings; the
    # unnamed don't-care ticker does not fail the exclusions check
    right = _grade(kit, 250 * M, 3)
    assert right.grade is Grade.CORRECT
    assert right.checks["exclusions"] is True
    assert _grade(kit, 250 * M, 4).grade is Grade.INCORRECT


@pytest.mark.criterion("C3")
@given(w=st.integers(1, 700), stated=st.integers(1, 7))
def test_accepted_ranks_are_the_two_readings(kit, w, stated):
    # oracle: requirement 3 — accepted = {3, 3 + [DEVW above DEVS]}; values 100..500 step
    # 100 never equal w * 1e6 + 0.5e6, so no tie arises
    value = w * M + 0.5 * M
    accepted = {3, 3 + (value > 300 * M)}
    g = _grade(kit, value, stated)
    assert g.checks["rank"] is (stated in accepted)
    assert (g.grade is Grade.CORRECT) is (stated in accepted)
