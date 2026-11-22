"""C6: the grader version is at least 9 (docs/specs/grader-v9-number-reading.md, requirement 6;
relaxed from "9" by grading-stated-aggregate, which sets version 10)."""

from __future__ import annotations

import pytest

from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import CaseResult, Metrics


@pytest.mark.criterion("C6")
def test_grader_version_is_9(kit):
    # oracle: requirement 6 (identity), on an empty and a graded scorecard
    assert int(grader_version()) >= 9
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 9
    case = kit.lookup_case()
    ans = kit.answer("DEVA's revenue was $500 million.", [500e6])
    m = Metrics.compute([CaseResult.build(case, ans, grade_case(case, ans))])
    assert m.to_dict()["grader_version"] == grader_version()


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_version_does_not_depend_on_the_graded_cases(kit):
    # oracle: requirement 6 — the version is the grader's, the same for any scorecard
    case = kit.lookup_case()
    graded = [CaseResult.build(case, a, grade_case(case, a)) for a in (
        kit.answer("DEVA's revenue was $500 million.", [500e6]),
        kit.answer("DEVA ranked 3rd.", [3.0], hedged=True))]
    versions = {Metrics.compute(graded[:k]).to_dict()["grader_version"] for k in (0, 1, 2)}
    assert versions == {grader_version()}
