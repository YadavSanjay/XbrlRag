"""C3: an empty answer scores zero without a call; invalid inputs are refused."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

QUESTION = "What did AAA say about liquidity?"
REFERENCE = "Cash and equivalents cover two years of operations."


@pytest.mark.criterion("C3")
def test_empty_answer_scores_zero_without_a_call(make_judge):
    # oracle: requirement 1 — no claims, no call; an empty answer is neither correct nor
    # complete
    judge, adapter = make_judge("not used")
    result = judge.judge(QUESTION, REFERENCE, ["two years of cash"], [])
    assert adapter.calls == 0
    assert result.error is None
    assert list(result.verdicts) == []
    # tolerance: a defined zero, not a computed ratio, so it must be exact
    assert result.correctness == pytest.approx(0.0, abs=0.0)
    assert result.completeness == pytest.approx(0.0, abs=0.0)
    assert result.citation_support == pytest.approx(0.0, abs=0.0)
    assert result.unsupported_claims == 0
    assert result.judge_version == "v1:claude-opus-5-5"


@pytest.mark.criterion("C3")
def test_empty_key_points_and_duplicate_ids_raise(make_judge, reply, claim):
    # oracle: requirement 1 — both are caller errors, refused before any call
    judge, adapter = make_judge(reply([("c1", True, True)], [True]))
    with pytest.raises(ValueError):
        judge.judge(QUESTION, REFERENCE, [], [claim("c1")])
    with pytest.raises(ValueError):
        judge.judge(QUESTION, REFERENCE, ["two years of cash"], [claim("c1"), claim("c1")])
    assert adapter.calls == 0


@pytest.mark.criterion("C3")
@given(points=st.lists(st.text(min_size=1, max_size=40), min_size=1, max_size=6))
def test_empty_answer_never_calls(make_judge, points):
    # oracle: requirement 1, for any non-empty key points
    judge, adapter = make_judge("not used")
    result = judge.judge(QUESTION, REFERENCE, points, [])
    assert adapter.calls == 0
    assert result.error is None
    scores = (result.correctness, result.completeness, result.citation_support)
    # tolerance: a defined zero, not a computed ratio, so it must be exact
    assert scores == pytest.approx((0.0, 0.0, 0.0), abs=0.0)
    assert result.unsupported_claims == 0
    assert result.judge_version == "v1:claude-opus-5-5"
