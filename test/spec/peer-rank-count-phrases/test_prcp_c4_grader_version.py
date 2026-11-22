"""C4: count phrases are grader version 6 (docs/specs/peer-rank-count-phrases.md, req. 4).

Since docs/specs/grading-dont-care-and-ties.md (requirement 9) this pin holds the version at
least 6; the newest grading change pins the exact version.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.honesty import grader_version
from src.eval.core.graders.v2 import grade_v2
from src.eval.core.report import CaseResult, Metrics


@pytest.mark.criterion("C4")
def test_grader_version_is_6():
    # Updated by docs/specs/grading-dont-care-and-ties.md (requirement 9): at least 6.
    # oracle: requirement 4 (identity)
    assert int(grader_version()) >= 6
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 6


@pytest.mark.criterion("C4")
@given(stated=st.integers(min_value=1, max_value=33))
def test_every_scorecard_carries_version_6(kit, stated):
    # Updated by docs/specs/grading-dont-care-and-ties.md (requirement 9): at least 6.
    # oracle: requirement 4 — the version does not depend on what was graded
    case = kit.mcd_case()
    ans = kit.answer(f"I ranked 32 of the 33. McDonald's is No. {stated}.", fact_ids=[1])
    m = Metrics.compute([CaseResult.build(case, ans, grade_v2(case, ans))])
    assert int(m.grader_version) >= 6
    assert m.to_dict()["grader_version"] == m.grader_version == grader_version()
