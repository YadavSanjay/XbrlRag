"""Wiring of the cagr op and the CAGR/ratio cues (docs/specs/plan-cagr-and-ratios.md).

The locked spec tests cover the pure rules, the validator, the executor and citation
integrity; these cover the unlocked wiring: the router's operation intent, the plan
generator's retry and prompt, the decomposer's passthrough, the pipeline's marking of a
missing asked value, the executor's lineage event and the verifier's evidence. LLM replies
are faked at `_do_llm_call` and the entity resolver (SQL) by a fake object. All figures are
synthetic (Example Co).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouter, IntentRouterResult
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse
from src.retriever.xbrl.plan_executor import PlanExecutor
from src.retriever.xbrl.plan_generator import PlanGenerator
from src.retriever.xbrl.query_decomposer import QueryDecomposer
from src.retriever.xbrl.xbrl_factstore_lookup import Citation, FactLookupResult, LookupStatus

CIK = "0000000001"
RATIO_Q = "Example Co Assets, Current / Liabilities, Current, FY2024"
CAGR_Q = "What was Example Co's revenue CAGR from FY2022 to FY2025?"


class _FakeResolver:
    """Stands in for EntityExtractor (an SQL boundary): Example Co only."""

    def resolve_companies(self, mentions):
        return [ResolvedCompany(mention=m, ticker="EXCO", cik=CIK, company_name="Example Co",
                                resolution="ticker") for m in mentions]

    def resolve_periods(self, mentions, companies):
        return [ResolvedPeriod(mention=m, fiscal_year=2024) for m in mentions]


def _reply(intent):
    return json.dumps({"intent": intent, "company_mentions": ["Example Co"],
                       "period_mentions": ["FY2024"], "concept_phrases": ["current assets"],
                       "dimension_hint_present": False})


# ------------------------------------------------------------------- router
def test_a_cued_lookup_is_routed_to_compute_with_a_decision_event():
    router = IntentRouter(api_key="sk-test", entity_extractor=_FakeResolver())
    with patch.object(router, "_do_llm_call", return_value=_reply("NUMERIC_LOOKUP")), \
            patch("src.retriever.xbrl.intent_router.log_event") as log:
        result = router.route(RATIO_Q)
    assert result.intent is XBRL_INTENT.NUMERIC_COMPUTE
    assert result.operation == "ratio" and result.to_dict()["operation"] == "ratio"
    assert result.plan is None  # never the one-step lookup fast path
    decisions = [c.kwargs for c in log.call_args_list
                 if c.args == ("decision",) and c.kwargs.get("point") == "operation_cue"]
    assert len(decisions) == 1
    assert decisions[0]["previous_intent"] == "NUMERIC_LOOKUP"
    assert decisions[0]["intent"] == "NUMERIC_COMPUTE"
    extraction = [c.kwargs for c in log.call_args_list if c.args == ("intent_extraction",)]
    assert extraction[0]["operation"] == "ratio"


def test_an_uncued_lookup_keeps_the_fast_path():
    router = IntentRouter(api_key="sk-test", entity_extractor=_FakeResolver())
    with patch.object(router, "_do_llm_call", return_value=_reply("NUMERIC_LOOKUP")):
        result = router.route("What were Example Co's current assets in FY2024?")
    assert result.intent is XBRL_INTENT.NUMERIC_LOOKUP
    assert result.operation is None


# ------------------------------------------------------------------- plan generator
def _lookup(sid, family, fy):
    return {"id": sid, "op": "lookup", "cik": CIK, "ticker": "EXCO", "fy": fy,
            "concept_family": family, "dimensions": "NONE", "section_hint": "Balance Sheet"}


RATIO_RESULT = IntentRouterResult(
    intent=XBRL_INTENT.NUMERIC_COMPUTE,
    periods=[ResolvedPeriod(mention="FY2024", fiscal_year=2024)],
    concept_candidates=["current assets", "current liabilities"],
    operation="ratio",
)
COMPONENTS = {"steps": [_lookup("ca", "CurrentAssets", 2024),
                        _lookup("cl", "CurrentLiabilities", 2024)]}
WITH_RATIO = {"steps": [*COMPONENTS["steps"],
                        {"id": "cr", "op": "compute", "expr": "ca / cl"}]}


def test_generator_retries_a_ratio_plan_without_the_division():
    generator = PlanGenerator(api_key="sk-test", max_retries=1)
    replies = [json.dumps(COMPONENTS), json.dumps(WITH_RATIO)]
    with patch.object(generator, "_do_llm_call", side_effect=replies) as call:
        plan = generator.generate_plan(RATIO_Q, RATIO_RESULT)
    assert plan == WITH_RATIO
    assert call.call_count == 2
    first = call.call_args_list[0].args[0][1]["content"][0]["text"]
    assert 'OPERATION: "ratio"' in first
    retry = call.call_args_list[1].args[0][1]["content"][0]["text"]
    assert "VALIDATION_FEEDBACK" in retry and "ratio" in retry and "compute" in retry


def test_generator_accepts_a_cagr_plan():
    result = IntentRouterResult(intent=XBRL_INTENT.NUMERIC_COMPUTE, operation="cagr")
    plan = {"steps": [_lookup("r22", "Revenue", 2022), _lookup("r25", "Revenue", 2025),
                      {"id": "g1", "op": "cagr", "start": "r22", "end": "r25"}]}
    generator = PlanGenerator(api_key="sk-test", max_retries=1)
    with patch.object(generator, "_do_llm_call", return_value=json.dumps(plan)) as call:
        assert generator.generate_plan(CAGR_Q, result) == plan
    assert call.call_count == 1


# ------------------------------------------------------------------- decomposer
def _decomposer():
    d = QueryDecomposer.__new__(QueryDecomposer)  # skip __init__ (no LLM)
    d.max_subqueries = 5
    return d


def _decompose(query, subqueries):
    d = _decomposer()
    raw = json.dumps({"subqueries": [{"id": f"sq{i + 1}", "query": q, "depends_on": []}
                                     for i, q in enumerate(subqueries)]})
    with patch.object(d, "_build_messages", return_value=[]), \
            patch.object(d, "_do_llm_call", return_value=raw), \
            patch("src.retriever.xbrl.query_decomposer.log_event") as log:
        return d.decompose(query), log


def test_decomposer_keeps_a_split_ratio_whole():
    subs, log = _decompose(RATIO_Q, ["Example Co current assets FY2024",
                                     "Example Co current liabilities FY2024"])
    assert [(s.id, s.query, s.depends_on) for s in subs] == [("sq1", RATIO_Q, [])]
    decisions = [c.kwargs for c in log.call_args_list if c.args == ("decision",)]
    assert decisions and decisions[0]["point"] == "query_decomposer"
    assert decisions[0]["operations"] == ["ratio"]


def test_decomposer_keeps_a_split_that_carries_the_operation():
    query = "What was Example Co's revenue CAGR from FY2022 to FY2025, and what drove it?"
    parts = ["What was Example Co's revenue CAGR from FY2022 to FY2025?",
             "What drove Example Co's revenue growth from FY2022 to FY2025?"]
    subs, log = _decompose(query, parts)
    assert [s.query for s in subs] == parts
    assert not [c for c in log.call_args_list if c.args == ("decision",)]


# ------------------------------------------------------------------- pipeline
def _components_response(query):
    def lookup(sid, value, fid):
        return {"step_id": sid, "op": "lookup", "status": "OK", "value": value,
                "unit": "USD", "citation": {"fact_id": fid}, "confidence": 0.9,
                "linked_chunk_ids": [], "error": None}

    return XbrlRagResponse(
        query=query,
        answer_summary="Current assets were $50.0 billion; current liabilities $40.0 billion.",
        answer_details=[{"statement": "Current assets were $50.0 billion.",
                         "fact_ids": [301], "chunk_ids": []}],
        unanswered_parts=[],
        metadata={"fully_answered": True},
        plan=COMPONENTS,
        execution_result={"status": "OK", "steps": [lookup("ca", 50e9, 301),
                                                    lookup("cl", 40e9, 302)]},
    )


def test_pipeline_marks_a_components_only_ratio_answer():
    with patch("src.pipelines.xbrl.retrieval_pipeline.log_event") as log:
        out = XbrlRetrievalPipeline._mark_missing_operation(
            RATIO_Q, _components_response(RATIO_Q))
    assert isinstance(out, XbrlRagResponse)
    assert out.metadata["fully_answered"] is False
    assert len(out.unanswered_parts) == 1 and "ratio" in out.unanswered_parts[0]["reason"]
    assert out.answer_details[0]["statement"] == "Current assets were $50.0 billion."
    assert [c.kwargs["point"] for c in log.call_args_list
            if c.args == ("decision",)] == ["operation_answer"]


def test_pipeline_leaves_an_uncued_answer_alone():
    query = "What were Example Co's current assets in FY2024?"
    response = _components_response(query)
    assert XbrlRetrievalPipeline._mark_missing_operation(query, response) is response


# ------------------------------------------------------------------- executor + verifier
class _Store:
    VALUES = {"r22": (80e9, 501), "r25": (106.48e9, 504)}

    def lookup(self, step):
        value, fid = self.VALUES[step["id"]]
        return FactLookupResult(step_id=step["id"], status=LookupStatus.OK, value=value,
                                unit="USD", citation=Citation(fact_id=fid), confidence=0.9)


CAGR_PLAN = {"steps": [_lookup("r22", "Revenue", 2022), _lookup("r25", "Revenue", 2025),
                       {"id": "g1", "op": "cagr", "start": "r22", "end": "r25"}]}


def test_cagr_lineage_event_names_inputs_years_and_facts():
    with patch("src.retriever.xbrl.plan_executor.log_event") as log:
        PlanExecutor(factstore_lookup=_Store()).execute(CAGR_PLAN)
    events = [c.kwargs for c in log.call_args_list
              if c.args == ("numeric_lineage",) and c.kwargs.get("op") == "cagr"]
    assert len(events) == 1
    event = events[0]
    assert event["start"] == "r22" and event["end"] == "r25" and event["years"] == 3
    assert event["source_fact_ids"] == [501, 504] and event["status"] == "OK"


def test_verifier_evidence_shows_a_cagr_record():
    execution = PlanExecutor(factstore_lookup=_Store()).execute(CAGR_PLAN).to_dict()
    by_step, by_fact = XbrlFaithfulnessVerifier._index_fact_steps(execution)
    evidence = XbrlFaithfulnessVerifier._resolve_fact_evidence(["g1"], by_step, by_fact)
    assert len(evidence) == 1
    record = evidence[0]["cagr"]
    assert record["years"] == 3 and record["start_fy"] == 2022 and record["end_fy"] == 2025
    assert record["fact_ids"] == [501, 504]
