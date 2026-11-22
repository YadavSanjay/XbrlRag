"""C1: every scorecard records which grader produced it."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import Metrics, RunReport


@pytest.mark.criterion("C1")
def test_version_is_recorded_on_metrics_and_run_report(scalar_case, answer, graded):
    # oracle: requirement 1 of docs/specs/eval-grader-honesty.md (identity: the scorecard
    # carries exactly the version the graders report)
    version = grader_version()
    assert isinstance(version, str) and version.strip() != ""

    results = [graded(scalar_case(100.0), answer([100.0]))]
    assert Metrics.compute(results).to_dict().get("grader_version") == version

    report = RunReport(run_id="eval-test", suite="synthetic", results=results)
    assert report.to_dict(include_results=False).get("grader_version") == version


@pytest.mark.criterion("C1")
@given(
    st.lists(
        st.tuples(
            st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
            st.booleans(),
        ),
        min_size=0,
        max_size=8,
    )
)
def test_version_does_not_depend_on_the_results(scalar_case, answer, graded, rows):
    # oracle: requirement 1 — the version identifies the grader, so it is the same for any
    # set of results, including an empty one, and stable across calls
    results = [
        graded(scalar_case(value, case_id=f"s{i}"), answer([value * 2.0], hedged=hedged))
        for i, (value, hedged) in enumerate(rows)
    ]
    first = grader_version()
    assert isinstance(first, str) and first.strip() != ""
    assert grader_version() == first
    assert Metrics.compute(results).to_dict().get("grader_version") == first
