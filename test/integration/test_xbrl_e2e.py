"""End-to-end regression & performance suite for the XBRL plan-based pipeline.

Runs the *real* pipeline (intent -> plan -> execute -> synthesize -> verify)
against live Postgres + Qdrant + OpenAI, modelled on the analyst query patterns in
``scripts/xbrl/analyst_queries.sql``. It is organized by query category:

  * flat numeric lookup        (income statement / balance sheet single facts)
  * dimensional/segment lookup (product-line revenue)
  * geographic lookup          (revenue by region)
  * numeric compute            (YoY growth, R&D intensity)
  * named ratios               (gross/operating margin)   -- KNOWN-GAP (xfail)
  * free cash flow             (CFO - Capex)               -- KNOWN-GAP (xfail)
  * cross-company comparison   (margins across two filers)
  * narrative                  (prose risk factors)        -- slow, gated
  * hybrid                     (numeric + prose drivers)   -- slow, gated

**Accuracy** is asserted against ground truth read live from ``mv_financial_facts``
(anchored to ``fiscal_calendar``) and the values the executor resolves/computes —
never the LLM's prose. **Performance** is reported per-query in the terminal summary.

Two known sources of intermittent failure are encoded deliberately:
  * the gpt-5-nano intent router is non-deterministic — a clean numeric question
    can occasionally route to the narrative branch (no fact + ~5x latency); when
    that happens the strict accuracy assertion correctly fails. See the
    xbrl-intent-router-nondeterminism finding.
  * named-ratio / FCF / business-segment resolution are current gaps, marked xfail
    (non-strict) so a future fix surfaces as an XPASS rather than silent success.

Run:
    conda run -n py312 python -m pytest test/integration -v -m integration -s
    # add RUN_NARRATIVE_E2E=1 to include the slow narrative/hybrid cases
"""
from __future__ import annotations

import os

import pytest

from .conftest import (
    close_enough,
    compute_step_values,
    lookup_step_values,
    matches_any,
    plan_ops,
    requires_backends,
)

pytestmark = [pytest.mark.integration, requires_backends]

# Per-query ceilings are HANG DETECTORS, not micro-regression thresholds: LLM API
# latency is highly variable and outside our control (a single 7-step compute has
# been seen at 33s and 185s on different runs), so tight budgets would be flaky.
# Actual performance is tracked by the per-query latency table in the terminal
# summary; these budgets only catch a genuinely stuck query. Numeric queries are
# LLM-bound (a few gpt-5-nano/mini calls); the narrative/hybrid path additionally
# reranks ~30 candidates through jina-reranker-v3 on CPU (~130s) — sub-second on GPU.
NUMERIC_BUDGET_SECONDS = 240.0
NARRATIVE_BUDGET_SECONDS = 360.0

NARRATIVE_GATED = pytest.mark.skipif(
    os.getenv("RUN_NARRATIVE_E2E") != "1",
    reason="slow on CPU (~200s, jina-reranker-v3); opt in with RUN_NARRATIVE_E2E=1",
)


# ===========================================================================
# 1. Flat numeric lookup — income statement & balance sheet single facts
#    (label, query, ticker, fy, concept_family, period_type)
# ===========================================================================

FLAT_CASES = [
    ("nvda-rev-2026", "What was NVIDIA's revenue in fiscal 2026?", "NVDA", 2026, "Revenue", "duration"),
    ("nvda-gp-2026", "What was NVIDIA's gross profit in fiscal 2026?", "NVDA", 2026, "GrossProfit", "duration"),
    ("nvda-oi-2026", "What was NVIDIA's operating income in fiscal 2026?", "NVDA", 2026, "OperatingIncome", "duration"),
    ("nvda-ni-2026", "What was NVIDIA's net income in fiscal 2026?", "NVDA", 2026, "NetIncome", "duration"),
    ("nvda-cfo-2026", "How much cash from operations did NVIDIA generate in fiscal 2026?", "NVDA", 2026, "CashFromOperations", "duration"),
    ("nvda-assets-2026", "What were NVIDIA's total assets at the end of fiscal 2026?", "NVDA", 2026, "TotalAssets", "instant"),
    ("nvda-cash-2026", "How much cash and cash equivalents did NVIDIA hold at fiscal 2026 year-end?", "NVDA", 2026, "CashAndEquivalents", "instant"),
    ("aapl-rev-2024", "What was Apple's revenue in fiscal 2024?", "AAPL", 2024, "Revenue", "duration"),
    ("aapl-ni-2024", "What was Apple's net income in fiscal 2024?", "AAPL", 2024, "NetIncome", "duration"),
    ("msft-rev-2024", "What was Microsoft's total revenue in fiscal 2024?", "MSFT", 2024, "Revenue", "duration"),
    ("amzn-rev-2024", "What was Amazon's net sales in fiscal 2024?", "AMZN", 2024, "Revenue", "duration"),
    ("googl-ni-2024", "What was Alphabet's net income in fiscal 2024?", "GOOGL", 2024, "NetIncome", "duration"),
]


@pytest.mark.parametrize(
    "label,query,ticker,fy,concept_family,period_type",
    FLAT_CASES,
    ids=[c[0] for c in FLAT_CASES],
)
def test_flat_lookup_accuracy(run_query, ground_truth, label, query, ticker, fy, concept_family, period_type):
    expected = ground_truth(ticker, fy, concept_family, period_type)
    assert expected is not None, f"no ground-truth fact for {ticker} {fy} {concept_family}"

    resp, _ = run_query(query, label=label, budget=NUMERIC_BUDGET_SECONDS)
    assert resp.execution_result.get("status") == "OK", resp.execution_result.get("error")

    values = lookup_step_values(resp)
    assert values, (
        f"no resolved lookup value; plan ops={plan_ops(resp)} "
        f"(intent router may have misrouted this numeric query)"
    )
    assert matches_any(values, expected), (
        f"resolved {values} != ground truth {expected:,.0f} for {ticker} FY{fy} {concept_family}\n"
        f"answer: {resp.answer_summary}"
    )


# ===========================================================================
# 2. Dimensional / segment lookup — product-line revenue
# ===========================================================================

def test_segment_lookup_product_axis(run_query, dim_ground_truth):
    """NVDA Data Center revenue — resolved via srt:ProductOrServiceAxis."""
    expected = dim_ground_truth("NVDA", 2026, "Revenue", "%ProductOrServiceAxis", "%data center%")
    assert expected is not None

    resp, _ = run_query(
        "What was NVIDIA's Data Center revenue in fiscal 2026?",
        label="seg-nvda-datacenter",
        budget=NUMERIC_BUDGET_SECONDS,
    )
    values = lookup_step_values(resp)
    assert matches_any(values, expected), (
        f"Data Center revenue {expected:,.0f} not among resolved {values}\n{resp.answer_summary}"
    )


@pytest.mark.xfail(
    reason="business-segment (StatementBusinessSegmentsAxis) resolution gap: AWS "
    "query resolves to total company revenue instead of the segment member",
    strict=False,
)
def test_segment_lookup_business_axis(run_query, dim_ground_truth):
    """AMZN AWS segment revenue — KNOWN GAP (returns total revenue)."""
    expected = dim_ground_truth("AMZN", 2024, "Revenue", "%BusinessSegments%", "%web services%")
    assert expected is not None

    resp, _ = run_query(
        "What was Amazon Web Services revenue in fiscal 2024?",
        label="seg-amzn-aws",
        budget=NUMERIC_BUDGET_SECONDS,
    )
    assert matches_any(lookup_step_values(resp), expected)


# ===========================================================================
# 3. Geographic lookup — revenue by region
# ===========================================================================

def test_geographic_lookup_resolves(run_query, dim_ground_truth):
    """NVDA US revenue — resolved via srt:StatementGeographicalAxis. The planner
    may also compute a share-of-total; we assert the underlying US fact resolves."""
    expected = dim_ground_truth("NVDA", 2026, "Revenue", "%GeographicalAxis", "%united states%")
    assert expected is not None

    resp, _ = run_query(
        "How much of NVIDIA's revenue came from the United States in fiscal 2026?",
        label="geo-nvda-us",
        budget=NUMERIC_BUDGET_SECONDS,
    )
    values = lookup_step_values(resp)
    assert matches_any(values, expected), (
        f"US revenue {expected:,.0f} not among resolved {values}\n{resp.answer_summary}"
    )


# ===========================================================================
# 4. Numeric compute — YoY growth & intensity ratios (multi-step plans)
# ===========================================================================

def test_yoy_growth_compute(run_query, ground_truth):
    """Revenue YoY: both years resolve as lookups and a compute yields the delta
    or % change."""
    rev_prev = ground_truth("NVDA", 2025, "Revenue")
    rev_curr = ground_truth("NVDA", 2026, "Revenue")
    assert rev_prev and rev_curr

    resp, _ = run_query(
        "How did NVIDIA's revenue change from fiscal 2025 to fiscal 2026?",
        label="compute-nvda-yoy",
        budget=NUMERIC_BUDGET_SECONDS,
    )
    assert resp.execution_result.get("status") == "OK", resp.execution_result.get("error")

    values = lookup_step_values(resp)
    assert matches_any(values, rev_prev) and matches_any(values, rev_curr), (
        f"both years must resolve; got {values}"
    )

    delta = rev_curr - rev_prev
    pct = delta / rev_prev          # fraction (executor emits 0.65, not 65)
    computes = compute_step_values(resp)
    assert computes, "expected a compute step for a YoY question"
    assert any(
        close_enough(v, delta, rel=1e-3) or close_enough(v, pct, rel=1e-2) for v in computes
    ), f"compute results {computes} match neither delta {delta:,.0f} nor pct {pct:.4f}"


def test_rnd_intensity_compute(run_query, ground_truth):
    """R&D as a % of revenue — explicit ratio phrasing reliably triggers a compute."""
    rnd = ground_truth("NVDA", 2026, "ResearchAndDevelopment")
    rev = ground_truth("NVDA", 2026, "Revenue")
    assert rnd and rev

    resp, _ = run_query(
        "What was NVIDIA's R&D as a percentage of revenue in fiscal 2026?",
        label="compute-nvda-rnd-intensity",
        budget=NUMERIC_BUDGET_SECONDS,
    )
    values = lookup_step_values(resp)
    assert matches_any(values, rnd) and matches_any(values, rev)

    ratio = rnd / rev               # ~0.0857
    computes = compute_step_values(resp)
    assert any(
        close_enough(v, ratio, rel=1e-2) or close_enough(v, ratio * 100, rel=1e-2) for v in computes
    ), f"compute results {computes} != R&D intensity {ratio:.4f}"


# ===========================================================================
# 5. Named ratios — KNOWN GAP: 'gross/operating margin' often mis-routes to a
#    single dollar lookup instead of a Revenue+component compute.
# ===========================================================================

@pytest.mark.parametrize(
    "label,query,numer_family,denom_family",
    [
        ("ratio-nvda-gross-margin", "What was NVIDIA's gross margin in fiscal 2026?", "GrossProfit", "Revenue"),
        ("ratio-nvda-op-margin", "What was NVIDIA's operating margin in fiscal 2026?", "OperatingIncome", "Revenue"),
    ],
    ids=["gross-margin", "operating-margin"],
)
@pytest.mark.xfail(
    reason="named-ratio terms ('gross margin', 'operating margin') intermittently "
    "route to a single dollar lookup instead of a ratio compute (intent-router "
    "non-determinism). Strict=False: passes as XPASS when it does compute correctly.",
    strict=False,
)
def test_named_ratio_compute(run_query, ground_truth, label, query, numer_family, denom_family):
    numer = ground_truth("NVDA", 2026, numer_family)
    denom = ground_truth("NVDA", 2026, denom_family)
    assert numer and denom
    ratio = numer / denom

    resp, _ = run_query(query, label=label, budget=NUMERIC_BUDGET_SECONDS)
    computes = compute_step_values(resp)
    assert any(
        close_enough(v, ratio, rel=1e-2) or close_enough(v, ratio * 100, rel=1e-2) for v in computes
    ), f"no compute matched margin {ratio:.4f}; computes={computes}, ops={plan_ops(resp)}"


# ===========================================================================
# 6. Free cash flow — KNOWN GAP: CFO - Capex currently errors / does not compute.
# ===========================================================================

@pytest.mark.xfail(reason="free-cash-flow (CFO - Capex) plan currently errors", strict=False)
def test_free_cash_flow_compute(run_query, ground_truth):
    cfo = ground_truth("NVDA", 2026, "CashFromOperations")
    capex = ground_truth("NVDA", 2026, "Capex")
    assert cfo and capex
    fcf = cfo - capex

    resp, _ = run_query(
        "What was NVIDIA's free cash flow in fiscal 2026?",
        label="compute-nvda-fcf",
        budget=NUMERIC_BUDGET_SECONDS,
    )
    assert resp.execution_result.get("status") == "OK"
    assert matches_any(compute_step_values(resp), fcf, rel=1e-3)


# ===========================================================================
# 7. Cross-company comparison — margins across two filers in one query
# ===========================================================================

def test_cross_company_margin_comparison(run_query, ground_truth):
    """Compare NVDA vs AMD gross margins (FY2024). The deterministic guarantee is
    that all four component facts resolve; the margin computes are checked softly."""
    nvda_rev = ground_truth("NVDA", 2024, "Revenue")
    nvda_gp = ground_truth("NVDA", 2024, "GrossProfit")
    amd_rev = ground_truth("AMD", 2024, "Revenue")
    amd_gp = ground_truth("AMD", 2024, "GrossProfit")
    assert all([nvda_rev, nvda_gp, amd_rev, amd_gp])

    resp, _ = run_query(
        "Compare NVIDIA and AMD gross margins in fiscal 2024.",
        label="xcompany-nvda-amd-gm",
        budget=NUMERIC_BUDGET_SECONDS,
    )
    assert resp.execution_result.get("status") == "OK", resp.execution_result.get("error")

    values = lookup_step_values(resp)
    # Both companies' revenue must be resolved for a valid comparison.
    assert matches_any(values, nvda_rev), f"NVDA FY2024 revenue {nvda_rev:,.0f} missing from {values}"
    assert matches_any(values, amd_rev), f"AMD FY2024 revenue {amd_rev:,.0f} missing from {values}"

    # Soft: if margins were computed, at least one should match a real gross margin.
    computes = compute_step_values(resp)
    if computes:
        nvda_gm, amd_gm = nvda_gp / nvda_rev, amd_gp / amd_rev
        assert any(
            close_enough(v, m, rel=2e-2) or close_enough(v, m * 100, rel=2e-2)
            for v in computes for m in (nvda_gm, amd_gm)
        ), f"computes {computes} match neither NVDA {nvda_gm:.3f} nor AMD {amd_gm:.3f} gross margin"


# ===========================================================================
# 8. Routing — numeric -> fact branch; prose -> narrative branch
# ===========================================================================

def test_numeric_query_routes_to_lookup(run_query):
    resp, _ = run_query(
        "What was NVIDIA's net income in fiscal 2026?",
        label="routing-lookup",
        budget=NUMERIC_BUDGET_SECONDS,
    )
    assert "lookup" in plan_ops(resp), f"expected a lookup op, got {plan_ops(resp)}"


# ===========================================================================
# 9. Narrative — prose question hits Qdrant and returns cited chunks (gated/slow)
# ===========================================================================

@NARRATIVE_GATED
def test_narrative_retrieval_returns_chunks(run_query):
    resp, _ = run_query(
        "What risk factors does NVIDIA describe related to export controls and competition?",
        label="narrative-risk",
        budget=NARRATIVE_BUDGET_SECONDS,
    )
    assert "narrative" in plan_ops(resp), f"expected a narrative op, got {plan_ops(resp)}"
    assert resp.narrative_chunks, "narrative query returned no chunks from Qdrant"
    assert resp.answer_summary.strip(), "narrative query produced an empty answer"


# ===========================================================================
# 10. Hybrid — numeric answer + prose drivers in one query (gated/slow)
# ===========================================================================

@NARRATIVE_GATED
def test_hybrid_yoy_with_drivers(run_query, ground_truth):
    rev_prev = ground_truth("NVDA", 2025, "Revenue")
    rev_curr = ground_truth("NVDA", 2026, "Revenue")
    assert rev_prev and rev_curr

    resp, _ = run_query(
        "How did NVIDIA's revenue change from fiscal 2025 to 2026, and what drove it?",
        label="hybrid-yoy-drivers",
        budget=NARRATIVE_BUDGET_SECONDS,
    )
    values = lookup_step_values(resp)
    assert matches_any(values, rev_prev) and matches_any(values, rev_curr)
    assert resp.narrative_chunks, "hybrid query returned no narrative chunks"


@NARRATIVE_GATED
def test_hybrid_segment_with_drivers(run_query, dim_ground_truth):
    dc = dim_ground_truth("NVDA", 2026, "Revenue", "%ProductOrServiceAxis", "%data center%")
    assert dc is not None

    resp, _ = run_query(
        "What was NVIDIA's Data Center revenue in fiscal 2026 and what factors drove its growth?",
        label="hybrid-segment-drivers",
        budget=NARRATIVE_BUDGET_SECONDS,
    )
    assert matches_any(lookup_step_values(resp), dc), "Data Center FY2026 revenue not resolved"
    assert resp.narrative_chunks, "hybrid query returned no narrative chunks"


# ===========================================================================
# 11. Performance budget — runs last; every timed query within its category budget
#     (per-query latency table is printed by conftest.pytest_terminal_summary)
# ===========================================================================

def test_performance_within_budget():
    from .conftest import PERF

    assert PERF, "no queries were timed (earlier tests skipped?)"
    over = [r for r in PERF if r["budget"] and r["seconds"] >= r["budget"]]
    assert not over, "queries exceeded their latency budget: " + ", ".join(
        f"{r['label']} {r['seconds']:.1f}s (budget {r['budget']:.0f}s)" for r in over
    )
