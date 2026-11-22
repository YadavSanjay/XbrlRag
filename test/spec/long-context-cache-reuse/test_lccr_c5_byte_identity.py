"""C5: the mark is the only difference a plan makes to a request (req. 4).

Metamorphic: the same case is answered by an Anthropic arm planned so that the case's
filing is single-use (unmarked) and by an unplanned arm (marked); everything but the
filing message's ``cache_point`` flag must be the same.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st


def _pair(kit, case, filers):
    planned, planned_adapter = kit.make_arm(kit.ANTHROPIC_MODEL, filers)
    planned.plan_cases([case])
    unplanned, unplanned_adapter = kit.make_arm(kit.ANTHROPIC_MODEL, filers)
    with_plan = planned.answer(case)
    without_plan = unplanned.answer(case)
    return with_plan, without_plan, planned_adapter, unplanned_adapter


def _assert_same_but_the_mark(with_plan, without_plan, a, b):
    assert a.calls == b.calls == 1
    unmarked, marked = a.messages(0), b.messages(0)
    assert marked[1].get("cache_point") is True
    assert not unmarked[1].get("cache_point")
    assert [m.get("role") for m in unmarked] == [m.get("role") for m in marked]
    assert kit_strip(unmarked) == kit_strip(marked)
    for left, right in zip(unmarked, marked, strict=True):
        assert str(left.get("content")).encode("utf-8") == str(right.get("content")).encode(
            "utf-8")
    other_params = {k: v for k, v in a.params[0].items() if k != "messages"}
    assert other_params == {k: v for k, v in b.params[0].items() if k != "messages"}
    assert with_plan.to_dict() == without_plan.to_dict()


def kit_strip(messages):
    return [{k: v for k, v in m.items() if k != "cache_point"} for m in messages]


@pytest.mark.criterion("C5")
def test_unmarked_request_equals_the_marked_one_but_for_the_flag(kit):
    # oracle: metamorphic — the unplanned arm's request (today's) is the reference
    case = kit.make_case("b1", "BBB", 2024)
    with_plan, without_plan, a, b = _pair(kit, case, [("BBB", 2024)])
    assert with_plan.error is None
    _assert_same_but_the_mark(with_plan, without_plan, a, b)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(
    case_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
    ticker=st.sampled_from(["AAA", "BBB", "MSFT", "TSM"]),
    fy=st.integers(2015, 2030),
)
def test_for_any_case_only_the_flag_differs(kit, case_id, ticker, fy):
    # oracle: metamorphic — for any single-use case, planned vs unplanned requests and
    # answers are equal once the cache_point flag is removed
    case = kit.make_case(case_id, ticker, fy)
    with_plan, without_plan, a, b = _pair(kit, case, [(ticker, fy)])
    _assert_same_but_the_mark(with_plan, without_plan, a, b)
