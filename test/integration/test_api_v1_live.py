"""Live-backend endpoint validation for the v1 Analyst API (`src/api/v1/`).

Scope (per the scenariogen integration decision): the *orchestration* logic —
deciding fact vs narrative vs compute across a multi-step analysis — lives in
scenariogen and is tested there. Here we validate that each XbrlRag-exposed v1
endpoint, when invoked exactly as scenariogen's LLM router would invoke it,
returns correct, well-formed results against the live stack.

This exercises the real FastAPI app via ``TestClient`` (no service mocking), so
the same cached singletons, SQL, and models the BFF uses are in the loop.
Accuracy is asserted against ground truth read live from ``mv_financial_facts``
(anchored to ``fiscal_calendar`` via the ``ground_truth`` / ``dim_ground_truth``
conftest fixtures) — never a hardcoded number that drifts on data refresh.

Run:
    conda run -n py312 python -m pytest test/integration/test_api_v1_live.py -v -m integration -s
    # add RUN_NARRATIVE_E2E=1 to include the slow narrative + /answer cases
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

from .conftest import close_enough, requires_backends

pytestmark = [pytest.mark.integration, requires_backends]

client = TestClient(app)

NVDA_CIK = "0001045810"
_RUN_HEAVY = os.getenv("RUN_NARRATIVE_E2E") == "1"
_heavy = pytest.mark.skipif(
    not _RUN_HEAVY,
    reason="slow (loads BGE-M3 + Jina / spends LLM tokens); set RUN_NARRATIVE_E2E=1",
)


# ---------------------------------------------------------------------------
# Discovery: coverage / concepts / companies
# ---------------------------------------------------------------------------

def test_coverage_lists_known_filer():
    resp = client.get("/api/v1/coverage")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "coverage returned no ingested companies"

    by_cik = {it["cik"]: it for it in items}
    assert NVDA_CIK in by_cik, "NVDA not present in coverage"
    nvda = by_cik[NVDA_CIK]
    assert nvda["fiscal_years"], "NVDA has no fiscal years"
    assert 2026 in nvda["fiscal_years"]
    assert any(ft.startswith("10-K") for ft in nvda["form_types"])
    # fiscal_years must come back sorted ascending (array_agg ORDER BY)
    assert nvda["fiscal_years"] == sorted(nvda["fiscal_years"])


def test_coverage_ticker_filter():
    resp = client.get("/api/v1/coverage", params={"ticker": "NVDA"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items and all(it.get("ticker") == "NVDA" for it in items)


def test_concepts_vocabulary_includes_revenue():
    resp = client.get("/api/v1/concepts")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "no concept families returned"
    families = {it["concept_family"] for it in items}
    assert "Revenue" in families
    rev = next(it for it in items if it["concept_family"] == "Revenue")
    assert rev["example_qname"], "Revenue has no example QName"


def test_resolve_company_ok():
    resp = client.get("/api/v1/companies/NVDA")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cik"] == NVDA_CIK
    assert body["ticker"] == "NVDA"
    assert body["resolution"] in ("exact_ticker", "fuzzy_name")


def test_resolve_company_unresolved_is_404():
    resp = client.get("/api/v1/companies/ZZZZ-NOT-A-TICKER")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Facts: lookup (deterministic) — assert against live ground truth
# ---------------------------------------------------------------------------

def test_facts_lookup_revenue_series(ground_truth):
    resp = client.post("/api/v1/facts/lookup",
                       json={"company": "NVDA", "concept": "Revenue", "fiscal_years": [2025, 2026]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["unresolved"] is False
    assert body["company"]["cik"] == NVDA_CIK
    series = {p["fiscal_year"]: p for p in body["series"]}
    assert set(series) == {2025, 2026}

    for fy in (2025, 2026):
        point = series[fy]
        assert point["status"] == "OK", f"FY{fy} not OK: {point}"
        assert point["unit"] == "USD"
        assert point["citation"] and point["citation"]["fact_id"] is not None
        expected = ground_truth("NVDA", fy, "Revenue")
        assert expected is not None, f"no ground truth for NVDA Revenue FY{fy}"
        assert close_enough(point["value"], expected), f"FY{fy}: {point['value']} != {expected}"


def test_facts_lookup_dimensional(dim_ground_truth):
    resp = client.post("/api/v1/facts/lookup",
                       json={"company": "NVDA", "concept": "Revenue", "fiscal_years": [2026],
                             "dimension": {"product": "data center"}})
    assert resp.status_code == 200
    point = resp.json()["series"][0]
    assert point["status"] == "OK"
    expected = dim_ground_truth("NVDA", 2026, "Revenue", "%ProductOrServiceAxis", "%data center%")
    assert expected is not None
    assert close_enough(point["value"], expected)


def test_facts_lookup_year_with_no_filing_is_not_found():
    resp = client.post("/api/v1/facts/lookup",
                       json={"company": "NVDA", "concept": "Revenue", "fiscal_years": [2019]})
    assert resp.status_code == 200
    point = resp.json()["series"][0]
    assert point["status"] == "NOT_FOUND"
    assert point["value"] is None


def test_facts_lookup_unresolved_company():
    resp = client.post("/api/v1/facts/lookup",
                       json={"company": "ZZZZ-NOT-A-TICKER", "concept": "Revenue", "fiscal_years": [2026]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["unresolved"] is True
    assert body["series"] == []


# ---------------------------------------------------------------------------
# Facts: compute (citation-traced) — value must match a from-ground-truth calc
# ---------------------------------------------------------------------------

def test_facts_compute_yoy_growth(ground_truth):
    req = {
        "expr": "(rev26 - rev25) / rev25",
        "bindings": [
            {"name": "rev26", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2026},
            {"name": "rev25", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2025},
        ],
    }
    resp = client.post("/api/v1/facts/compute", json=req)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, body
    # every binding carries its own fact-id provenance
    assert set(body["resolved_bindings"]) == {"rev26", "rev25"}
    assert all(rb["citation"] and rb["citation"]["fact_id"] for rb in body["resolved_bindings"].values())

    g26, g25 = ground_truth("NVDA", 2026, "Revenue"), ground_truth("NVDA", 2025, "Revenue")
    assert g26 and g25
    assert close_enough(body["value"], (g26 - g25) / g25)


def test_facts_compute_rejects_bad_binding_name():
    req = {"expr": "x", "bindings": [
        {"name": "1bad", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2026}]}
    resp = client.post("/api/v1/facts/compute", json=req)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Narrative + Answer — heavy (models / LLM); opt-in via RUN_NARRATIVE_E2E=1
# ---------------------------------------------------------------------------

@_heavy
def test_narrative_search_returns_provenance():
    resp = client.post("/api/v1/narrative/search",
                       json={"company": "NVDA", "query": "export control restrictions",
                             "fiscal_years": [2026], "section_hint": "Risk Factors", "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"]["cik"] == NVDA_CIK
    assert body["chunks"], "no narrative chunks returned"
    top = body["chunks"][0]
    assert top["chunk_id"] and top["text"]
    assert top["cik"] == NVDA_CIK
    assert top["fiscal_year"] == 2026


@_heavy
def test_answer_full_pipeline():
    resp = client.post("/api/v1/answer",
                       json={"query": "What was NVIDIA's total revenue in FY2026?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer_summary"], "empty answer_summary"
    assert isinstance(body["answer_details"], list)
    # a numeric question should surface the headline figure somewhere in prose
    assert "215" in body["answer_summary"] or any(
        "215" in str(d) for d in body["answer_details"]
    )
