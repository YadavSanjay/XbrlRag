"""Wiring of a value step's period into the executor and the verifier prompt (unlocked work of
docs/specs/verifier-period-evidence.md, Wiring 5).

The executor sets ``StepResult.period`` on an OK lookup, compute and aggregate, and on no
non-OK step; ``to_dict()`` emits it; the registry resolves the verifier prompt v3, whose
placeholders are the ones ``_build_messages`` passes.
"""

from __future__ import annotations

import string

from prompts.prompt_registry import get_xbrl_registry
from src.retriever.xbrl.plan_executor import (
    PlanExecutor,
    StepResult,
    StepStatus,
    first_reference_order,
)
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactCandidate,
    FactLookupResult,
    LookupStatus,
)

# step id -> (fy, value, fact_id, stored start, stored end): Apple revenue FY2024 / FY2023.
FACTS = {
    "r24": (2024, 391.035e9, 14262, "2023-10-01", "2024-09-29"),
    "r23": (2023, 383.285e9, 12096, "2022-09-25", "2023-10-01"),
}
R24 = {"fiscal_year": 2024, "fiscal_period": "FY", "ytd": False,
       "period_start": "2023-10-01", "period_end": "2024-09-28"}
R23 = {"fiscal_year": 2023, "fiscal_period": "FY", "ytd": False,
       "period_start": "2022-09-25", "period_end": "2023-09-30"}


def _candidate(fact_id: int, start: str, end: str) -> FactCandidate:
    return FactCandidate(
        fact_id=fact_id, value=None, unit="USD", period_type="duration", period_start=start,
        period_end=end, concept="us-gaap:Revenues", concept_family="Revenue",
        concept_pref_rank=1, segment_axis=None, segment_member=None, segment_label=None,
        section_path=None, citation=Citation(fact_id=fact_id),
    )


class _Store:
    """A lookup store answering FACTS; ``missing`` step ids are NOT_FOUND."""

    def __init__(self, missing: tuple[str, ...] = ()):
        self.missing = missing

    def lookup(self, step):
        if step["id"] in self.missing:
            return FactLookupResult(step_id=step["id"], status=LookupStatus.NOT_FOUND)
        _, value, fid, start, end = FACTS[step["id"]]
        other = _candidate(fid + 1, "2020-01-01", "2020-07-01")
        return FactLookupResult(
            step_id=step["id"], status=LookupStatus.OK, value=value, unit="USD",
            citation=Citation(fact_id=fid), confidence=0.9,
            candidates=[other, _candidate(fid, start, end)],
        )


def _lookup(sid: str) -> dict:
    return {"id": sid, "op": "lookup", "cik": 320193, "ticker": "AAPL", "fy": FACTS[sid][0],
            "concept_family": "Revenue", "fiscal_period": "FY", "dimensions": "NONE"}


# r23 is listed first in the plan, but the expression references r24 first.
GROWTH_PLAN = {"steps": [_lookup("r23"), _lookup("r24"),
                         {"id": "g", "op": "compute", "expr": "(r24 - r23) / r23"}]}


def _screen(step: dict) -> StepResult:
    rows = [{"ticker": t, "fact_id": str(fid), "value": v, "unit": "USD", "rank": i,
             "fiscal_year": 2023, "period_end": end}
            for i, (t, fid, v, end) in enumerate(
                [("INTC", 154795, 43.269e9, "2023-12-30"), ("MU", 55674, 21.244e9, "2023-08-31"),
                 ("TXN", 182004, 15.122e9, "2023-12-31")], start=1)]
    return StepResult(step_id=step["id"], op="screen", status=StepStatus.OK, rows=rows,
                      disclosure={"fiscal_year": 2023, "members": 3,
                                  "ranked": ["INTC", "MU", "TXN"],
                                  "universe": "semiconductors", "exclusions": []})


def test_ok_lookup_and_compute_carry_their_period():
    execution = PlanExecutor(factstore_lookup=_Store()).execute(GROWTH_PLAN)
    assert execution.step("r24").period == R24
    assert execution.step("r23").to_dict()["period"] == R23
    period = execution.step("g").to_dict()["period"]
    # first-reference order (r24 then r23), not plan or sorted order
    assert period == {"inputs": [{"step_id": "r24", **R24}, {"step_id": "r23", **R23}],
                      "fiscal_years": [2023, 2024]}


def test_non_ok_steps_carry_no_period():
    execution = PlanExecutor(factstore_lookup=_Store(missing=("r23",))).execute(GROWTH_PLAN)
    for sid in ("r23", "g"):
        step = execution.step(sid)
        assert step.status is not StepStatus.OK
        assert step.period is None and "period" not in step.to_dict()
    assert execution.step("r24").period == R24


def test_ok_aggregate_carries_its_screens_period():
    plan = {"steps": [{"id": "s", "op": "screen", "metric": "AssetsCurrent", "fy": 2023},
                      {"id": "m", "op": "aggregate", "of": "s", "fn": "median"}]}
    execution = PlanExecutor(factstore_lookup=_Store(), screen_fn=_screen).execute(plan)
    agg = execution.step("m")
    assert agg.status is StepStatus.OK
    assert agg.to_dict()["period"]["fiscal_year"] == 2023
    assert set(agg.period) == {"fiscal_year", "period_ends", "period_ends_differ"}
    assert "period" not in execution.step("s").to_dict()


def test_first_reference_order():
    assert first_reference_order("(r24 - r23) / r23") == ["r24", "r23"]
    assert first_reference_order("b / a + a") == ["b", "a"]
    assert first_reference_order("") == [] and first_reference_order("(") == []


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_registry_resolves_verifier_v3_with_the_call_sites_placeholders():
    prompt = get_xbrl_registry().resolve("faithfulness_verifier", provider="openai",
                                         series="gpt-5")
    assert prompt.ref == "_base/faithfulness_verifier/v3"
    assert _placeholders(prompt.system.template) == {"llm_answer_format"}
    assert _placeholders(prompt.user.template) == {"statements_json"}
    assert '"period"' in prompt.system.template
    v2 = get_xbrl_registry().resolve("faithfulness_verifier", provider="openai",
                                     series="gpt-5", version="v2")
    assert '"period"' not in v2.system.template
    assert v2.user.template == prompt.user.template
