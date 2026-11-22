"""C3: the scorecard reports hedged misses and the decline rate; accuracy is unchanged."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.report import Metrics

# Outcome kinds for a scalar case expecting 100.0, with the answer that produces each.
_OUTCOMES = ("correct", "hedged_miss", "refused", "incorrect")


def _answer_for(kind, answer):
    if kind == "correct":
        return answer([100.0])
    if kind == "hedged_miss":
        return answer([250.0], hedged=True)
    if kind == "refused":
        return answer([], refused=True)
    return answer([250.0])


@pytest.mark.criterion("C3")
def test_hand_counted_scorecard(scalar_case, answer, graded):
    # oracle: hand count — 2 correct, 3 hedged misses, 1 refusal, 1 asserted wrong number
    # out of n = 7, so hedged_miss_rate = 3/7, decline_rate = (1 + 3)/7, accuracy = 2/7
    kinds = ["correct"] * 2 + ["hedged_miss"] * 3 + ["refused", "incorrect"]
    results = [graded(scalar_case(100.0, case_id=f"s{i}"), _answer_for(k, answer))
               for i, k in enumerate(kinds)]
    m = Metrics.compute(results)

    assert getattr(m, "n_hedged_miss", None) == 3
    assert m.n_incorrect == 1
    assert m.n_refused == 1
    # tolerance: exact rationals; rel=1e-12 only absorbs float division
    assert getattr(m, "hedged_miss_rate", None) == pytest.approx(3 / 7, rel=1e-12)
    assert getattr(m, "decline_rate", None) == pytest.approx(4 / 7, rel=1e-12)
    assert m.accuracy == pytest.approx(2 / 7, rel=1e-12)
    assert m.to_dict().get("n_hedged_miss") == 3


@pytest.mark.criterion("C3")
@given(st.lists(st.sampled_from(_OUTCOMES), min_size=1, max_size=30))
def test_counts_partition_n_and_rates_follow_counts(scalar_case, answer, graded, kinds):
    # oracle: property — every case gets exactly one grade, so the grade counts partition
    # n, and each rate is its count over n
    results = [graded(scalar_case(100.0, case_id=f"s{i}"), _answer_for(k, answer))
               for i, k in enumerate(kinds)]
    m = Metrics.compute(results)
    n = len(kinds)

    hm = getattr(m, "n_hedged_miss", None)
    assert isinstance(hm, int)
    assert m.n_correct + m.n_partial + m.n_incorrect + m.n_refused + hm + m.n_error == n
    assert hm == kinds.count("hedged_miss")
    # tolerance: exact rationals; rel=1e-12 only absorbs float division
    assert getattr(m, "hedged_miss_rate", None) == pytest.approx(hm / n, rel=1e-12)
    assert getattr(m, "decline_rate", None) == pytest.approx((m.n_refused + hm) / n, rel=1e-12)
    assert m.accuracy == pytest.approx(kinds.count("correct") / n, rel=1e-12)
