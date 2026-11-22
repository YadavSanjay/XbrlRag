"""C8: these rules are grader version 7 (docs/specs/grading-dont-care-and-ties.md, req. 8).

Since docs/specs/grader-synthesis-error.md (requirement 7) this pin holds the version at least 7;
the newest grading change pins the exact version.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.honesty import grader_version
from src.eval.core.graders.v2 import grade_v2
from src.eval.core.report import CaseResult, Metrics


@pytest.mark.criterion("C8")
def test_grader_version_is_7():
    # Updated by docs/specs/grader-synthesis-error.md (requirement 7): at least 7.
    # oracle: requirement 8 (identity)
    assert int(grader_version()) >= 7
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 7


@pytest.mark.criterion("C8")
@given(third=st.sampled_from(["COST", "WMT"]))
def test_every_scorecard_carries_version_7(kit, third):
    # Updated by docs/specs/grader-synthesis-error.md (requirement 7): at least 7.
    # oracle: requirement 8 — the version does not depend on what was graded
    case = kit.screen_case([("AMZN", 15673e6), ("HD", 2161e6), ("COST", 0.0), ("WMT", 0.0)])
    ans = kit.screen_answer([("AMZN", 15673e6), ("HD", 2161e6), (third, 0.0)])
    m = Metrics.compute([CaseResult.build(case, ans, grade_v2(case, ans))])
    assert int(m.grader_version) >= 7
    assert m.to_dict()["grader_version"] == m.grader_version == grader_version()
