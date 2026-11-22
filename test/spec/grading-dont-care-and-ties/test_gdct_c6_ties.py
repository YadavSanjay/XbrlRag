"""C6: any order within a tie (docs/specs/grading-dont-care-and-ties.md, requirement 5).

The cut case is dev int-722d72d7beb7-000ced2c once COST is keyed (key-fallback-witness):
retailers' FY2025 long-term borrowings raised, AMZN 15,673M, HD 2,161M, then COST and WMT both
0. The key breaks the tie by ticker (COST third); an answer listing WMT third is equally right.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

M = 1e6
TIE = [("AMZN", 15673 * M), ("HD", 2161 * M), ("COST", 0.0), ("WMT", 0.0)]


@pytest.mark.criterion("C6")
def test_either_member_of_a_tie_at_the_cut(kit):
    # oracle: COST and WMT are exactly equal (0 == 0), so the third place is either
    case = kit.screen_case(TIE, top_n=3)
    wmt = grade_v2(case, kit.screen_answer([("AMZN", 15673 * M), ("HD", 2161 * M),
                                            ("WMT", 0.0)]))
    cost = grade_v2(case, kit.screen_answer([("AMZN", 15673 * M), ("HD", 2161 * M),
                                             ("COST", 0.0)]))
    assert wmt.grade is Grade.CORRECT, wmt.reason
    assert wmt.checks["ranking"] is True
    assert cost.grade is Grade.CORRECT


@pytest.mark.criterion("C6")
def test_past_the_cut_needs_aligned_inputs_and_a_real_tie(kit):
    # oracle: requirement 5 — misaligned inputs (WMT's value unknown past the cut) leave only
    # the key's row; a member at 1 (not 0) is not tied, so swapping it in is wrong
    misaligned = kit.screen_case(TIE, top_n=3, input_values=[15673 * M, 2161 * M])
    shifted = kit.screen_case(TIE, top_n=3, input_values=[2161 * M, 15673 * M, 0.0, 0.0])
    untied = kit.screen_case([("AMZN", 15673 * M), ("HD", 2161 * M), ("COST", 1.0),
                              ("WMT", 0.0)], top_n=3)
    aligned = kit.screen_case(TIE, top_n=3)
    wmt = kit.screen_answer([("AMZN", 15673 * M), ("HD", 2161 * M), ("WMT", 0.0)])
    assert grade_v2(misaligned, wmt).grade is Grade.INCORRECT
    assert grade_v2(shifted, wmt).grade is Grade.INCORRECT
    assert grade_v2(untied, wmt).grade is Grade.INCORRECT
    assert grade_v2(aligned, wmt).grade is Grade.CORRECT


@pytest.mark.criterion("C6")
def test_a_tie_for_first_in_either_order(kit):
    # oracle: DEVA and DEVB are both 5e9; the key lists DEVA first by ticker only
    case = kit.screen_case([("DEVA", 5000 * M), ("DEVB", 5000 * M), ("DEVC", 3000 * M)],
                           top_n=2)
    swapped = grade_v2(case, kit.screen_answer([("DEVB", 5000 * M), ("DEVA", 5000 * M)]))
    assert swapped.grade is Grade.CORRECT, swapped.reason


@pytest.mark.criterion("C6")
def test_a_tied_peer_subject_may_state_either_place(kit):
    # oracle: requirement 5 — DEVS ties DEVB at 300 behind DEVA 500, so ranks 2 to 3 are
    # right (the key says 3, by ticker); 1 and 4 are not
    members = [("DEVA", 500 * M), ("DEVB", 300 * M), ("DEVS", 300 * M), ("DEVC", 100 * M)]
    case = kit.peer_case(members, "DEVS", 3)

    def grade(n):
        return grade_v2(case, kit.answer(f"Among the retailers, DEVS is No. {n}.", values=[]))

    assert grade(2).grade is Grade.CORRECT
    assert grade(3).grade is Grade.CORRECT
    assert grade(1).grade is Grade.INCORRECT
    assert grade(4).grade is Grade.INCORRECT


@pytest.mark.criterion("C6")
@given(levels=st.lists(st.tuples(st.integers(1, 10**6), st.integers(1, 3)), min_size=2,
                       max_size=5, unique_by=lambda t: t[0]),
       top_n=st.integers(1, 5), data=st.data())
def test_any_order_within_ties_is_correct(kit, levels, top_n, data):
    # oracle: requirement 5 — the key is built here (value descending, ties by ticker); an
    # answer that keeps the value order but shuffles and re-chooses within each tie group is
    # right, and replacing a top-N member by a strictly lower member is wrong
    members = []
    for value, count in sorted(levels, key=lambda t: -t[0]):
        group = sorted(f"T{value:07d}{c}" for c in "ABC"[:count])
        members += [(t, float(value)) for t in group]
    top_n = min(top_n, len(members) - 1)
    case = kit.screen_case(members, top_n=top_n)
    order = []
    for value, _ in sorted(levels, key=lambda t: -t[0]):
        group = [m for m in members if m[1] == value]
        order += data.draw(st.permutations(group))
    g = grade_v2(case, kit.screen_answer(order[:top_n]))
    assert g.grade is Grade.CORRECT, g.reason
    cut_value = members[top_n - 1][1]
    lower = [m for m in members if m[1] < cut_value]
    if lower and top_n <= 5:
        wrong = order[: top_n - 1] + [lower[-1]]
        assert grade_v2(case, kit.screen_answer(wrong)).grade is Grade.INCORRECT
