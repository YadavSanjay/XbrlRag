"""C6: the peer-rank reading is grader version 5 (docs/specs/peer-rank-grading.md, req. 5).

Since docs/specs/peer-rank-count-phrases.md (requirement 5) this pin holds the version at
least 5; the newest grading change pins the exact version.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.honesty import grader_version
from src.eval.core.graders.v2 import grade_v2
from src.eval.core.report import CaseResult, Metrics


@pytest.mark.criterion("C6")
def test_grader_version_is_5():
    # Updated by docs/specs/peer-rank-count-phrases.md (requirement 5): at least 5.
    # oracle: requirement 5 (identity)
    assert int(grader_version()) >= 5
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 5


@pytest.mark.criterion("C6")
@given(order=st.permutations(["AMZN", "COST", "HD", "WMT"]))
def test_every_scorecard_carries_version_5(kit, order):
    # Updated by docs/specs/peer-rank-count-phrases.md (requirement 5): at least 5.
    # oracle: requirement 5 — the version does not depend on what was graded
    case = kit.wmt_case()
    ans = kit.answer(kit.WMT_TEXT, kit.rows_in(list(order)), fact_ids=[101])
    m = Metrics.compute([CaseResult.build(case, ans, grade_v2(case, ans))])
    assert int(m.grader_version) >= 5
    assert m.to_dict()["grader_version"] == m.grader_version == grader_version()
