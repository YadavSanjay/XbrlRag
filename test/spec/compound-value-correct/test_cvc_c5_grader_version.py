"""C5: the grader version rises to at least 13 (requirement 6)."""

from __future__ import annotations

import pytest

from src.eval.core.graders.honesty import grader_version
from src.eval.core.graders.v2 import grade_v2
from src.eval.core.report import CaseResult, Metrics


@pytest.mark.criterion("C5")
def test_grader_version_is_at_least_13():
    # oracle: requirement 6 — version 12 was grader-pick-echo-grow's; this change is the next
    assert int(grader_version()) >= 13
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 13


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_metrics_over_compound_results_carry_the_version(kit):
    # oracle: requirement 6 — metrics over any results, here compound ones cited and uncited,
    # report the same grader version as the grader, and it is at least 13
    case = kit.trend_compound()
    results = [CaseResult.build(case, a, grade_v2(case, a))
               for a in (kit.hx_answer(), kit.hx_answer(fact_ids=[]))]
    m = Metrics.compute(results)
    assert m.to_dict()["grader_version"] == grader_version()
    assert int(m.to_dict()["grader_version"]) >= 13
