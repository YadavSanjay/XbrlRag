"""C4: a compound's value_correct survives the checkpoint payload (requirement 5)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import checkpoint_payload
from src.eval.core.graders.v2 import grade_v2
from src.eval.core.report import CaseResult


def _round_trip(case, answer):
    result = CaseResult.build(case, answer, grade_v2(case, answer))
    return result, CaseResult.from_dict(checkpoint_payload(result), case)


@pytest.mark.criterion("C4")
def test_uncited_right_compound_round_trips_value_correct(kit):
    # oracle: requirement 5 with C1 — the uncited right answer is value-correct before and
    # after the round trip
    case = kit.trend_compound()
    before, after = _round_trip(case, kit.hx_answer(fact_ids=[]))
    assert before.grade.value_correct is True
    assert after.grade.value_correct is True
    assert after.grade.grade is before.grade.grade


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(rank_ok=st.booleans(), disclosed=st.booleans(), trend_ok=st.booleans(),
       cited=st.lists(st.sampled_from([1, 2, 34]), unique=True, max_size=3))
def test_round_trip_is_identity_on_value_correct(kit, rank_ok, disclosed, trend_ok, cited):
    # oracle: requirement 5 — rebuilding from the payload is an identity on value_correct;
    # requirement 1 by hand for the value itself
    case = kit.trend_compound()
    answer = kit.hx_answer(rank_ok, disclosed, "right" if trend_ok else "wrong", cited)
    before, after = _round_trip(case, answer)
    assert after.grade.value_correct is before.grade.value_correct
    assert after.grade.value_correct is (rank_ok and trend_ok)
