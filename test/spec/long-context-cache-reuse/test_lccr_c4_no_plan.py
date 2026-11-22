"""C4: no plan, a case outside the plan, or a failed plan keeps the mark (req. 3).

All arms here are Anthropic. Each test also shows a planned single-use case left unmarked,
so a test cannot pass by the arm ignoring plans altogether.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st


def _answer_all(arm, cases):
    for case in cases:
        assert arm.answer(case).error is None


@pytest.mark.criterion("C4")
def test_without_a_plan_every_filing_is_marked(kit):
    # oracle: requirement 3 — no plan_cases call: a1, b1 marked as today; the same cases
    # on a planned arm are single-use and unmarked
    cases = [kit.make_case("a1", "AAA", 2024), kit.make_case("b1", "BBB", 2024)]
    filers = [("AAA", 2024), ("BBB", 2024)]
    unplanned, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, filers)
    _answer_all(unplanned, cases)
    assert adapter.marked(kit.question_of("a1"))
    assert adapter.marked(kit.question_of("b1"))
    planned, control = kit.make_arm(kit.ANTHROPIC_MODEL, filers)
    planned.plan_cases(cases)
    _answer_all(planned, cases)
    assert not control.marked(kit.question_of("a1"))
    assert not control.marked(kit.question_of("b1"))


@pytest.mark.criterion("C4")
def test_a_case_outside_the_plan_is_marked(kit):
    # oracle: requirement 3 — a1 and b1 are planned once each (unmarked); c1 (another
    # filing) and a2 (a planned filing, but not a planned case) are outside it (marked)
    filers = [("AAA", 2024), ("BBB", 2024), ("CCC", 2024)]
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, filers)
    a1, b1 = kit.make_case("a1", "AAA", 2024), kit.make_case("b1", "BBB", 2024)
    c1, a2 = kit.make_case("c1", "CCC", 2024), kit.make_case("a2", "AAA", 2024)
    arm.plan_cases([a1, b1])
    _answer_all(arm, [a1, b1, c1, a2])
    assert not adapter.marked(kit.question_of("a1"))
    assert not adapter.marked(kit.question_of("b1"))
    assert adapter.marked(kit.question_of("c1"))
    assert adapter.marked(kit.question_of("a2"))


@pytest.mark.criterion("C4")
def test_a_failed_plan_leaves_no_partial_plan(kit):
    # oracle: requirement 3 — the resolver raises for FFF while planning; plan_cases
    # raises, and a1 (single-use in the failed plan) is answered as if never planned
    filers = [("AAA", 2024), ("BBB", 2024)]
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, filers, fail=["FFF"])
    a1, b1 = kit.make_case("a1", "AAA", 2024), kit.make_case("b1", "BBB", 2024)
    with pytest.raises(ConnectionError):
        arm.plan_cases([a1, b1, kit.make_case("f1", "FFF", 2024)])
    _answer_all(arm, [a1, b1])
    assert adapter.marked(kit.question_of("a1"))
    assert adapter.marked(kit.question_of("b1"))
    control, recorded = kit.make_arm(kit.ANTHROPIC_MODEL, filers, fail=["FFF"])
    control.plan_cases([a1, b1])
    _answer_all(control, [a1, b1])
    assert not recorded.marked(kit.question_of("a1"))


@pytest.mark.criterion("C4")
def test_a_failed_plan_after_a_good_one_drops_the_good_one(kit):
    # oracle: requirement 3 — a failed call leaves no plan, so the earlier plan (where
    # a1 was single-use) no longer applies and a1 is marked
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, [("AAA", 2024)], fail=["FFF"])
    a1 = kit.make_case("a1", "AAA", 2024)
    arm.plan_cases([a1])
    with pytest.raises(ConnectionError):
        arm.plan_cases([a1, kit.make_case("f1", "FFF", 2024)])
    _answer_all(arm, [a1])
    assert adapter.marked(kit.question_of("a1"))


@pytest.mark.criterion("C4")
def test_a_later_plan_replaces_the_earlier_one(kit):
    # oracle: requirement 3 — first plan: a1 twice-filed (reused); second plan: a1 alone
    # (single-use) — the second decides. Then the reverse on a fresh arm.
    a1, a2 = kit.make_case("a1", "AAA", 2024), kit.make_case("a2", "AAA", 2024)
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, [("AAA", 2024)])
    arm.plan_cases([a1, a2])
    arm.plan_cases([a1])
    _answer_all(arm, [a1])
    assert not adapter.marked(kit.question_of("a1"))
    arm2, adapter2 = kit.make_arm(kit.ANTHROPIC_MODEL, [("AAA", 2024)])
    arm2.plan_cases([a1])
    arm2.plan_cases([a1, a2])
    _answer_all(arm2, [a1])
    assert adapter2.marked(kit.question_of("a1"))


@pytest.mark.criterion("C4")
@given(
    counts=st.lists(st.integers(1, 3), min_size=1, max_size=4),
    planned_mask=st.lists(st.booleans(), min_size=12, max_size=12),
)
def test_marked_unless_planned_and_single_use(kit, counts, planned_mask):
    # oracle: requirements 2 and 3 — a case is unmarked exactly when it is in the plan and
    # its filing is planned once; cases left out of the plan are marked
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    rows = [(f"{t}{n}", t) for t, k in zip(tickers, counts, strict=False) for n in range(k)]
    cases = [kit.make_case(i, t, 2024) for i, t in rows]
    planned = [c for c, p in zip(cases, planned_mask, strict=False) if p]
    planned_count: dict[str, int] = {}
    for case in planned:
        planned_count[case.ticker] = planned_count.get(case.ticker, 0) + 1
    planned_ids = {c.id for c in planned}
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, sorted({(t, 2024) for _, t in rows}))
    arm.plan_cases(planned)
    _answer_all(arm, cases)
    for case in cases:
        expect = not (case.id in planned_ids and planned_count[case.ticker] == 1)
        assert adapter.marked(kit.question_of(case.id)) == expect, case.id
