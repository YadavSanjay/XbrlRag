"""Unit tests for PlanGenerator (design doc §2.2.3).

All LLM calls are mocked via `_do_llm_call` - these tests validate the
surrounding harness (RESOLVED_ENTITIES injection, validation, retry-with-
feedback, error passthrough), not the LLM itself. Scenarios mirror
scripts/xbrl/analyst_queries.sql.
"""
import json
from unittest.mock import patch

import pytest

from src.llm.profiles import load_committed_settings
from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult
from src.retriever.xbrl.plan_generator import PlanGenerator

NVDA_CIK = "0001045810"
GOOGL_CIK = "0001652044"


@pytest.fixture
def generator() -> PlanGenerator:
    return PlanGenerator(api_key="sk-test")


@pytest.fixture
def nvda_data_center_vs_gaming_result() -> IntentRouterResult:
    """analyst_queries.sql 3a/3d: 'NVDA Data Center revenue vs Gaming in FY2026?'"""
    return IntentRouterResult(
        intent=XBRL_INTENT.NUMERIC_COMPUTE,
        companies=[
            ResolvedCompany(
                mention="NVIDIA", cik=NVDA_CIK, ticker="NVDA", company_name="NVIDIA Corporation",
                fye_month=1, fye_day=28, last_seen_fy=2026, resolution="fuzzy_name", similarity=0.9,
            )
        ],
        periods=[ResolvedPeriod(mention="FY2026", fiscal_year=2026)],
        concept_candidates=["data center revenue", "gaming revenue"],
        dimension_hint_present=True,
    )


@pytest.fixture
def googl_rd_yoy_result() -> IntentRouterResult:
    """analyst_queries.sql-style: YoY R&D growth for Alphabet FY2022 -> FY2023."""
    return IntentRouterResult(
        intent=XBRL_INTENT.NUMERIC_COMPUTE,
        companies=[
            ResolvedCompany(
                mention="Google", cik=GOOGL_CIK, ticker="GOOGL", company_name="Alphabet Inc.",
                fye_month=12, fye_day=31, last_seen_fy=2024, resolution="fuzzy_name", similarity=0.8,
            )
        ],
        periods=[ResolvedPeriod(mention="FY2022", fiscal_year=2022), ResolvedPeriod(mention="FY2023", fiscal_year=2023)],
        concept_candidates=["R&D expense"],
        dimension_hint_present=False,
    )


VALID_DC_VS_GAMING_PLAN = {
    "steps": [
        {
            "id": "dc26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
            "fy": 2026, "concept_family": "Revenue", "dimensions": {"product": "data center"},
            "section_hint": "Disaggregated Revenue",
        },
        {
            "id": "gm26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
            "fy": 2026, "concept_family": "Revenue", "dimensions": {"product": "gaming"},
            "section_hint": "Disaggregated Revenue",
        },
        {"id": "diff", "op": "compute", "expr": "dc26 - gm26"},
    ]
}

VALID_RD_YOY_PLAN = {
    "steps": [
        {
            "id": "rd22", "op": "lookup", "cik": GOOGL_CIK, "ticker": "GOOGL",
            "fy": 2022, "concept_family": "ResearchAndDevelopment", "dimensions": "NONE",
            "section_hint": "Income Statement",
        },
        {
            "id": "rd23", "op": "lookup", "cik": GOOGL_CIK, "ticker": "GOOGL",
            "fy": 2023, "concept_family": "ResearchAndDevelopment", "dimensions": "NONE",
            "section_hint": "Income Statement",
        },
        {"id": "growth", "op": "compute", "expr": "(rd23 - rd22) / rd22"},
    ]
}

# Invalid: duplicate step ids
INVALID_DUPLICATE_ID_PLAN = {
    "steps": [
        {
            "id": "rd22", "op": "lookup", "cik": GOOGL_CIK, "ticker": "GOOGL",
            "fy": 2022, "concept_family": "ResearchAndDevelopment", "dimensions": "NONE",
            "section_hint": "Income Statement",
        },
        {
            "id": "rd22", "op": "lookup", "cik": GOOGL_CIK, "ticker": "GOOGL",
            "fy": 2023, "concept_family": "ResearchAndDevelopment", "dimensions": "NONE",
            "section_hint": "Income Statement",
        },
    ]
}

ERROR_OBJECT_RESPONSE = {
    "error": "Period mismatch: query asks for calendar 2024 but filer's FYE is Jan 25.",
    "user_query": "...",
    "reason": "fiscal vs calendar mismatch",
    "suggestion": "Clarify fiscal vs calendar year.",
}


class TestGeneratePlanHappyPath:
    def test_valid_plan_returned_as_is(self, generator, nvda_data_center_vs_gaming_result):
        with patch.object(generator, "_do_llm_call", return_value=json.dumps(VALID_DC_VS_GAMING_PLAN)) as mock_call:
            plan = generator.generate_plan(
                "What was NVDA's Data Center revenue vs Gaming in FY2026?",
                nvda_data_center_vs_gaming_result,
            )

        assert plan == VALID_DC_VS_GAMING_PLAN
        mock_call.assert_called_once()

    def test_error_object_passthrough_without_validation(self, generator, googl_rd_yoy_result):
        with patch.object(generator, "_do_llm_call", return_value=json.dumps(ERROR_OBJECT_RESPONSE)) as mock_call:
            plan = generator.generate_plan("calendar 2024 R&D for Alphabet", googl_rd_yoy_result)

        assert plan == ERROR_OBJECT_RESPONSE
        mock_call.assert_called_once()  # no retry for the prompt's own error contract

    def test_invalid_json_response(self, generator, googl_rd_yoy_result):
        with patch.object(generator, "_do_llm_call", return_value="not json"):
            plan = generator.generate_plan("Alphabet R&D growth", googl_rd_yoy_result)

        assert plan["error"] == "invalid_json_response"
        assert plan["user_query"] == "Alphabet R&D growth"


class TestResolvedEntitiesInjection:
    def test_resolved_entities_block_contains_cik_and_fye(self, generator, nvda_data_center_vs_gaming_result):
        with patch.object(generator, "_do_llm_call", return_value=json.dumps(VALID_DC_VS_GAMING_PLAN)) as mock_call:
            generator.generate_plan(
                "What was NVDA's Data Center revenue vs Gaming in FY2026?",
                nvda_data_center_vs_gaming_result,
            )

        messages = mock_call.call_args[0][0]
        user_text = messages[1]["content"][0]["text"]

        assert f"NVDA (cik={NVDA_CIK})" in user_text
        assert "fye=01-28" in user_text
        assert "FISCAL_YEARS_IN_SCOPE" in user_text
        assert "2026" in user_text
        assert "data center revenue" in user_text
        assert '"dimension_hint_present": true' in user_text.lower() or "DIMENSION_HINT_PRESENT: true" in user_text

    def test_unresolved_company_is_flagged_in_prompt(self, generator):
        result = IntentRouterResult(
            intent=XBRL_INTENT.NUMERIC_LOOKUP,
            companies=[ResolvedCompany(mention="SomeObscureCo", resolution="ambiguous", similarity=0.1)],
            periods=[ResolvedPeriod(mention="fiscal 2024", fiscal_year=2024)],
            concept_candidates=["revenue"],
            dimension_hint_present=False,
        )
        with patch.object(generator, "_do_llm_call", return_value=json.dumps(VALID_RD_YOY_PLAN)):
            generator.generate_plan("SomeObscureCo revenue in FY2024", result)
        # construct via internal helper directly too, for a focused assertion
        block = generator._format_resolved_entities(result)
        assert "SomeObscureCo: UNRESOLVED" in block

    def test_system_prompt_is_raw_taxonomy_text(self, generator, googl_rd_yoy_result):
        """The static plan-generator system prompt must be passed through verbatim
        (it contains literal JSON braces and must never go through .format())."""
        with patch.object(generator, "_do_llm_call", return_value=json.dumps(VALID_RD_YOY_PLAN)) as mock_call:
            generator.generate_plan("Alphabet R&D YoY growth FY22 to FY23", googl_rd_yoy_result)

        messages = mock_call.call_args[0][0]
        system_text = messages[0]["content"][0]["text"]
        assert "CONCEPT_FAMILIES" in system_text
        assert "FUZZY INTENT HINTS" in system_text


class TestRetryOnValidationFailure:
    def test_retry_then_success(self, generator, googl_rd_yoy_result):
        responses = [json.dumps(INVALID_DUPLICATE_ID_PLAN), json.dumps(VALID_RD_YOY_PLAN)]
        with patch.object(generator, "_do_llm_call", side_effect=responses) as mock_call:
            plan = generator.generate_plan(
                "What was the YoY growth in Google's R&D expense from FY2022 to FY2023?",
                googl_rd_yoy_result,
            )

        assert plan == VALID_RD_YOY_PLAN
        assert mock_call.call_count == 2

        second_call_messages = mock_call.call_args_list[1][0][0]
        retry_user_text = second_call_messages[1]["content"][0]["text"]
        assert "VALIDATION_FEEDBACK" in retry_user_text
        assert "not unique" in retry_user_text

    def test_max_retries_exhausted_returns_error(self, googl_rd_yoy_result):
        generator = PlanGenerator(api_key="sk-test", max_retries=1)
        with patch.object(generator, "_do_llm_call", return_value=json.dumps(INVALID_DUPLICATE_ID_PLAN)) as mock_call:
            plan = generator.generate_plan(
                "What was the YoY growth in Google's R&D expense from FY2022 to FY2023?",
                googl_rd_yoy_result,
            )

        assert plan["error"] == "plan_validation_failed"
        assert "not unique" in plan["reason"]
        # A7: after the base-model validate-retry loop exhausts (1 + max_retries = 2
        # calls), plan_validation_failed triggers a one-tier escalation that re-runs
        # the whole loop on the frontier model (2 more calls).
        assert mock_call.call_count == 4

    def test_zero_retries_still_escalates_once_before_error(self, googl_rd_yoy_result):
        generator = PlanGenerator(api_key="sk-test", max_retries=0)
        with patch.object(generator, "_do_llm_call", return_value=json.dumps(INVALID_DUPLICATE_ID_PLAN)) as mock_call:
            plan = generator.generate_plan("Alphabet R&D YoY growth", googl_rd_yoy_result)

        assert plan["error"] == "plan_validation_failed"
        # A7: 1 base call + 1 frontier escalation call (max_retries=0 => one per rung).
        assert mock_call.call_count == 2


class TestFiscalYearsInScope:
    def test_collects_unique_sorted_years(self, generator, googl_rd_yoy_result):
        years = generator._fiscal_years_in_scope(googl_rd_yoy_result)
        assert years == [2022, 2023]

    def test_empty_when_no_periods_resolved(self, generator):
        result = IntentRouterResult(intent=XBRL_INTENT.NUMERIC_COMPUTE)
        assert generator._fiscal_years_in_scope(result) == []


# ---------------------------------------------------------------------------
# A7 — model escalation on plan_validation_failed
# ---------------------------------------------------------------------------

@pytest.fixture
def events():
    """Capture `xbrlrag.events` records (the logger has propagate=False)."""
    import logging
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)


class TestPlanEscalation:
    def test_validation_failure_escalates_to_frontier_then_succeeds(self, googl_rd_yoy_result, events):
        """The base (medium-tier) model keeps failing validation; after the loop
        exhausts, escalate to the fallback profile's frontier model, which returns a
        valid plan. A7 wiring, mirroring the synthesizer."""
        tiers = load_committed_settings()
        generator = PlanGenerator(api_key="sk-test", max_retries=0)
        # rung 0 (base): 1 invalid call -> plan_validation_failed -> escalate.
        # rung 1 (frontier): 1 valid call -> success.
        responses = [json.dumps(INVALID_DUPLICATE_ID_PLAN), json.dumps(VALID_RD_YOY_PLAN)]
        with patch.object(generator, "_do_llm_call", side_effect=responses) as mock_call:
            plan = generator.generate_plan("Alphabet R&D YoY growth", googl_rd_yoy_result)

        assert plan == VALID_RD_YOY_PLAN
        assert mock_call.call_count == 2
        # The escalated (2nd) call used the frontier model tier.
        assert mock_call.call_args_list[1].kwargs["model_ref"].model == tiers.model_for_tier(
            "frontier")

        esc = [e for e in events if e["kind"] == "model_escalation"]
        assert len(esc) == 1
        assert esc[0]["stage"] == "plan_generator"
        assert esc[0]["from_model"] == tiers.model_for_tier("medium")
        assert esc[0]["to_model"] == tiers.model_for_tier("frontier")
        assert esc[0]["reason"] == "plan_validation_failed"

    def test_valid_plan_on_base_does_not_escalate(self, googl_rd_yoy_result, events):
        generator = PlanGenerator(api_key="sk-test")
        with patch.object(generator, "_do_llm_call", return_value=json.dumps(VALID_RD_YOY_PLAN)) as mock_call:
            plan = generator.generate_plan("Alphabet R&D YoY growth", googl_rd_yoy_result)

        assert plan == VALID_RD_YOY_PLAN
        mock_call.assert_called_once()  # base rung only
        assert not [e for e in events if e["kind"] == "model_escalation"]

    def test_json_parse_error_does_not_escalate(self, googl_rd_yoy_result, events):
        """A JSON-parse failure is NOT an escalation trigger (only
        plan_validation_failed is) — it returns immediately on the base model."""
        generator = PlanGenerator(api_key="sk-test")
        with patch.object(generator, "_do_llm_call", return_value="not json") as mock_call:
            plan = generator.generate_plan("Alphabet R&D growth", googl_rd_yoy_result)

        assert plan["error"] == "invalid_json_response"
        mock_call.assert_called_once()
        assert not [e for e in events if e["kind"] == "model_escalation"]
