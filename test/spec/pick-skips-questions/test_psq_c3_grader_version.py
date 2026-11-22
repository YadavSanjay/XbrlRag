"""C3: the grader version is 11 (docs/specs/pick-skips-questions.md, requirement 6)."""

from __future__ import annotations

import pytest

from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import CaseResult, Metrics


def _graded(kit, text_index: int):
    case = kit.pick_case()
    ans = kit.answer(kit.SHAPES[text_index], kit.SHAPE_VALUES[text_index])
    return CaseResult.build(case, ans, grade_case(case, ans))


@pytest.mark.criterion("C3")
def test_grader_version_is_11(kit):
    # oracle: requirement 6 (identity), on an empty and a graded scorecard
    assert int(grader_version()) >= 11
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 11
    assert int(Metrics.compute([_graded(kit, 0)]).to_dict()["grader_version"]) >= 11


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_version_does_not_depend_on_the_graded_cases(kit):
    # oracle: requirement 6 — the version is the grader's, the same for any scorecard
    graded = [_graded(kit, i) for i in range(3)]
    versions = {Metrics.compute(graded[:k]).to_dict()["grader_version"] for k in range(4)}
    assert len(versions) == 1 and int(versions.pop()) >= 11
