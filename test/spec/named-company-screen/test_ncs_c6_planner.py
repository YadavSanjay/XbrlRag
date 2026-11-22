"""C6: the planner runs the named-company check (docs/specs/named-company-screen.md, R4, R5).

The real ``PlanGenerator`` over a real ``LLMGateway`` whose provider adapter replies from a
script (conftest; the provider is the I/O boundary). The question is synthetic. Expected
outcomes are Requirements 4 and 5 by hand: a screen of another universe is a plan error,
retried with VALIDATION_FEEDBACK; all attempts failing ends in plan_validation_failed.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

QUERY = "Of Apple, Microsoft and Alphabet, whose FY2024 R&D spend was the largest?"
NAMED = ["AAPL", "MSFT", "GOOGL"]


@pytest.mark.criterion("C6")
def test_c6_wrong_screen_is_retried(kit, plan_with):
    # oracle: Requirement 5 by hand — the covered screen is rejected, the feedback names the
    # three tickers, and the member-list plan of the second call is returned
    wrong, right = kit.screen_plan("covered"), kit.screen_plan(["GOOGL", "AAPL", "MSFT"])
    result, adapter = plan_with([wrong, right], QUERY, kit.intent(NAMED))
    assert result == right
    assert adapter.calls == 2
    feedback = adapter.user_text(1).split("VALIDATION_FEEDBACK", 1)
    assert len(feedback) == 2, "the retry carries no VALIDATION_FEEDBACK"
    assert "universe" in feedback[1]
    for t in NAMED:
        assert t in feedback[1]


@pytest.mark.criterion("C6")
def test_c6_always_wrong_fails_validation(kit, plan_with):
    # oracle: Requirement 5 by hand — every attempt screens the corpus, so the result is the
    # plan_validation_failed error whose reason names the universe problem
    result, adapter = plan_with([kit.screen_plan("<absent>")], QUERY, kit.intent(NAMED))
    assert result.get("error") == "plan_validation_failed"
    assert "universe" in result.get("reason", "")
    assert adapter.calls >= 2


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_c6_out_of_scope_intents_accept_the_covered_screen(kit, plan_with):
    # oracle: Requirement 4 by hand — the same covered screen passes on the first call when
    # only one company is named, or when the router named the covered universe; with three
    # named companies and no universe it does not
    covered = kit.screen_plan("covered")
    one, a1 = plan_with([covered], "Is Apple's FY2024 R&D the largest?", kit.intent(["AAPL"]))
    assert one == covered and a1.calls == 1
    grp, a2 = plan_with([covered], QUERY, kit.intent(NAMED, universe="covered"))
    assert grp == covered and a2.calls == 1
    named, a3 = plan_with([covered, kit.screen_plan(NAMED)], QUERY, kit.intent(NAMED))
    assert named == kit.screen_plan(NAMED) and a3.calls == 2


@pytest.mark.criterion("C6")
@settings(max_examples=20)
@given(st.data())
def test_c6_wrong_then_right_for_any_named_set(kit, plan_with, data):
    # oracle: Requirements 4 and 5 — for any two or more named companies, a wrong screen and
    # then the exact member list (shuffled, lower-cased) returns the second plan after two
    # calls
    named = data.draw(st.lists(st.sampled_from(kit.POOL), min_size=2, max_size=4, unique=True))
    wrong = data.draw(st.sampled_from([kit.screen_plan("covered"), kit.screen_plan(None),
                                       kit.screen_plan("big_tech"),
                                       kit.screen_plan([*named[:-1], "IBM"]),
                                       kit.screen_plan([*named, "IBM"])]))
    listed = data.draw(st.permutations([t.lower() for t in named]))
    right = kit.screen_plan(listed)
    result, adapter = plan_with([wrong, right], QUERY, kit.intent(named))
    assert result == right
    assert adapter.calls == 2
