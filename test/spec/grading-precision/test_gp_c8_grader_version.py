"""C8: the precision rules were grader version 4 (docs/specs/grading-precision.md, req. 7).

Version 5 since docs/specs/peer-rank-grading.md (requirement 5). Since
docs/specs/peer-rank-count-phrases.md (requirement 5) this pin holds the version at least 5;
the newest grading change pins the exact version.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import CaseResult, Metrics


@pytest.mark.criterion("C8")
def test_grader_version_is_5():
    # Updated by docs/specs/peer-rank-grading.md (requirement 5): the version is now 5.
    # Updated by docs/specs/peer-rank-count-phrases.md (requirement 5): at least 5.
    # oracle: requirement 7 (identity)
    assert int(grader_version()) >= 5
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 5


@pytest.mark.criterion("C8")
@given(millions=st.lists(st.integers(min_value=1, max_value=999), max_size=6))
def test_every_scorecard_carries_version_5(dev, answer, millions):
    # Updated by docs/specs/peer-rank-count-phrases.md (requirement 5): at least 5.
    # oracle: requirement 7 — the version does not depend on what was graded
    case = dev("intc_opinc")
    results = []
    for m in millions:
        ans = answer(f"${m} million")
        results.append(CaseResult.build(case, ans, grade_case(case, ans)))
    m = Metrics.compute(results)
    assert int(m.grader_version) >= 5
    assert m.to_dict()["grader_version"] == m.grader_version == grader_version()
