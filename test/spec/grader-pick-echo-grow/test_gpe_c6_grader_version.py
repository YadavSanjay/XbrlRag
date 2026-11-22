"""C6: the grader version is 12 (docs/specs/grader-pick-echo-grow.md, requirement 5)."""

from __future__ import annotations

import pytest

from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import CaseResult, Metrics


def _graded(kit, ending: str):
    case = kit.pick_case()
    ans = kit.answer(kit.real_shape(ending), kit.PICK_VALUES)
    return CaseResult.build(case, ans, grade_case(case, ans))


@pytest.mark.criterion("C6")
def test_grader_version_is_at_least_12(kit):
    # oracle: requirement 5 (identity), on an empty and a graded scorecard. Relaxed from
    # "== 12" to ">= 12" by compound-value-correct (grader 13), as earlier version tests were.
    assert int(grader_version()) >= 12
    assert Metrics.compute([]).to_dict()["grader_version"] == grader_version()
    assert Metrics.compute([_graded(kit, ".")]).to_dict()["grader_version"] == grader_version()


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_version_does_not_depend_on_the_graded_cases(kit):
    # oracle: requirement 5 — the version is the grader's, the same for any scorecard
    graded = [_graded(kit, e) for e in (".", "", "?")]
    versions = {Metrics.compute(graded[:k]).to_dict()["grader_version"] for k in range(4)}
    assert versions == {grader_version()}
    assert int(grader_version()) >= 12
