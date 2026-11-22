"""An unqualified share count is the period-end shares outstanding.

The fast path maps it deterministically; plan_generator v9 states the same convention
for multi-step plans. A weighted-average, basic or diluted count stays off the fast path.
"""

from pathlib import Path

import pytest

from src.retriever.xbrl.intent_router import IntentRouter

_PLAN_V9 = (
    Path(__file__).resolve().parents[2]
    / "prompts/templates/xbrl/_base/plan_generator/v9/system.txt"
)


@pytest.mark.parametrize(
    "phrase",
    ["share count", "Shares count", "number of shares", "number of shares outstanding",
     "total share count", "shares outstanding"],
)
def test_unqualified_share_count_is_shares_outstanding(phrase: str) -> None:
    assert IntentRouter._match_concept_family(phrase) == "SharesOutstanding"


@pytest.mark.parametrize(
    "phrase",
    ["diluted share count", "basic share count", "weighted average share count",
     "weighted average shares", "diluted weighted average shares"],
)
def test_qualified_share_count_is_not_fast_pathed(phrase: str) -> None:
    assert IntentRouter._match_concept_family(phrase) is None


def test_plan_generator_v9_states_the_convention() -> None:
    text = _PLAN_V9.read_text(encoding="utf-8")
    assert "share count with no qualifier" in text
    assert "SharesOutstanding, an instant figure" in text
    # The qualified families keep their own entries.
    assert "- WeightedAverageSharesDiluted (includes: diluted weighted average shares" in text
