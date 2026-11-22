"""C8: nothing cited is "not applicable" for precision, and recall 0."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.grounding_v2 import attach_v2_grounding, grade_v2_grounding
from src.eval.core.report import Metrics

AAA = 1000
FY = ("2025-01-01", "2025-12-31")


def _case(gb, n):
    i = gb.inp(AAA, "us-gaap:Revenues", "USD", *FY, 100000000000)
    return gb.case(f"c-{n}", [i])


@pytest.mark.criterion("C8")
def test_refusal_has_no_precision(gb):
    # oracle: requirement 10 — precision None, recall 0, nothing right or wrong
    g = grade_v2_grounding(_case(gb, 0), [], {})
    assert g is not None
    assert g["precision"] is None
    assert (g["recall"], g["right"], g["wrong"], g["unresolved"], g["evidenced"]) == (
        0, (), (), (), ())


@pytest.mark.criterion("C8")
@given(st.integers(1, 6), st.sampled_from([Grade.REFUSED, Grade.CORRECT, Grade.INCORRECT]))
def test_an_arm_that_cites_nothing_is_not_applicable(gb, n, grade):
    # oracle: requirements 10 and 13 — every case None, so the run's mean is None; every
    # result is still v2-grounded and counted
    results = [gb.result(_case(gb, k), [], grade=grade) for k in range(n)]
    assert attach_v2_grounding(results, gb.FakeFactStore({})) == n
    assert all(r.grade.grounding_precision is None for r in results)
    assert all(r.grade.grounding_recall == 0 for r in results)
    metrics = Metrics.compute(results)
    assert metrics.grounding_precision is None
    assert metrics.grounding_recall == 0
