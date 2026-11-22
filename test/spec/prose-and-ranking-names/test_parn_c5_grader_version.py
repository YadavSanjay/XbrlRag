"""C5: the grader version rises to at least 14 (requirement 5)."""

from __future__ import annotations

import pytest

from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import CaseResult, Metrics


@pytest.mark.criterion("C5")
def test_grader_version_is_at_least_14():
    # oracle: requirement 5 — version 13 was compound-value-correct's; this change is the next
    assert int(grader_version()) >= 14
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 14


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_metrics_over_generated_results_carry_the_version(kit):
    # oracle: requirement 5 — metrics over any results, here generated compounds cited and
    # uncited, report the grader's version, and it is at least 14
    case = kit.generated("rank_by_ratio")
    results = [CaseResult.build(case, a, grade_case(case, a))
               for a in (kit.gen_answer("rank_by_ratio"),
                         kit.gen_answer("rank_by_ratio", cited=False))]
    m = Metrics.compute(results)
    assert m.to_dict()["grader_version"] == grader_version()
    assert int(m.to_dict()["grader_version"]) >= 14
