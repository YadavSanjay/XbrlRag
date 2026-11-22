"""Tests for PlanExecutor (design doc §2.2 orchestration + lookup + compute branches).

- TestOrchestration: plan-level errors, unknown ops — pure, no database.
- TestComputeBranch: the sandboxed XbrlCalculatorTool wired into the `compute`
  op, including its error paths (division by zero, undefined ids, unsafe
  expressions) — mocked lookups, no database.
- TestStubbedNarrative: the still-unimplemented `narrative` branch.
- TestExecuteAgainstFactStore (requires_db): run real `lookup` + `compute`
  steps through the live xbrl_db, modeled on scripts/xbrl/analyst_queries.sql
  (YoY growth §2c, margin waterfall §7a/7b, free cash flow §6a/7d, capex
  intensity §6b, R&D intensity §7c) and the NVIDIA worked example in
  an NVIDIA worked example (Data Center vs total revenue).

Integration tests skip automatically if xbrl_db is unreachable.
"""
import json
import logging
from unittest.mock import MagicMock

import pytest

from src.retriever.xbrl.plan_executor import (
    ExecutionStatus,
    PlanExecutor,
    StepResult,
    StepStatus,
)
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactCandidate,
    FactLookupResult,
    LookupStatus,
    XbrlFactstoreLookup,
)


@pytest.fixture
def events():
    """Collect ``xbrlrag.events`` records (logger has propagate=False in prod config)."""
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)

NVDA = "0001045810"
AAPL = "0000320193"


def _db_available() -> bool:
    try:
        lk = XbrlFactstoreLookup.from_env()
        with lk._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM mv_financial_facts LIMIT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="xbrl_db not reachable")


def _ok_lookup(step_id, value, fact_id=1):
    return FactLookupResult(
        step_id=step_id, status=LookupStatus.OK, value=value, unit="USD",
        citation=Citation(fact_id=fact_id, html_anchor=f"#{step_id}"), confidence=0.9,
        linked_chunk_ids=[f"chunk-{step_id}"],
    )


def _make_executor(lookup_results, **kwargs):
    """Build a PlanExecutor whose factstore returns canned results by step id."""
    factstore = MagicMock()
    factstore.lookup.side_effect = lambda step: lookup_results[step["id"]]
    return PlanExecutor(factstore_lookup=factstore, **kwargs)


# NVIDIA worked example: Data Center vs total revenue + a compute share step.
DC_TOT_SHARE_PLAN = {
    "steps": [
        {"id": "dc26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue",
         "dimensions": {"product": "data center"}, "section_hint": "Disaggregated Revenue"},
        {"id": "tot26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue",
         "dimensions": "NONE", "section_hint": "Income Statement"},
        {"id": "share26", "op": "compute", "expr": "dc26 / tot26"},
    ]
}


# ---------------------------------------------------------------------------
# Plan-level orchestration (no DB)
# ---------------------------------------------------------------------------

class TestOrchestration:
    def _executor(self, **kwargs):
        return PlanExecutor(factstore_lookup=MagicMock(), **kwargs)

    def test_error_plan_object(self):
        result = self._executor().execute({"error": "plan_validation_failed", "reason": "..."})
        assert result.status == ExecutionStatus.ERROR
        assert "plan_validation_failed" in result.error

    def test_empty_steps(self):
        assert self._executor().execute({"steps": []}).status == ExecutionStatus.ERROR

    def test_not_a_dict(self):
        assert self._executor().execute([1, 2, 3]).status == ExecutionStatus.ERROR

    def test_unknown_op(self):
        plan = {"steps": [{"id": "z", "op": "aggregate", "expr": "sum(x)"}]}
        result = self._executor().execute(plan)
        assert result.step("z").status == StepStatus.ERROR


# ---------------------------------------------------------------------------
# Compute branch: sandboxed calculator wired into the executor (no DB)
# ---------------------------------------------------------------------------

class TestComputeBranch:
    def test_simple_ratio(self):
        results = {
            "dc26": _ok_lookup("dc26", 115186000000.0, 2001),
            "tot26": _ok_lookup("tot26", 130497000000.0, 1001),
        }
        executor = _make_executor(results)
        result = executor.execute(DC_TOT_SHARE_PLAN)

        assert result.status == ExecutionStatus.OK
        share = result.step("share26")
        assert share.status == StepStatus.OK
        assert share.value == pytest.approx(115186000000.0 / 130497000000.0)

    def test_yoy_growth_expression(self):
        results = {
            "rev25": _ok_lookup("rev25", 130497000000.0),
            "rev26": _ok_lookup("rev26", 215938000000.0),
        }
        plan = {
            "steps": [
                {"id": "rev25", "op": "lookup", "cik": NVDA, "fy": 2025, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "rev26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "growth", "op": "compute", "expr": "(rev26 - rev25) / rev25"},
            ]
        }
        result = _make_executor(results).execute(plan)
        assert result.status == ExecutionStatus.OK
        assert result.bindings["growth"] == pytest.approx(0.65473535790094791451)

    def test_growth_from_a_negative_base_is_not_meaningful(self):
        """Micron FY2023 -> FY2024 operating income (-5,745M -> 1,304M): the formula
        gives -122.7% for a large improvement, so the step reports n/m, no value."""
        results = {
            "op23": _ok_lookup("op23", -5_745_000_000.0),
            "op24": _ok_lookup("op24", 1_304_000_000.0),
        }
        plan = {
            "steps": [
                {"id": "op23", "op": "lookup", "cik": "0000723125", "fy": 2023, "concept_family": "OperatingIncome", "dimensions": "NONE"},
                {"id": "op24", "op": "lookup", "cik": "0000723125", "fy": 2024, "concept_family": "OperatingIncome", "dimensions": "NONE"},
                {"id": "growth", "op": "compute", "expr": "(op24 - op23) / op23"},
            ]
        }
        result = _make_executor(results).execute(plan)

        growth = result.step("growth")
        assert growth.status == StepStatus.NOT_MEANINGFUL
        assert growth.value is None
        assert "n/m" in growth.error
        assert growth.to_dict()["status"] == "NOT_MEANINGFUL"  # what the synthesizer sees
        assert result.bindings["growth"] is None
        assert result.status == ExecutionStatus.PARTIAL  # the lookups are still OK

    def test_division_by_zero_is_step_error(self):
        results = {"a": _ok_lookup("a", 10.0), "b": _ok_lookup("b", 0.0)}
        plan = {
            "steps": [
                {"id": "a", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "b", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "CostOfRevenue", "dimensions": "NONE"},
                {"id": "ratio", "op": "compute", "expr": "a / b"},
            ]
        }
        result = _make_executor(results).execute(plan)
        ratio = result.step("ratio")
        assert ratio.status == StepStatus.ERROR
        assert "division by zero" in ratio.error
        assert result.status == ExecutionStatus.PARTIAL  # the two lookups still OK

    def test_unresolved_dependency_value_is_step_error(self):
        """A lookup that returned no value (NOT_FOUND) leaves bindings[id]=None;
        a compute step referencing it must fail cleanly, not raise TypeError."""
        results = {
            "a": _ok_lookup("a", 10.0),
            "b": FactLookupResult("b", LookupStatus.NOT_FOUND),
        }
        plan = {
            "steps": [
                {"id": "a", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "b", "op": "lookup", "cik": NVDA, "fy": 2019, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "sum_ab", "op": "compute", "expr": "a + b"},
            ]
        }
        result = _make_executor(results).execute(plan)
        sum_step = result.step("sum_ab")
        assert sum_step.status == StepStatus.ERROR
        assert "no value" in sum_step.error

    def test_undefined_identifier_is_step_error(self):
        results = {"a": _ok_lookup("a", 10.0)}
        plan = {
            "steps": [
                {"id": "a", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "bad", "op": "compute", "expr": "a + nonexistent_step"},
            ]
        }
        result = _make_executor(results).execute(plan)
        assert result.step("bad").status == StepStatus.ERROR
        assert "nonexistent_step" in result.step("bad").error

    @pytest.mark.parametrize("malicious_expr", [
        "__import__('os').system('echo pwned')",
        "(1).__class__.__bases__[0]",
        "[x for x in ().__class__.__base__.__subclasses__()]",
        "a if True else b",
        "open('/etc/passwd').read()",
        "2 ** 999999",
        "a == b",
    ])
    def test_unsafe_expressions_are_rejected(self, malicious_expr):
        """Defense-in-depth: even if a malformed expr slipped past PlanValidator,
        the executor's calculator must reject it, not execute it."""
        results = {"a": _ok_lookup("a", 10.0), "b": _ok_lookup("b", 5.0)}
        plan = {
            "steps": [
                {"id": "a", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "b", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "CostOfRevenue", "dimensions": "NONE"},
                {"id": "evil", "op": "compute", "expr": malicious_expr},
            ]
        }
        result = _make_executor(results).execute(plan)
        evil = result.step("evil")
        assert evil.status == StepStatus.ERROR
        assert evil.value is None

    def test_injected_compute_fn_overrides_default(self):
        def compute_fn(step, bindings):
            return StepResult(step_id=step["id"], op="compute", status=StepStatus.OK, value=42.0)

        results = {"a": _ok_lookup("a", 1.0), "b": _ok_lookup("b", 1.0)}
        plan = {
            "steps": [
                {"id": "a", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "b", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "CostOfRevenue", "dimensions": "NONE"},
                {"id": "c", "op": "compute", "expr": "a / b"},
            ]
        }
        result = _make_executor(results, compute_fn=compute_fn).execute(plan)
        assert result.bindings["c"] == 42.0


# ---------------------------------------------------------------------------
# Narrative branch remains a stub (no DB)
# ---------------------------------------------------------------------------

class TestStubbedNarrative:
    def test_narrative_is_not_implemented_by_default(self):
        executor = PlanExecutor(factstore_lookup=MagicMock())
        plan = {"steps": [{"id": "ctx", "op": "narrative", "section_hint": "MD&A"}]}
        result = executor.execute(plan)
        assert result.step("ctx").status == StepStatus.NOT_IMPLEMENTED
        assert result.status == ExecutionStatus.PARTIAL  # stub keeps the plan from being "OK"

    def test_injected_narrative_fn_is_used(self):
        def narrative_fn(step):
            return StepResult(step_id=step["id"], op="narrative", status=StepStatus.OK, value=None)

        executor = PlanExecutor(factstore_lookup=MagicMock(), narrative_fn=narrative_fn)
        plan = {"steps": [{"id": "ctx", "op": "narrative", "section_hint": "MD&A"}]}
        result = executor.execute(plan)
        assert result.step("ctx").status == StepStatus.OK


# ---------------------------------------------------------------------------
# Real lookups + compute through the fact store (requires xbrl_db)
# ---------------------------------------------------------------------------

@requires_db
class TestExecuteAgainstFactStore:
    @pytest.fixture(scope="class")
    def executor(self):
        return PlanExecutor(factstore_lookup=XbrlFactstoreLookup.from_env())

    def test_worked_example_data_center_share_of_revenue(self, executor):
        """Plan_Extractor doc §2 worked example, end to end (lookup + compute)."""
        result = executor.execute(DC_TOT_SHARE_PLAN)

        assert result.bindings["dc26"] == pytest.approx(193737000000.0)
        assert result.bindings["tot26"] == pytest.approx(215938000000.0)
        assert result.step("share26").status == StepStatus.OK
        assert result.bindings["share26"] == pytest.approx(193737000000.0 / 215938000000.0)
        assert result.status == ExecutionStatus.OK

    def test_yoy_revenue_growth(self, executor):
        """analyst_queries.sql 2c — NVDA revenue YoY growth FY2025 -> FY2026."""
        plan = {
            "steps": [
                {"id": "rev25", "op": "lookup", "cik": NVDA, "fy": 2025, "concept_family": "Revenue",
                 "dimensions": "NONE", "section_hint": "Income Statement"},
                {"id": "rev26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue",
                 "dimensions": "NONE", "section_hint": "Income Statement"},
                {"id": "growth", "op": "compute", "expr": "(rev26 - rev25) / rev25"},
            ]
        }
        result = executor.execute(plan)
        assert result.status == ExecutionStatus.OK
        # cross-checked against mv_yoy_comparison.yoy_pct_change for fact_id_current=1224
        assert result.bindings["growth"] == pytest.approx(0.65473535790094791451)

    def test_gross_and_operating_margin(self, executor):
        """analyst_queries.sql 7a/7b — margin waterfall via lookup + compute,
        cross-checked against mv_derived_metrics (GrossMarginPct, OperatingMarginPct)."""
        plan = {
            "steps": [
                {"id": "rev26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "cogs26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "CostOfRevenue", "dimensions": "NONE"},
                {"id": "oi26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "OperatingIncome", "dimensions": "NONE"},
                {"id": "gross_margin", "op": "compute", "expr": "(rev26 - cogs26) / rev26"},
                {"id": "op_margin", "op": "compute", "expr": "oi26 / rev26"},
            ]
        }
        result = executor.execute(plan)
        assert result.status == ExecutionStatus.OK
        assert result.bindings["gross_margin"] == pytest.approx(0.71068084357547073697)
        assert result.bindings["op_margin"] == pytest.approx(0.60381683631412720318)

    def test_free_cash_flow(self, executor):
        """analyst_queries.sql 6a/7d — FreeCashFlow = CashFromOperations - Capex."""
        plan = {
            "steps": [
                {"id": "ocf26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "CashFromOperations", "dimensions": "NONE"},
                {"id": "capex26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Capex", "dimensions": "NONE"},
                {"id": "fcf", "op": "compute", "expr": "ocf26 - capex26"},
            ]
        }
        result = executor.execute(plan)
        assert result.status == ExecutionStatus.OK
        assert result.bindings["fcf"] == pytest.approx(96676000000.0)

    def test_rnd_intensity(self, executor):
        """analyst_queries.sql 7c — R&D as a percentage of revenue."""
        plan = {
            "steps": [
                {"id": "rd26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "ResearchAndDevelopment", "dimensions": "NONE"},
                {"id": "rev26", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "rnd_pct", "op": "compute", "expr": "rd26 / rev26"},
            ]
        }
        result = executor.execute(plan)
        assert result.status == ExecutionStatus.OK
        assert result.bindings["rnd_pct"] == pytest.approx(18497000000.0 / 215938000000.0)

    def test_capex_intensity_aapl(self, executor):
        """analyst_queries.sql 6b — capex / revenue for AAPL FY2024."""
        plan = {
            "steps": [
                {"id": "capex24", "op": "lookup", "cik": AAPL, "fy": 2024, "concept_family": "Capex", "dimensions": "NONE"},
                {"id": "rev24", "op": "lookup", "cik": AAPL, "fy": 2024, "concept_family": "Revenue", "dimensions": "NONE"},
                {"id": "intensity", "op": "compute", "expr": "capex24 / rev24"},
            ]
        }
        result = executor.execute(plan)
        assert result.status == ExecutionStatus.OK
        assert result.bindings["intensity"] == pytest.approx(9447000000.0 / 391035000000.0)

    def test_lookup_failure_propagates_as_partial(self, executor):
        plan = {
            "steps": [
                {"id": "ok", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue",
                 "dimensions": "NONE", "section_hint": "Income Statement"},
                {"id": "gone", "op": "lookup", "cik": NVDA, "fy": 2019, "concept_family": "Revenue",
                 "dimensions": "NONE", "section_hint": "Income Statement"},
            ]
        }
        result = executor.execute(plan)
        assert result.step("ok").status == StepStatus.OK
        assert result.step("gone").status == StepStatus.NOT_FOUND
        assert result.status == ExecutionStatus.PARTIAL


# ---------------------------------------------------------------------------
# Numeric lineage (design doc R1 / §4.5) — audit trail for every number
# ---------------------------------------------------------------------------

class TestNumericLineage:
    def _lineage(self, events, op=None):
        out = [e for e in events if e["kind"] == "numeric_lineage"]
        return [e for e in out if op is None or e["op"] == op]

    def test_lookup_lineage_emits_fact_id_and_confidence(self, events):
        executor = _make_executor({"rev": _ok_lookup("rev", 100.0, fact_id=4242)})
        plan = {"steps": [
            {"id": "rev", "op": "lookup", "cik": NVDA, "fy": 2026,
             "concept_family": "Revenue", "dimensions": "NONE"},
        ]}
        executor.execute(plan)

        lookups = self._lineage(events, "lookup")
        assert len(lookups) == 1
        ev = lookups[0]
        assert ev["step_id"] == "rev"
        assert ev["fact_id"] == 4242
        assert ev["value"] == 100.0
        assert ev["confidence"] == 0.9
        assert ev["cik"] == NVDA and ev["fy"] == 2026
        assert ev["concept_family"] == "Revenue"

    def test_compute_lineage_links_source_facts(self, events):
        executor = _make_executor({
            "dc26": _ok_lookup("dc26", 60.0, fact_id=11),
            "tot26": _ok_lookup("tot26", 200.0, fact_id=22),
        })
        executor.execute(DC_TOT_SHARE_PLAN)

        computes = self._lineage(events, "compute")
        assert len(computes) == 1
        ev = computes[0]
        assert ev["step_id"] == "share26"
        assert ev["expr"] == "dc26 / tot26"
        assert ev["result"] == pytest.approx(0.3)
        assert ev["bindings"] == {"dc26": 60.0, "tot26": 200.0}
        # every input is traceable back to the exact source fact
        assert ev["source_fact_ids"] == {"dc26": 11, "tot26": 22}
        assert ev["source_steps"] == {"dc26": "lookup", "tot26": "lookup"}

    def test_failed_compute_still_emits_lineage(self, events):
        executor = _make_executor({
            "a": _ok_lookup("a", 5.0, fact_id=1),
            "b": _ok_lookup("b", 0.0, fact_id=2),
        })
        plan = {"steps": [
            {"id": "a", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
            {"id": "b", "op": "lookup", "cik": NVDA, "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE"},
            {"id": "ratio", "op": "compute", "expr": "a / b"},
        ]}
        result = executor.execute(plan)

        assert result.step("ratio").status == StepStatus.ERROR
        compute_ev = self._lineage(events, "compute")[0]
        assert compute_ev["status"] == "ERROR"
        assert compute_ev["result"] is None
        assert "division by zero" in compute_ev["error"]
        # inputs are still attributed even though the computation failed
        assert compute_ev["source_fact_ids"] == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Intersection fallback (docs/specs/multidim-recovery.md) — the trace side
# ---------------------------------------------------------------------------

AVGO = "0001730168"
AVGO_SPLIT_PLAN = {"steps": [
    {"id": "ap", "op": "lookup", "cik": AVGO, "fy": 2024, "concept_family": "Revenue",
     "dimensions": {"geography": "asia pacific"}},
    {"id": "subs", "op": "lookup", "cik": AVGO, "fy": 2024, "concept_family": "Revenue",
     "dimensions": {"product": "subscriptions and services"}},
]}


def _cell_lookup(step_id, labels, status=LookupStatus.OK):
    candidate = FactCandidate(
        fact_id=25183, value=2.203e9, unit="USD", period_type="duration",
        period_start=None, period_end="2024-11-03", concept="us-gaap:Revenues",
        concept_family="Revenue", concept_pref_rank=1, segment_axis=None,
        segment_member=None, segment_label=None, section_path=None,
        citation=Citation(fact_id=25183), dimension_labels=labels,
    )
    return FactLookupResult(
        step_id=step_id, status=status, value=2.203e9, unit="USD",
        citation=Citation(fact_id=25183), confidence=0.8, candidates=[candidate],
    )


class TestIntersectionFallback:
    def _results(self, cell):
        return {
            "ap": FactLookupResult(step_id="ap", status=LookupStatus.AMBIGUOUS),
            "subs": _ok_lookup("subs", 2.1215e10, fact_id=7),
            "ap+subs": cell,
        }

    def _decisions(self, events):
        return [e for e in events
                if e["kind"] == "decision" and e.get("decision") == "intersection_fallback"]

    def test_accepted_cell_is_traced_with_lineage(self, events):
        labels = {"srt:ProductOrServiceAxis": "Subscriptions and Services [Member]",
                  "srt:StatementGeographicalAxis": "Asia Pacific [Member]"}
        result = _make_executor(self._results(_cell_lookup("ap+subs", labels))).execute(
            AVGO_SPLIT_PLAN)

        assert result.step("ap+subs").value == pytest.approx(2.203e9)
        [decision] = self._decisions(events)
        assert decision["accepted"] is True
        assert decision["fact_id"] == 25183
        assert decision["hints"] == ["asia pacific", "subscriptions and services"]
        lineage = [e for e in events
                   if e["kind"] == "numeric_lineage" and e["step_id"] == "ap+subs"]
        assert lineage and lineage[0]["fact_id"] == 25183

    def test_rejected_cell_is_traced_and_not_added(self, events):
        # A third, unmentioned dimension: not a one-to-one match.
        labels = {"srt:ProductOrServiceAxis": "Subscriptions and Services [Member]",
                  "srt:StatementGeographicalAxis": "Asia Pacific [Member]",
                  "us-gaap:SubsegmentsAxis": "VMware [Member]"}
        result = _make_executor(self._results(_cell_lookup("ap+subs", labels))).execute(
            AVGO_SPLIT_PLAN)

        assert result.step("ap+subs") is None
        [decision] = self._decisions(events)
        assert decision["accepted"] is False
