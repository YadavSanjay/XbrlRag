"""C3: the scorecard reports confident-wrong and value accuracy."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.report import Metrics

# Each kind is one (produced value, decline flags, cited) answer to a lookup expecting 100.
_KINDS = {
    "right_cited": ([100.0], {}, True),  # CORRECT, value-correct
    "right_uncited": ([100.0], {}, False),  # PARTIAL, value-correct
    "wrong_cited": ([250.0], {}, True),  # INCORRECT, silent- and confident-wrong
    "wrong_uncited": ([250.0], {}, False),  # INCORRECT, confident-wrong only
    "hedged_miss": ([250.0], {"hedged": True}, True),  # HEDGED_MISS
    "refused": ([], {"refused": True}, False),  # REFUSED
}


def _results(kinds, scalar_case, answer, graded):
    out = []
    for i, kind in enumerate(kinds):
        values, flags, cited = _KINDS[kind]
        out.append(
            graded(scalar_case(100.0, case_id=f"c{i}"), answer(values, cited=cited, **flags))
        )
    return out


@pytest.mark.criterion("C3")
def test_metrics_count_confident_wrong_and_value_accuracy(scalar_case, answer, graded):
    # oracle: hand count of the six kinds above — 2 confident-wrong (wrong_cited,
    # wrong_uncited), 2 value-correct (right_cited, right_uncited), 1 CORRECT, 1 silent
    results = _results(list(_KINDS), scalar_case, answer, graded)
    m = Metrics.compute(results)
    assert getattr(m, "n_confident_wrong", None) == 2
    # tolerance: exact rationals over n=6; abs=1e-12 only absorbs float division
    assert getattr(m, "confident_wrong_rate", None) == pytest.approx(2 / 6, abs=1e-12)
    assert getattr(m, "value_accuracy", None) == pytest.approx(2 / 6, abs=1e-12)
    assert m.accuracy == pytest.approx(1 / 6, abs=1e-12)
    assert m.silent_wrong_rate == pytest.approx(1 / 6, abs=1e-12)
    d = m.to_dict()
    assert d.get("n_confident_wrong") == 2
    assert d.get("confident_wrong_rate") == pytest.approx(2 / 6, abs=1e-4)  # to_dict rounds
    assert d.get("value_accuracy") == pytest.approx(2 / 6, abs=1e-4)  # to_dict rounds


@pytest.mark.criterion("C3")
def test_empty_results_report_zero_rates():
    # oracle: C3 invariant — no cases, nothing wrong or right
    m = Metrics.compute([])
    assert getattr(m, "n_confident_wrong", None) == 0
    # tolerance: the rates are exactly zero; abs=1e-12 is the float-comparison idiom
    assert getattr(m, "confident_wrong_rate", None) == pytest.approx(0.0, abs=1e-12)
    assert getattr(m, "value_accuracy", None) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.criterion("C3")
@given(kinds=st.lists(st.sampled_from(sorted(_KINDS)), min_size=1, max_size=30))
def test_metrics_invariants_hold_for_any_mix(scalar_case, answer, graded, kinds):
    # oracle: C3 invariants and per-kind hand counts from the _KINDS table
    m = Metrics.compute(_results(kinds, scalar_case, answer, graded))
    n = len(kinds)
    n_cw = sum(k in ("wrong_cited", "wrong_uncited") for k in kinds)
    n_vc = sum(k in ("right_cited", "right_uncited") for k in kinds)
    assert getattr(m, "n_confident_wrong", None) == n_cw
    # tolerance: exact rationals; abs=1e-12 only absorbs float division
    assert getattr(m, "confident_wrong_rate", None) == pytest.approx(n_cw / n, abs=1e-12)
    assert getattr(m, "value_accuracy", None) == pytest.approx(n_vc / n, abs=1e-12)
    assert m.confident_wrong_rate >= m.silent_wrong_rate
    assert m.value_accuracy >= m.accuracy
