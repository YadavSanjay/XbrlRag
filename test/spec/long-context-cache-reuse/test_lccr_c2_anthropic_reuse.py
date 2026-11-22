"""C2: on Anthropic, only a filing planned for two or more cases is a cache point (req. 2).

The arm, the gateway, the prompt registry and the runner are real; the provider adapter,
the filing resolver and the filing store are fakes (conftest.py). Each case's question
carries its id, so the adapter's calls can be matched to cases.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.runner import EvalRunner, RunnerConfig

# Hand-counted plan: AAA FY2024 three cases, CCC FY2023 two, BBB FY2024 and DDD FY2022 one.
PLAN = [
    ("a1", "AAA", 2024), ("b1", "BBB", 2024), ("a2", "AAA", 2024), ("c1", "CCC", 2023),
    ("d1", "DDD", 2022), ("a3", "AAA", 2024), ("c2", "CCC", 2023),
]
REUSED = {"a1", "a2", "a3", "c1", "c2"}
SINGLE = {"b1", "d1"}


def _plan_cases(kit, plan=PLAN):
    return [kit.make_case(i, ticker=t, fy=y) for i, t, y in plan]


def _filers(plan=PLAN):
    return sorted({(t, y) for _, t, y in plan})


@pytest.mark.criterion("C2")
def test_reused_filings_are_marked_and_single_use_filings_are_not(kit):
    # oracle: hand count above — AAA (3) and CCC (2) are reused; BBB and DDD are asked once
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, _filers())
    cases = _plan_cases(kit)
    arm.plan_cases(cases)
    for case in cases:
        assert arm.answer(case).error is None
    assert adapter.calls == 7
    for case_id in REUSED:
        assert adapter.marked(kit.question_of(case_id)), case_id
    for case_id in SINGLE:
        assert not adapter.marked(kit.question_of(case_id)), case_id


@pytest.mark.criterion("C2")
def test_through_the_runner_with_filing_cache_groups(kit):
    # oracle: the same hand count, end to end: the runner plans the arm, then answers
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, _filers())
    report = EvalRunner(RunnerConfig(max_workers=3, cache_groups="filing")).run(
        _plan_cases(kit), arm)
    assert sorted(r.case.id for r in report.results) == sorted(REUSED | SINGLE)
    assert adapter.calls == 7
    marks = {i: adapter.marked(kit.question_of(i)) for i in REUSED | SINGLE}
    assert marks == {i: i in REUSED for i in REUSED | SINGLE}


@pytest.mark.criterion("C2")
def test_reuse_is_counted_over_document_keys(kit):
    # oracle: requirement 2 — AAA FY2023 resolves to the FY2024 filing (a restated period
    # cited from a later filing), so that one key is planned twice; BBB is planned once
    shared = kit.filing_key("AAA", 2024)
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, [("AAA", 2024), ("BBB", 2024)],
                                keys={("AAA", 2023): shared})
    cases = [kit.make_case("x1", "AAA", 2024), kit.make_case("x2", "AAA", 2023),
             kit.make_case("y1", "BBB", 2024)]
    arm.plan_cases(cases)
    for case in cases:
        assert arm.answer(case).error is None
    assert adapter.marked(kit.question_of("x1"))
    assert adapter.marked(kit.question_of("x2"))
    assert not adapter.marked(kit.question_of("y1"))


@pytest.mark.criterion("C2")
def test_cases_without_a_filing_are_ignored_by_the_plan(kit):
    # oracle: requirement 2 — the no-ticker, no-year and unresolvable cases count for no
    # key; BBB (2) is reused, AAA (1) is not; the three are excluded with no call
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, [("AAA", 2024), ("BBB", 2024)])
    cases = [kit.make_case("n1", None, 2024), kit.make_case("n2", "AAA", None),
             kit.make_case("n3", "ZZZ", 2024), kit.make_case("a1", "AAA", 2024),
             kit.make_case("b1", "BBB", 2024), kit.make_case("b2", "BBB", 2024)]
    arm.plan_cases(cases)
    answers = {c.id: arm.answer(c) for c in cases}
    for case_id in ("n1", "n2", "n3"):
        assert (answers[case_id].error or "").startswith("excluded: ")
    assert adapter.calls == 3
    assert not adapter.marked(kit.question_of("a1"))
    assert adapter.marked(kit.question_of("b1"))
    assert adapter.marked(kit.question_of("b2"))


_TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE"]


@pytest.mark.criterion("C2")
@given(
    counts=st.lists(st.integers(1, 4), min_size=1, max_size=5),
    order=st.randoms(use_true_random=False),
)
def test_marked_exactly_when_the_filing_is_planned_twice_or_more(kit, counts, order):
    # oracle: the generated multiplicities — a filing with count >= 2 is marked on every
    # call, a filing with count 1 on none
    plan = [(f"{t}{n}", t, 2024) for t, k in zip(_TICKERS, counts, strict=False)
            for n in range(k)]
    order.shuffle(plan)
    count_of = dict(zip(_TICKERS, counts, strict=False))
    arm, adapter = kit.make_arm(kit.ANTHROPIC_MODEL, _filers(plan))
    cases = _plan_cases(kit, plan)
    arm.plan_cases(cases)
    for case in cases:
        assert arm.answer(case).error is None
    assert adapter.calls == len(plan)
    for case_id, ticker, _ in plan:
        assert adapter.marked(kit.question_of(case_id)) == (count_of[ticker] >= 2), case_id
