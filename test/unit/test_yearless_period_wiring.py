"""Router wiring of yearless-period-mention (docs/specs/yearless-period-mention.md R3).

The locked spec tests cover the pure rule (`period_evidence.py`); these cover the router:
a mention naming no year is dropped from `IntentRouterResult.periods` when another period
resolved, a `decision` event records it, the raw extraction is untouched, and the dispatcher
then decides on the remaining periods. LLM replies are faked at `_do_llm_call` and the entity
resolver (SQL) by a fake object. All figures are synthetic (Example Co).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from src.retriever.xbrl.branch_dispatcher import BRANCH, BranchDispatcher
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouter

CIK = "0000000001"
YOY_Q = "What was the year-over-year percentage change in Example Co's Assets for fiscal 2024?"
LOOKUP_Q = "What were Example Co's total assets for fiscal 2024?"

# Mentions the fake resolver resolves; every other mention fails as the real
# EntityExtractor does for a mention without a year.
_RESOLVED = {"fiscal 2024": 2024, "FY2023": 2023}


class _FakeResolver:
    """Stands in for EntityExtractor (an SQL boundary): Example Co only."""

    def resolve_companies(self, mentions):
        return [ResolvedCompany(mention=m, ticker="EXCO", cik=CIK, company_name="Example Co",
                                resolution="ticker") for m in mentions]

    def resolve_periods(self, mentions, companies):
        periods = []
        for m in mentions:
            if m in _RESOLVED:
                periods.append(ResolvedPeriod(mention=m, fiscal_year=_RESOLVED[m]))
            else:
                periods.append(ResolvedPeriod(mention=m, ambiguous=True,
                                              reason="could_not_resolve_year"))
        return periods


def _reply(intent, period_mentions, concepts=("total assets",)):
    return json.dumps({"intent": intent, "company_mentions": ["Example Co"],
                       "period_mentions": list(period_mentions),
                       "concept_phrases": list(concepts), "dimension_hint_present": False})


def _route(query, reply):
    router = IntentRouter(api_key="sk-test", entity_extractor=_FakeResolver())
    with patch.object(router, "_do_llm_call", return_value=reply), \
            patch("src.retriever.xbrl.intent_router.log_event") as log:
        result = router.route(query)
    decision = BranchDispatcher(intent_router=router).decide_from_result(query, result)
    return result, decision, log


def _ignore_events(log):
    return [c.kwargs for c in log.call_args_list
            if c.args == ("decision",) and c.kwargs.get("point") == "period_resolution"]


def test_yearless_mention_beside_a_resolved_year_is_dropped_and_planned():
    result, decision, log = _route(
        YOY_Q, _reply("NUMERIC_COMPUTE", ["fiscal 2024", "year-over-year"]))
    assert [(p.mention, p.fiscal_year) for p in result.periods] == [("fiscal 2024", 2024)]
    assert result.raw_extraction["period_mentions"] == ["fiscal 2024", "year-over-year"]
    assert _ignore_events(log) == [{
        "point": "period_resolution", "decision": "ignore_yearless_mention",
        "mention": "year-over-year", "reason": "could_not_resolve_year",
    }]
    extraction = [c.kwargs for c in log.call_args_list if c.args == ("intent_extraction",)]
    assert [p["fiscal_year"] for p in extraction[0]["periods"]] == [2024]
    assert decision.branch is BRANCH.PLAN_GENERATOR
    assert not decision.degraded


def test_lookup_left_with_one_period_after_a_drop_skips_the_fast_path():
    # The dropped "year-over-year" still says the question spans years: a one-step FY2024
    # lookup would answer it with a single value, so the plan generator gets it instead.
    result, decision, log = _route(
        YOY_Q, _reply("NUMERIC_LOOKUP", ["fiscal 2024", "year-over-year"]))
    assert result.intent is XBRL_INTENT.NUMERIC_LOOKUP
    assert len(result.periods) == 1 and not result.periods[0].ambiguous
    assert result.plan is None
    assert decision.branch is BRANCH.PLAN_GENERATOR
    assert not decision.degraded
    assert len(_ignore_events(log)) == 1


def test_lookup_with_no_drop_keeps_the_fast_path():
    result, decision, log = _route(LOOKUP_Q, _reply("NUMERIC_LOOKUP", ["fiscal 2024"]))
    assert result.plan is not None
    assert result.plan["steps"][0]["fy"] == 2024
    assert decision.branch is BRANCH.FACT_BRANCH_DIRECT
    assert _ignore_events(log) == []


def test_yearless_mention_alone_still_degrades():
    result, decision, log = _route(YOY_Q, _reply("NUMERIC_COMPUTE", ["year-over-year"]))
    assert [p.mention for p in result.periods] == ["year-over-year"]
    assert decision.branch is BRANCH.NARRATIVE_BRANCH
    assert decision.degraded
    assert "year-over-year" in decision.reason
    assert _ignore_events(log) == []


def test_failed_mention_with_year_evidence_still_degrades():
    result, decision, log = _route(
        "What was Example Co's revenue in fiscal 2024 Q3?",
        _reply("NUMERIC_LOOKUP", ["fiscal 2024", "Q3"], concepts=("revenue",)))
    assert [p.mention for p in result.periods] == ["fiscal 2024", "Q3"]
    assert decision.branch is BRANCH.NARRATIVE_BRANCH
    assert decision.degraded
    assert "Q3" in decision.reason
    assert _ignore_events(log) == []


def test_last_fiscal_year_still_degrades():
    # The fake resolver fails it, as the real one does with no anchored company
    # (docs/specs/last-fiscal-year-phrase.md R3): a failed mention with year evidence
    # still degrades and is never dropped. With an anchor it resolves to last_seen_fy - 1.
    _, decision, log = _route(
        YOY_Q, _reply("NUMERIC_COMPUTE", ["FY2023", "last fiscal year"]))
    assert decision.branch is BRANCH.NARRATIVE_BRANCH
    assert _ignore_events(log) == []


def test_several_yearless_mentions_are_each_logged_in_order():
    result, decision, log = _route(
        YOY_Q, _reply("NUMERIC_COMPUTE", ["each year", "fiscal 2024", "annual filing"]))
    assert [p.mention for p in result.periods] == ["fiscal 2024"]
    assert [e["mention"] for e in _ignore_events(log)] == ["each year", "annual filing"]
    assert decision.branch is BRANCH.PLAN_GENERATOR
