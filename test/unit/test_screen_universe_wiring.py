"""Wiring of the screen universe disclosure (docs/specs/screen-universe.md)."""

from __future__ import annotations

from pathlib import Path

from src.retriever.xbrl.plan_executor import StepResult, StepStatus
from src.retriever.xbrl.screen_disclosure import append_screen_disclosures
from src.retriever.xbrl.xbrl_factstore_screen import universe_groups

REPO = Path(__file__).resolve().parents[2]
PLAN_PROMPT = (
    REPO / "prompts" / "templates" / "xbrl" / "_base" / "plan_generator" / "v7" / "system.txt"
)

DISCLOSURE = {
    "universe": "retailers", "fiscal_year": 2023, "members": 4, "ranked": ["WMT", "AMZN"],
    "exclusions": [{"ticker": "COST", "reason": "metric_not_tagged"}],
    "period_ends": {"WMT": "2024-01-31", "AMZN": "2023-12-31"}, "period_ends_differ": False,
}


def test_step_result_emits_disclosure_only_when_set():
    with_d = StepResult(step_id="s1", op="screen", status=StepStatus.OK, disclosure=DISCLOSURE)
    without = StepResult(step_id="s1", op="screen", status=StepStatus.OK)
    assert with_d.to_dict()["disclosure"] == DISCLOSURE
    assert "disclosure" not in without.to_dict()


def test_step_dicts_feed_the_appended_sentence():
    step = StepResult(step_id="s1", op="screen", status=StepStatus.OK, disclosure=DISCLOSURE)
    text = append_screen_disclosures("Walmart ranked first.", [step.to_dict()])
    assert text == (
        "Walmart ranked first. Universe: the retailers group (4 companies; 2 ranked for "
        "FY2023). Not ranked: COST (metric not reported)."
    )


def test_plan_prompt_names_every_group_id():
    # The planner can only set a universe it is told about; a group added to groups.yaml
    # needs a prompt version that lists it.
    prompt = PLAN_PROMPT.read_text(encoding="utf-8")
    for gid in universe_groups():
        assert f'"{gid}"' in prompt, gid
