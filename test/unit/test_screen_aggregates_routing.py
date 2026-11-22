"""Wiring of group routing (docs/specs/screen-aggregates.md, Part A; unlocked work).

The locked spec tests cover the pure rules; these cover how the router, the plan
generator and the pipeline use them. The router's LLM reply is faked at `_do_llm_call`
and the entity resolver (SQL on entity_master) by a fake object; the rules themselves run
for real, over the committed groups file.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouter, IntentRouterResult
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.universe_routing import group_plan_errors

WMT_CIK = "0000104169"


class _FakeResolver:
    """Stands in for EntityExtractor (an SQL boundary): a fixed name -> filer table."""

    FILERS = {"walmart": ("WMT", WMT_CIK, "Walmart Inc."),
              "costco": ("COST", "0000909832", "Costco Wholesale Corp."),
              "aapl": ("AAPL", "0000320193", "Apple Inc.")}

    def __init__(self):
        self.seen: list[list[str]] = []

    def resolve_companies(self, mentions):
        self.seen.append(list(mentions))
        out = []
        for m in mentions:
            hit = self.FILERS.get(m.lower())
            if hit is None:
                out.append(ResolvedCompany(mention=m, resolution="unresolved"))
            else:
                out.append(ResolvedCompany(mention=m, ticker=hit[0], cik=hit[1],
                                           company_name=hit[2], resolution="ticker"))
        return out

    def resolve_periods(self, mentions, companies):
        return [ResolvedPeriod(mention=m, fiscal_year=2024) for m in mentions]


def _route(query, reply):
    resolver = _FakeResolver()
    router = IntentRouter(api_key="sk-test", entity_extractor=resolver)
    with patch.object(router, "_do_llm_call", return_value=json.dumps(reply)):
        return router.route(query), resolver


def _reply(intent, companies=(), periods=("fiscal 2024",), concepts=("capex",)):
    return {"intent": intent, "company_mentions": list(companies),
            "period_mentions": list(periods), "concept_phrases": list(concepts),
            "dimension_hint_present": False}


def test_a_group_lifted_as_a_company_is_routed_to_a_screen():
    result, resolver = _route(
        "What was the combined capex of the big tech companies in fiscal 2024?",
        _reply("NUMERIC_COMPUTE", companies=["the big tech companies"]),
    )
    assert resolver.seen == [[]]  # the group phrase never reached company resolution
    assert result.companies == []
    assert result.intent is XBRL_INTENT.NUMERIC_SCREEN
    assert result.universe == "big_tech" and result.aggregate == "sum"
    assert result.plan is None  # the plan generator runs
    assert result.to_dict()["universe"] == "big_tech"
    assert result.to_dict()["aggregate"] == "sum"


def test_a_group_in_the_question_routes_a_narrative_reply_with_a_cue():
    result, _ = _route("Median R&D of the semiconductor companies in fiscal 2024",
                       _reply("NARRATIVE", concepts=["R&D"]))
    assert result.intent is XBRL_INTENT.NUMERIC_SCREEN
    assert result.universe == "semiconductors" and result.aggregate == "median"


def test_a_peer_question_keeps_the_router_intent():
    result, _ = _route("Where does Walmart rank among the retailers on capex in fiscal 2024?",
                       _reply("NUMERIC_COMPUTE", companies=["Walmart"]))
    assert result.intent is XBRL_INTENT.NUMERIC_COMPUTE
    assert [c.ticker for c in result.companies] == ["WMT"]
    assert result.universe == "retailers" and result.aggregate is None


@pytest.mark.parametrize("query", [
    "What was the combined capex of Walmart and Costco in fiscal 2024?",
    "What is the median capex of Walmart and Costco in fiscal 2024?",
])
def test_a_cue_over_named_companies_stays_a_compute_plan(query):
    # A cue over named companies is not a group question: no aggregate is required, so
    # the planner's lookup + compute plan passes group_plan_errors on the first try.
    result, _ = _route(query, _reply("NUMERIC_COMPUTE", companies=["Walmart", "Costco"]))
    assert result.intent is XBRL_INTENT.NUMERIC_COMPUTE
    assert [c.ticker for c in result.companies] == ["WMT", "COST"]
    assert result.universe is None and result.aggregate is None
    compute_plan = {"steps": [
        {"id": "s1", "op": "lookup"}, {"id": "s2", "op": "lookup"},
        {"id": "s3", "op": "compute", "expr": "s1 + s2"}]}
    assert group_plan_errors(compute_plan, result.universe, result.aggregate) == []


def test_a_cue_with_a_company_and_a_group_requires_no_aggregate():
    result, _ = _route(
        "What was the combined capex of Walmart and the retailers in fiscal 2024?",
        _reply("NUMERIC_COMPUTE", companies=["Walmart"]),
    )
    assert result.intent is XBRL_INTENT.NUMERIC_COMPUTE
    assert result.universe == "retailers" and result.aggregate is None


def test_no_group_changes_nothing():
    result, _ = _route("What was Walmart's capex in fiscal 2024?",
                       _reply("NUMERIC_LOOKUP", companies=["Walmart"]))
    assert result.intent is XBRL_INTENT.NUMERIC_LOOKUP
    assert result.universe is None and result.aggregate is None
    assert result.plan is not None  # the one-step fast path still applies


def test_the_routing_shows_in_the_intent_extraction_event():
    resolver = _FakeResolver()
    router = IntentRouter(api_key="sk-test", entity_extractor=resolver)
    reply = _reply("NUMERIC_COMPUTE")
    with patch.object(router, "_do_llm_call", return_value=json.dumps(reply)), \
            patch("src.retriever.xbrl.intent_router.log_event") as log:
        router.route("Combined capex of the retailers, fiscal 2024")
    extraction = [c.kwargs for c in log.call_args_list if c.args == ("intent_extraction",)]
    assert extraction[0]["universe"] == "retailers"
    assert extraction[0]["aggregate"] == "sum"
    assert extraction[0]["intent"] == "NUMERIC_SCREEN"


# ------------------------------------------------- group sum cue (docs/specs/group-sum-cue.md)
def _universe_routing_events(log):
    return [c.kwargs for c in log.call_args_list
            if c.args == ("decision",) and c.kwargs.get("point") == "universe_routing"]


def test_total_over_a_named_group_routes_a_screen_with_a_sum():
    resolver = _FakeResolver()
    router = IntentRouter(api_key="sk-test", entity_extractor=resolver)
    reply = _reply("NUMERIC_LOOKUP", concepts=["interest expense"])
    with patch.object(router, "_do_llm_call", return_value=json.dumps(reply)), \
            patch("src.retriever.xbrl.intent_router.log_event") as log:
        result = router.route("total interest expense, the retailers, fiscal year 2024")
    assert result.intent is XBRL_INTENT.NUMERIC_SCREEN
    assert result.universe == "retailers" and result.aggregate == "sum"
    assert result.plan is None  # the planner runs, and group_plan_errors requires the sum
    events = _universe_routing_events(log)
    assert len(events) == 1
    assert events[0]["aggregate"] == "sum" and events[0]["aggregate_rule"] == "total"
    assert events[0]["decision"] == "route_to_screen"


def test_add_up_over_a_named_group_is_logged_when_the_llm_already_chose_a_screen():
    resolver = _FakeResolver()
    router = IntentRouter(api_key="sk-test", entity_extractor=resolver)
    reply = _reply("NUMERIC_SCREEN", concepts=["revenue"])
    with patch.object(router, "_do_llm_call", return_value=json.dumps(reply)), \
            patch("src.retriever.xbrl.intent_router.log_event") as log:
        result = router.route("If you add up the retailers, how much revenue in fiscal 2024?")
    assert result.intent is XBRL_INTENT.NUMERIC_SCREEN
    assert result.universe == "retailers" and result.aggregate == "sum"
    events = _universe_routing_events(log)
    assert len(events) == 1
    assert events[0]["aggregate_rule"] == "add_up" and events[0]["decision"] == "keep_intent"


def test_total_for_a_named_company_is_not_a_group_sum():
    result, resolver = _route("AAPL total revenue fiscal 2024",
                              _reply("NUMERIC_LOOKUP", companies=["AAPL"],
                                     concepts=["total revenue"]))
    assert resolver.seen == [["AAPL"]]
    assert result.intent is XBRL_INTENT.NUMERIC_LOOKUP
    assert [c.ticker for c in result.companies] == ["AAPL"]
    assert result.universe is None and result.aggregate is None
    assert result.plan is not None  # the one-step lookup fast path still applies


# ------------------------------------------------------------------- plan generator
GROUP_RESULT = IntentRouterResult(
    intent=XBRL_INTENT.NUMERIC_SCREEN,
    periods=[ResolvedPeriod(mention="fiscal 2024", fiscal_year=2024)],
    concept_candidates=["capex"],
    universe="retailers",
    aggregate="sum",
)
SCREEN = {"id": "s1", "op": "screen", "fy": 2024, "dimension": "NONE", "metric_kind": "value",
          "order": "desc", "concept_family": "Capex", "top_n": None, "universe": "retailers"}
GOOD_PLAN = {"steps": [SCREEN, {"id": "a1", "op": "aggregate", "of": "s1", "fn": "sum"}]}
NO_AGGREGATE_PLAN = {"steps": [SCREEN]}


def test_generator_retries_a_plan_without_the_cued_aggregate():
    generator = PlanGenerator(api_key="sk-test", max_retries=1)
    replies = [json.dumps(NO_AGGREGATE_PLAN), json.dumps(GOOD_PLAN)]
    with patch.object(generator, "_do_llm_call", side_effect=replies) as call:
        plan = generator.generate_plan("Combined capex of the retailers, FY2024", GROUP_RESULT)
    assert plan == GOOD_PLAN
    assert call.call_count == 2
    retry_prompt = call.call_args_list[1].args[0][1]["content"][0]["text"]
    assert "VALIDATION_FEEDBACK" in retry_prompt and "aggregate" in retry_prompt


def test_generator_fails_validation_when_the_group_is_never_screened():
    generator = PlanGenerator(api_key="sk-test", max_retries=1)
    covered = {"steps": [dict(SCREEN, universe=None),
                         {"id": "a1", "op": "aggregate", "of": "s1", "fn": "sum"}]}
    with patch.object(generator, "_do_llm_call", return_value=json.dumps(covered)), \
            patch("src.retriever.xbrl.plan_generator.escalation_ladder",
                  side_effect=lambda stage, ref, settings=None: [ref]):
        plan = generator.generate_plan("Combined capex of the retailers, FY2024", GROUP_RESULT)
    assert plan["error"] == "plan_validation_failed"
    assert "universe" in plan["reason"] and "retailers" in plan["reason"]


def test_generator_prompt_carries_the_universe_and_cue():
    generator = PlanGenerator(api_key="sk-test")
    with patch.object(generator, "_do_llm_call", return_value=json.dumps(GOOD_PLAN)) as call:
        generator.generate_plan("Combined capex of the retailers, FY2024", GROUP_RESULT)
    user = call.call_args_list[0].args[0][1]["content"][0]["text"]
    assert 'UNIVERSE: "retailers"' in user and 'AGGREGATE: "sum"' in user


# ------------------------------------------------------------------- pipeline
AGG = {"fn": "sum", "status": "OK", "value": 7.0, "unit": "USD", "n": 2,
       "members": ["WMT", "AMZN"], "fact_ids": ["1", "2"], "middle": [],
       "universe": "retailers", "universe_members": 4, "exclusions": [],
       "period_ends": {}, "period_ends_differ": False, "error": None}
DISCLOSURE = {"universe": "retailers", "fiscal_year": 2024, "members": 4,
              "ranked": ["WMT", "AMZN"],
              "exclusions": [{"ticker": "COST", "reason": "metric_not_tagged"},
                             {"ticker": "HD", "reason": "metric_not_tagged"}],
              "period_ends": {}, "period_ends_differ": False}


@pytest.mark.parametrize("order", ["screen_first", "aggregate_first"])
def test_pipeline_appends_screen_then_aggregate_sentences(order):
    screen = {"step_id": "s1", "op": "screen", "status": "OK", "disclosure": DISCLOSURE}
    aggregate = {"step_id": "a1", "op": "aggregate", "status": "OK", "aggregate": AGG}
    steps = [screen, aggregate] if order == "screen_first" else [aggregate, screen]
    text = XbrlRetrievalPipeline._append_disclosures("Total was $7.", steps)
    universe_at = text.index("Universe: the retailers group")
    total_at = text.index("Total of 2 companies: WMT, AMZN.")
    assert text.startswith("Total was $7. ") and universe_at < total_at
