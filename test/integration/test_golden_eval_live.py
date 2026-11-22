"""Live accuracy check (WS-2 / A3): score the XBRL pipeline against the static
golden set with the numeric-tolerance grader.

Skips without a reachable xbrl_db (module marker) and without OPENAI_API_KEY (the
shared `pipeline` fixture skips). The full-set run is heavy (one pipeline call per
case), so it is gated behind RUN_GOLDEN_EVAL=1; a small headline-lookup subset runs
whenever the backends + key are available.
"""
import os

import pytest

from src.eval.xbrl.golden import DEFAULT_GOLDEN_PATH, NumericGrader, load_golden_set

from .conftest import requires_backends

pytestmark = [pytest.mark.integration, requires_backends]

# Observed baseline is 12/13 = 92.3%; the threshold leaves headroom for LLM
# routing variance on the compute/ratio cases.
MIN_ACCURACY = 0.80

# Deterministic headline lookups that must always resolve exactly.
SUBSET_IDS = {"nvda-rev-2026", "aapl-rev-2024", "googl-rev-2023"}


def _grade(pipeline, cases):
    return NumericGrader(cases).grade(lambda q: pipeline.generate(q))


def test_headline_lookups_score_exactly(pipeline):
    cases = [c for c in load_golden_set(DEFAULT_GOLDEN_PATH) if c.id in SUBSET_IDS]
    assert cases, "expected the subset cases to exist in the golden set"
    report = _grade(pipeline, cases)
    failed = [r.to_dict() for r in report.results if not r.passed]
    assert report.passed == report.total, f"headline lookups regressed: {failed}"


@pytest.mark.skipif(
    os.environ.get("RUN_GOLDEN_EVAL") != "1",
    reason="full golden-set eval is heavy; set RUN_GOLDEN_EVAL=1 to run",
)
def test_full_golden_set_accuracy(pipeline):
    cases = load_golden_set(DEFAULT_GOLDEN_PATH)
    report = _grade(pipeline, cases)
    failed = [r.to_dict() for r in report.results if not r.passed]
    assert report.accuracy >= MIN_ACCURACY, (
        f"golden-set accuracy {report.accuracy:.1%} < {MIN_ACCURACY:.0%}; "
        f"failures={failed}"
    )
