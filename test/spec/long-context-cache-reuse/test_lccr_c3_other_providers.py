"""C3: on every provider but Anthropic the filing message is always marked (req. 2).

Each test runs the same plan on an Anthropic arm as a control: there the single-use filing
is unmarked, so the plan is known to have been taken.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

PLAN = [("a1", "AAA", 2024), ("a2", "AAA", 2024), ("b1", "BBB", 2024), ("c1", "CCC", 2023)]


def _run(kit, model, plan):
    filers = sorted({(t, y) for _, t, y in plan})
    arm, adapter = kit.make_arm(model, filers)
    cases = [kit.make_case(i, ticker=t, fy=y) for i, t, y in plan]
    arm.plan_cases(cases)
    for case in cases:
        assert arm.answer(case).error is None
    assert adapter.calls == len(plan)
    return {i: adapter.marked(kit.question_of(i)) for i, _, _ in plan}


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("which", ["OPENAI_MODEL", "DEEPSEEK_MODEL"])
def test_non_anthropic_marks_every_filing_whatever_the_plan(kit, which):
    # oracle: requirement 2 — gpt-5.6-sol (provider openai) and deepseek-flash (provider
    # deepseek) in configs/llm/llm.yaml keep today's mark; claude-opus-5-5 (anthropic)
    # leaves the single-use BBB and CCC filings unmarked
    assert _run(kit, getattr(kit, which), PLAN) == {"a1": True, "a2": True, "b1": True,
                                                    "c1": True}
    assert _run(kit, kit.ANTHROPIC_MODEL, PLAN) == {"a1": True, "a2": True, "b1": False,
                                                    "c1": False}


@pytest.mark.criterion("C3")
@given(
    counts=st.lists(st.integers(1, 3), min_size=1, max_size=4),
    which=st.sampled_from(["OPENAI_MODEL", "DEEPSEEK_MODEL"]),
)
def test_non_anthropic_marks_all_for_any_multiplicities(kit, counts, which):
    # oracle: the generated multiplicities — every call marked off Anthropic; on the
    # Anthropic control a call is marked exactly when its filing's count is >= 2
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    plan = [(f"{t}{n}", t, 2024) for t, k in zip(tickers, counts, strict=False)
            for n in range(k)]
    count_of = dict(zip(tickers, counts, strict=False))
    assert set(_run(kit, getattr(kit, which), plan).values()) == {True}
    control = _run(kit, kit.ANTHROPIC_MODEL, plan)
    assert control == {i: count_of[t] >= 2 for i, t, _ in plan}
