"""C6: the grader version: 3 here, 4 since grading-precision, 5 since peer-rank-grading.

Since docs/specs/peer-rank-count-phrases.md (requirement 5) this pin holds the version at
least 5; the newest grading change pins the exact version.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import Metrics


@pytest.mark.criterion("C6")
def test_grader_version_is_5():
    # Updated by docs/specs/peer-rank-grading.md (requirement 5): the version is now 5.
    # Updated by docs/specs/peer-rank-count-phrases.md (requirement 5): at least 5.
    # oracle: requirement 6 of docs/specs/arm-neutral-grading.md, as amended by
    # requirement 7 of docs/specs/grading-precision.md (identity)
    assert int(grader_version()) >= 5
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 5


@pytest.mark.criterion("C6")
@given(
    values=st.lists(st.floats(min_value=0.0, max_value=1e6, allow_nan=False), max_size=10)
)
def test_every_scorecard_carries_version_5(scalar_case, answer, graded, values):
    # Updated by docs/specs/peer-rank-count-phrases.md (requirement 5): at least 5.
    # oracle: requirement 6 — the version does not depend on what was graded
    results = [
        graded(scalar_case(100.0, case_id=f"c{i}"), answer([v])) for i, v in enumerate(values)
    ]
    m = Metrics.compute(results)
    assert int(m.grader_version) >= 5
    assert m.to_dict()["grader_version"] == m.grader_version == grader_version()
