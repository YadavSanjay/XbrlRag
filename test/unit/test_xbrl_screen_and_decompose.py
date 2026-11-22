"""Unit tests for the cross-filer `screen` op + compound-query decomposition.

Covers the offline (no-LLM, no-DB) surfaces added for the NUMERIC_SCREEN intent
and dependent decomposition:

- PlanValidator._validate_screen (screen op shapes an LLM would plausibly emit
  for scripts/xbrl/analyst_queries.sql 4c / 5c / 7a).
- BranchDispatcher: NUMERIC_SCREEN routes to PLAN_GENERATOR without degrading on
  a missing company.
- XbrlFaithfulnessVerifier._index_fact_steps: per-row screen fact indexing.
- QueryDecomposer parse/order/passthrough (LLM call stubbed out).
- XbrlRetrievalPipeline dependency injection + top-company extraction + merge.
"""
import pytest

from src.pipelines.xbrl.retrieval_pipeline import DependencyCompanies, XbrlRetrievalPipeline
from src.retriever.xbrl.branch_dispatcher import BRANCH, BranchDispatcher
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse
from src.retriever.xbrl.plan_validator import PlanValidator
from src.retriever.xbrl.query_decomposer import QueryDecomposer, SubQuery

# ---------------------------------------------------------------------------
# PlanValidator: screen op
# ---------------------------------------------------------------------------

class TestScreenValidation:
    @pytest.fixture
    def validator(self) -> PlanValidator:
        return PlanValidator()

    def test_geographic_value_screen_ok(self, validator):
        """analyst_queries.sql 4c: 'Which companies have China revenue exposure?'"""
        plan = {"steps": [{
            "id": "cn", "op": "screen", "concept_family": "Revenue", "fy": 2024,
            "dimension": {"geography": "China"}, "metric_kind": "value",
            "order": "desc", "top_n": None, "min_threshold": None,
        }]}
        assert validator.validate(plan) == []

    def test_derived_screen_allows_null_concept_family(self, validator):
        """A derived:<Metric> screen needs no concept_family (analyst 7a)."""
        plan = {"steps": [{
            "id": "gm", "op": "screen", "concept_family": None, "fy": 2024,
            "dimension": "NONE", "metric_kind": "derived:GrossMarginPct",
            "order": "desc", "top_n": 3,
        }]}
        assert validator.validate(plan) == []

    def test_synonym_derived_metric_is_accepted(self, validator):
        """The planner wrote "derived:NetProfitMargin" for NetMarginPct and the screen
        returned nothing (docs/specs/screen-metric-names.md); it now resolves."""
        plan = {"steps": [{
            "id": "nm", "op": "screen", "concept_family": None, "fy": 2025,
            "dimension": "NONE", "metric_kind": "derived:NetProfitMargin",
            "order": "desc", "top_n": 5,
        }]}
        assert validator.validate(plan) == []

    def test_unknown_derived_metric_is_rejected(self, validator):
        plan = {"steps": [{
            "id": "x", "op": "screen", "concept_family": None, "fy": 2025,
            "dimension": "NONE", "metric_kind": "derived:ProfitabilityScore",
            "order": "desc", "top_n": 5,
        }]}
        errors = validator.validate(plan)
        assert any("unknown derived metric 'ProfitabilityScore'" in e for e in errors)

    def test_missing_concept_family_for_value_kind(self, validator):
        plan = {"steps": [{
            "id": "s1", "op": "screen", "fy": 2024, "dimension": "NONE",
            "metric_kind": "value", "order": "desc",
        }]}
        errors = validator.validate(plan)
        assert any("concept_family" in e for e in errors)

    def test_bad_metric_kind_and_order(self, validator):
        plan = {"steps": [{
            "id": "s1", "op": "screen", "concept_family": "Revenue", "fy": 2024,
            "dimension": "NONE", "metric_kind": "nope", "order": "sideways",
        }]}
        errors = validator.validate(plan)
        assert any("metric_kind" in e for e in errors)
        assert any("order" in e for e in errors)

    def test_non_integer_fy(self, validator):
        plan = {"steps": [{
            "id": "s1", "op": "screen", "concept_family": "Revenue", "fy": "2024",
            "dimension": "NONE", "metric_kind": "value", "order": "desc",
        }]}
        assert any("'fy' must be an integer" in e for e in validator.validate(plan))


# ---------------------------------------------------------------------------
# BranchDispatcher: NUMERIC_SCREEN never degrades on a missing company
# ---------------------------------------------------------------------------

class TestScreenDispatch:
    def _dispatcher(self) -> BranchDispatcher:
        # skip __init__ (needs an IntentRouter/LLM) — decide_from_result is pure.
        return BranchDispatcher.__new__(BranchDispatcher)

    def test_numeric_screen_goes_to_plan_generator(self):
        result = IntentRouterResult(
            intent=XBRL_INTENT.NUMERIC_SCREEN, companies=[], periods=[],
            concept_candidates=["China revenue"], dimension_hint_present=True,
        )
        decision = self._dispatcher().decide_from_result("which companies...", result)
        assert decision.branch == BRANCH.PLAN_GENERATOR
        assert decision.degraded is False

    def test_numeric_lookup_without_company_still_degrades(self):
        """Contrast: a plain NUMERIC_LOOKUP with no company still degrades."""
        result = IntentRouterResult(
            intent=XBRL_INTENT.NUMERIC_LOOKUP, companies=[], periods=[],
            concept_candidates=["revenue"], dimension_hint_present=False,
        )
        decision = self._dispatcher().decide_from_result("what was revenue", result)
        assert decision.branch == BRANCH.NARRATIVE_BRANCH
        assert decision.degraded is True


# ---------------------------------------------------------------------------
# Verifier: per-row screen evidence indexing
# ---------------------------------------------------------------------------

class TestScreenFactIndexing:
    def test_rows_indexed_by_fact_id_with_context(self):
        execution_result = {"steps": [{
            "step_id": "cn", "op": "screen", "status": "OK", "rows": [
                {"ticker": "NVDA", "fact_id": "61392", "value": 7.1e9, "unit": "USD",
                 "fiscal_year": 2022, "pct_of_total": 0.13, "citation": {"fact_id": 61392}},
                {"ticker": "META", "fact_id": "40385", "value": 1.8e10, "unit": "USD",
                 "fiscal_year": 2024, "pct_of_total": 0.03, "citation": {"fact_id": 40385}},
            ],
        }]}
        _, by_fact = XbrlFaithfulnessVerifier._index_fact_steps(execution_result)
        assert set(by_fact) == {"61392", "40385"}
        assert by_fact["61392"]["value"] == 7.1e9
        assert by_fact["40385"]["ticker"] == "META"
        # unique per-row step_id so multi-row citations aren't deduped away
        assert by_fact["61392"]["step_id"] != by_fact["40385"]["step_id"]

    def test_resolve_fact_evidence_surfaces_screen_context(self):
        # A cited screen row brings its row entry and one entry with the screen's summary
        # (docs/specs/verifier-derived-disclosures.md, Requirement 4).
        by_step, by_fact = XbrlFaithfulnessVerifier._index_fact_steps({"steps": [{
            "step_id": "cn", "op": "screen", "status": "OK", "rows": [
                {"ticker": "NVDA", "fact_id": "61392", "value": 7.1e9, "unit": "USD",
                 "fiscal_year": 2022, "pct_of_total": 0.13, "citation": {"fact_id": 61392},
                 "rank": 1, "period_end": "2022-01-30"},
            ],
        }]})
        ev = XbrlFaithfulnessVerifier._resolve_fact_evidence(["61392"], by_step, by_fact)
        assert len(ev) == 2
        assert ev[0]["ticker"] == "NVDA"
        assert ev[0]["fiscal_year"] == 2022
        assert ev[0]["pct_of_total"] == 0.13
        assert ev[0]["rank"] == 1 and ev[0]["period_end"] == "2022-01-30"
        assert "screen" not in ev[0]
        assert ev[1]["step_id"] == "cn" and ev[1]["op"] == "screen"
        assert ev[1]["screen"]["ranked"] == ["NVDA"]
        assert ev[1]["screen"]["period_ends"] == {"NVDA": "2022-01-30"}


# ---------------------------------------------------------------------------
# QueryDecomposer: parsing / ordering / passthrough (LLM stubbed)
# ---------------------------------------------------------------------------

class TestDecomposerParsing:
    def _decomposer(self) -> QueryDecomposer:
        d = QueryDecomposer.__new__(QueryDecomposer)  # skip __init__ (no LLM)
        d.max_subqueries = 5
        return d

    def test_dependency_is_ordered_first(self):
        d = self._decomposer()
        raw = (
            '{"subqueries": ['
            '{"id": "sq2", "query": "For <<TOP:sq1:3>>, risks?", "depends_on": ["sq1"]},'
            '{"id": "sq1", "query": "Which companies?", "depends_on": []}]}'
        )
        subs = d._order_by_dependency(d._parse(raw, "orig"))
        assert [s.id for s in subs] == ["sq1", "sq2"]
        assert subs[1].top_refs == [("sq1", 3)]

    def test_invalid_json_passes_through(self):
        d = self._decomposer()
        subs = d._parse("not json at all", "ORIGINAL QUERY")
        assert len(subs) == 1 and subs[0].query == "ORIGINAL QUERY"

    def test_empty_subqueries_passes_through(self):
        d = self._decomposer()
        subs = d._parse('{"subqueries": []}', "ORIGINAL")
        assert len(subs) == 1 and subs[0].query == "ORIGINAL"


# ---------------------------------------------------------------------------
# Pipeline: dependency injection / top-company extraction / merge
# ---------------------------------------------------------------------------

class TestPipelineDecomposition:
    def test_inject_top_companies_with_ranks(self):
        sq = SubQuery(id="sq2", query="For <<TOP:sq1:3>>, risks?", depends_on=["sq1"])
        rows = [{"ticker": t} for t in ("AAPL", "TSLA", "META", "NVDA")]
        out = XbrlRetrievalPipeline._inject_dependencies(sq, {"sq1": DependencyCompanies(screen_rows=rows)})
        assert out == "For AAPL (#1), TSLA (#2), META (#3), risks?"

    def test_inject_top_one_stays_bare(self):
        sq = SubQuery(id="sq2", query="For <<TOP:sq1:1>>, risks?", depends_on=["sq1"])
        rows = [{"ticker": "AAPL"}, {"ticker": "TSLA"}]
        out = XbrlRetrievalPipeline._inject_dependencies(sq, {"sq1": DependencyCompanies(screen_rows=rows)})
        assert out == "For AAPL, risks?"

    def test_inject_lookup_fallback_has_no_labels(self):
        sq = SubQuery(id="sq2", query="For <<TOP:sq1:2>>, risks?", depends_on=["sq1"])
        dep = DependencyCompanies(screen_rows=[{"ticker": ""}], fallback_tickers=["AAPL", "TSLA", "META"])
        out = XbrlRetrievalPipeline._inject_dependencies(sq, {"sq1": dep})
        assert out == "For AAPL, TSLA, risks?"

    def test_inject_missing_dependency_is_graceful(self):
        sq = SubQuery(id="sq2", query="For <<TOP:sq1:3>>, risks?", depends_on=["sq1"])
        out = XbrlRetrievalPipeline._inject_dependencies(sq, {})
        assert out == "For the top companies identified above, risks?"

    def test_extract_top_companies_keeps_the_screen_order(self):
        # The screen numbers its rows in list order, so they are never re-sorted by `rank`.
        rows = [{"ticker": "META", "rank": 3}, {"ticker": "AAPL", "rank": 1}, {"ticker": "TSLA", "rank": 2}]
        resp = XbrlRagResponse(
            query="q", answer_summary="", answer_details=[],
            plan={"steps": [{"op": "lookup", "ticker": "NVDA"}]},
            execution_result={"steps": [{"op": "screen", "rows": rows}]},
        )
        dep = XbrlRetrievalPipeline._extract_top_companies(resp)
        assert dep.screen_rows == rows
        assert dep.fallback_tickers == ["NVDA"]

    def test_extract_top_companies_without_a_screen(self):
        resp = XbrlRagResponse(
            query="q", answer_summary="", answer_details=[],
            plan={"steps": [{"op": "lookup", "ticker": "AAPL"}, {"op": "lookup", "ticker": "AAPL"},
                            {"op": "lookup", "ticker": "TSLA"}]},
            execution_result={"steps": [{"op": "lookup"}]},
        )
        dep = XbrlRetrievalPipeline._extract_top_companies(resp)
        assert dep.screen_rows is None
        assert dep.fallback_tickers == ["AAPL", "TSLA"]

    def test_merge_responses_concatenates_and_ands_fully_answered(self):
        r1 = XbrlRagResponse(
            query="screen q", answer_summary="AAPL leads.",
            answer_details=[{"statement": "AAPL", "fact_ids": ["1"], "chunk_ids": []}],
            metadata={"fully_answered": True}, plan={}, execution_result={})
        r2 = XbrlRagResponse(
            query="narrative q", answer_summary="Supply risk.",
            answer_details=[{"statement": "risk", "fact_ids": [], "chunk_ids": ["c1"]}],
            metadata={"fully_answered": False}, plan={}, execution_result={})
        subs = [SubQuery("sq1", "screen q", []), SubQuery("sq2", "narrative q", ["sq1"])]
        pipeline = XbrlRetrievalPipeline.__new__(XbrlRetrievalPipeline)
        merged = pipeline._merge_responses("compound", subs, {"sq1": r1, "sq2": r2})
        assert merged.metadata["decomposed"] is True
        assert merged.metadata["fully_answered"] is False  # AND of sub-answers
        assert len(merged.answer_details) == 2
        assert len(merged.metadata["subqueries"]) == 2

    def test_merge_responses_tags_each_statement_with_its_subquery(self):
        # Step ids repeat across sub-queries (the fast path's f1), so each merged statement
        # names the sub-query its ids resolve in, and keeps the ids unchanged.
        r1 = XbrlRagResponse(
            query="a", answer_summary="A.",
            answer_details=[{"statement": "A", "fact_ids": ["f1"], "chunk_ids": []}],
            metadata={"fully_answered": True}, plan={}, execution_result={"steps": []})
        r2 = XbrlRagResponse(
            query="b", answer_summary="B.",
            answer_details=[{"statement": "B", "fact_ids": ["f1", "42"], "chunk_ids": ["c"]}],
            metadata={"fully_answered": True}, plan={}, execution_result={"steps": []})
        subs = [SubQuery("sq1", "a", []), SubQuery("sq2", "b", [])]
        pipeline = XbrlRetrievalPipeline.__new__(XbrlRetrievalPipeline)
        merged = pipeline._merge_responses("compound", subs, {"sq1": r1, "sq2": r2})
        assert merged.answer_details == [
            {"statement": "A", "fact_ids": ["f1"], "chunk_ids": [], "subquery_id": "sq1"},
            {"statement": "B", "fact_ids": ["f1", "42"], "chunk_ids": ["c"],
             "subquery_id": "sq2"},
        ]
        assert "subquery_id" not in r1.answer_details[0]  # the sub-answer is not mutated
        assert merged.execution_result["subqueries"]["sq2"] is r2.execution_result


# ---------------------------------------------------------------------------
# Derived metric names (docs/specs/screen-metric-names.md)
# ---------------------------------------------------------------------------

class TestDerivedMetricNames:
    def test_every_alias_targets_a_known_metric(self):
        from src.retriever.xbrl.xbrl_factstore_screen import (
            DERIVED_METRIC_ALIASES,
            DERIVED_METRICS,
            normalize_metric_name,
        )

        assert set(DERIVED_METRIC_ALIASES.values()) <= DERIVED_METRICS
        # An alias key must not shadow a real metric's normalised name.
        real = {normalize_metric_name(m) for m in DERIVED_METRICS}
        assert not set(DERIVED_METRIC_ALIASES) & real

    def test_names_mirror_the_view(self):
        """DERIVED_METRICS must list exactly mv_derived_metrics' metric names."""
        from src.retriever.xbrl.xbrl_factstore_screen import DERIVED_METRICS

        try:
            import psycopg2

            from src.db.conn import conn_params

            with psycopg2.connect(**conn_params()) as conn, conn.cursor() as cur:
                cur.execute("SELECT DISTINCT metric_name FROM mv_derived_metrics")
                live = {r[0] for r in cur.fetchall()}
        except Exception as exc:  # no reachable DB: nothing to compare against
            pytest.skip(f"xbrl_db not reachable: {exc}")
        assert DERIVED_METRICS == live
