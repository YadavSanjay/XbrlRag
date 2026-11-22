"""C4: the grader version is at least 10 (docs/specs/grading-stated-aggregate.md, requirement 3;
relaxed from "10" by docs/specs/pick-skips-questions.md requirement 6, grader 11)."""

from __future__ import annotations

import pytest

from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import CaseResult, Metrics


@pytest.mark.criterion("C4")
def test_grader_version_is_at_least_10(kit):
    # oracle: requirement 3 (identity), on an empty and a graded scorecard
    assert int(grader_version()) >= 10
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 10
    case = kit.real_case()
    ans = kit.real_answer()
    m = Metrics.compute([CaseResult.build(case, ans, grade_case(case, ans))])
    assert int(m.to_dict()["grader_version"]) >= 10


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_version_does_not_depend_on_the_graded_cases(kit):
    # oracle: requirement 3 — the version is the grader's, the same for any scorecard
    case = kit.real_case()
    graded = [CaseResult.build(case, a, grade_case(case, a)) for a in (
        kit.real_answer(), kit.real_answer(hedged=False))]
    assert len({Metrics.compute(graded[:k]).to_dict()["grader_version"] for k in (0, 1, 2)}) == 1
