"""C1: a case's checkpoint payload restores it exactly (requirements 1-2).

The judge's error text and cost are set on a result by attribute, as the graded runner does
(requirement 1); a result nobody judged keeps the defaults.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import checkpoint_payload
from src.eval.core.report import CaseResult, Metrics
from src.eval.core.system import SystemAnswer

JUDGED = {"correctness": 0.9, "completeness": 0.5, "citation_support": 1.0,
          "unsupported_claims": 0}
JUDGE_ERROR = "judge_error: the reply has no JSON object"


def _restore(result: CaseResult) -> CaseResult:
    """What a resume sees: the payload through JSON, back onto the suite's case."""
    return CaseResult.from_dict(json.loads(json.dumps(checkpoint_payload(result))), result.case)


def _assert_same(restored: CaseResult, original: CaseResult) -> None:
    assert restored.case is original.case
    assert restored.grade == original.grade  # dataclass equality, field by field
    assert restored.query_id == original.query_id
    assert restored.latency_ms == original.latency_ms
    assert restored.cost_usd == original.cost_usd
    assert restored.served_models == original.served_models
    assert restored.judge_error == original.judge_error
    assert restored.judge_cost_usd == original.judge_cost_usd
    assert restored.to_dict() == original.to_dict()


def _samples(kit) -> list[CaseResult]:
    case = kit.make_case("c1", ticker="AAA", fy=2024)
    right = kit.graded(case, SystemAnswer(text="t", numeric_values=[1.0], fact_ids=[1],
                                          query_id="q-1", latency_ms=12.345678,
                                          cost_usd=0.0012345678,
                                          served_models={"m": ["m-2026-01", "m-2026-02"]}))
    wrong = kit.graded(kit.make_case("c2"), SystemAnswer(numeric_values=[5.0], fact_ids=[1]))
    refused = kit.graded(kit.make_case("c3"), SystemAnswer(text="cannot", refused=True))
    error = kit.graded(kit.make_case("c4"), SystemAnswer(error="ValueError('bad plan')"))
    judged = kit.graded(kit.make_case("c5"), SystemAnswer(numeric_values=[1.0], fact_ids=[1]))
    judged.grade.judge_scores = dict(JUDGED)
    judged.judge_error = None
    judged.judge_cost_usd = 0.0123456789
    judge_error = kit.graded(kit.make_case("c6"), SystemAnswer(numeric_values=[1.0]))
    judge_error.grade.judge_scores = dict.fromkeys(JUDGED)
    judge_error.judge_error = JUDGE_ERROR
    judge_error.judge_cost_usd = 0.008
    # set by attribute, as a breakdown grader and the v2 grader do: a cited wrong answer that
    # is incomplete is not silent_wrong, so dropping the flag changes a metric
    incomplete = kit.graded(kit.make_case("c7"), SystemAnswer(numeric_values=[5.0], fact_ids=[1]))
    incomplete.grade.incomplete = True
    incomplete.grade.checks = {"universe": True, "FY2023": False}
    return [right, wrong, refused, error, judged, judge_error, incomplete]


@pytest.mark.criterion("C1")
def test_payload_keeps_exact_cost_latency_served_and_judge(kit):
    # oracle: requirement 1 — the values set on the result above, unrounded (to_dict would
    # round cost to 6 places and latency to 1)
    right, *_rest = _samples(kit)
    payload = checkpoint_payload(right)
    # tolerance: rel=1e-12 is far tighter than to_dict's rounding (0.001235 and 12.3 differ
    # by 3e-4 and 4e-3 relative), so a rounded value fails
    assert payload["cost_usd"] == pytest.approx(0.0012345678, rel=1e-12)
    assert payload["latency_ms"] == pytest.approx(12.345678, rel=1e-12)
    assert payload["served_models"] == {"m": ["m-2026-01", "m-2026-02"]}
    assert payload["case_id"] == "c1"
    assert payload["grade"] == "CORRECT"
    assert payload["incomplete"] is False
    # requirement 1: a result nobody judged carries no judge error and no judge cost
    assert payload["judge_error"] is None
    # tolerance: the default is exactly zero; abs=1e-15 admits nothing a judge call costs
    assert payload["judge_cost_usd"] == pytest.approx(0.0, abs=1e-15)
    samples = _samples(kit)
    judged = checkpoint_payload(samples[4])
    assert judged["judge_scores"] == JUDGED
    assert judged["judge_error"] is None
    # tolerance: rel=1e-12, so a cost rounded to 6 places (0.012346) fails
    assert judged["judge_cost_usd"] == pytest.approx(0.0123456789, rel=1e-12)
    failed = checkpoint_payload(samples[5])
    assert failed["judge_error"] == JUDGE_ERROR
    assert failed["judge_cost_usd"] == pytest.approx(0.008, rel=1e-12)  # tolerance: as above
    json.dumps(payload)  # JSON-serialisable, as a JSONB column needs


@pytest.mark.criterion("C1")
def test_each_sample_restores_exactly(kit):
    # oracle: requirement 2 — identity with the original result
    for original in _samples(kit):
        _assert_same(_restore(original), original)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_metrics_over_restored_results_equal_the_originals(kit):
    # oracle: requirement 10 — a resumed run's metrics are those of the same answers
    originals = _samples(kit)
    restored = [_restore(r) for r in originals]
    assert Metrics.compute(restored) == Metrics.compute(originals)
    assert Metrics.compute(restored).n == 7
    # hand count: c2 is the one silent wrong; c7 is wrong and cited but incomplete.
    # tolerance: an exact ratio; abs=1e-12 only absorbs float division
    assert Metrics.compute(restored).silent_wrong_rate == pytest.approx(1 / 7, abs=1e-12)


@pytest.mark.criterion("C1")
def test_a_payload_for_another_case_is_refused(kit):
    # oracle: requirement 2 — a payload restores only onto its own case
    right = _samples(kit)[0]
    payload = json.loads(json.dumps(checkpoint_payload(right)))
    with pytest.raises(ValueError):
        CaseResult.from_dict(payload, kit.make_case("other"))


_values = st.lists(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False), max_size=3)


@pytest.mark.criterion("C1")
@given(values=_values, facts=st.lists(st.integers(1, 5), max_size=3), refused=st.booleans(),
       hedged=st.booleans(), error=st.sampled_from([None, "ReadTimeout('timed out')"]),
       latency=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
       cost=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
       served=st.dictionaries(st.sampled_from(["a", "b"]),
                              st.lists(st.sampled_from(["a-1", "a-2", "b-1"]), max_size=2),
                              max_size=2),
       judge=st.sampled_from([None, "scores", "error"]),
       judge_cost=st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_round_trip_holds_for_any_graded_answer(kit, values, facts, refused, hedged, error,
                                                latency, cost, served, judge, judge_cost):
    # oracle: requirement 2 — restoring is the identity on any result the real grader makes
    answer = SystemAnswer(text="t", numeric_values=values, fact_ids=facts, refused=refused,
                          hedged=hedged, error=error, query_id="q", latency_ms=latency,
                          cost_usd=cost, served_models=served)
    original = kit.graded(kit.make_case("p", ticker="AAA", fy=2024), answer)
    if judge == "scores":
        original.grade.judge_scores = dict(JUDGED)
        original.judge_error, original.judge_cost_usd = None, judge_cost
    elif judge == "error":
        original.grade.judge_scores = dict.fromkeys(JUDGED)
        original.judge_error, original.judge_cost_usd = JUDGE_ERROR, judge_cost
    _assert_same(_restore(original), original)
