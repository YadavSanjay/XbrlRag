"""Unit tests for the v1 Analyst API (``src/api/v1/``).

These mock the service layer so no DB / models / LLM are needed — they assert
the route wiring, request validation, and the domain->wire mapping. A live
end-to-end smoke test belongs in the integration suite (gated on xbrl_db),
mirroring ``test/integration`` for the XBRL pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.v1 import service
from src.observability.trace_context import current_trace
from src.retriever.xbrl.xbrl_factstore_lookup import Citation, LookupStatus

client = TestClient(app)


# ---------------------------------------------------------------------------
# Lightweight stand-ins for the domain objects the service returns
# ---------------------------------------------------------------------------

@dataclass
class FakeCompany:
    cik: str | None = "0001045810"
    ticker: str | None = "NVDA"
    company_name: str | None = "NVIDIA CORP"
    fye_month: int | None = 1
    fye_day: int | None = 28
    last_seen_fy: int | None = 2026
    resolution: str = "exact_ticker"
    similarity: float = 1.0


@dataclass
class FakeLookupResult:
    status: LookupStatus = LookupStatus.OK
    value: float | None = 1000.0
    unit: str | None = "USD"
    citation: Citation | None = field(default_factory=lambda: Citation(fact_id=42))
    confidence: float = 0.95
    resolution_notes: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class FakeChunk:
    chunk_id: str = "c1"
    text: str = "Export controls may materially affect revenue."
    section_path: str | None = "Item 1A. Risk Factors"
    cik: str | None = "0001045810"
    ticker: str | None = "NVDA"
    company_name: str | None = "NVIDIA CORP"
    fiscal_year: int | None = 2026
    form_type: str | None = "10-K"
    accession_no: str | None = "0001045810-26-000001"
    page_num: int | None = 12
    rerank_score: float | None = 0.88
    is_expansion: bool = False


@dataclass
class FakeNarrativeResult:
    chunks: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Facts: lookup
# ---------------------------------------------------------------------------

def test_facts_lookup_series(monkeypatch):
    monkeypatch.setattr(
        service, "lookup_series",
        # Router now passes fiscal_period/ytd/as_of/as_originally_reported too.
        lambda company, concept, fys, dim, *args, **kw: (FakeCompany(), [FakeLookupResult() for _ in fys]),
    )
    resp = client.post("/api/v1/facts/lookup",
                       json={"company": "NVDA", "concept": "Revenue", "fiscal_years": [2025, 2026]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"]["ticker"] == "NVDA"
    assert body["unresolved"] is False
    assert [p["fiscal_year"] for p in body["series"]] == [2025, 2026]
    assert body["series"][0]["status"] == "OK"
    assert body["series"][0]["citation"]["fact_id"] == 42
    # SQL only: the block is present and says "free", not missing ("unknown").
    assert body["usage"] == {"cost_usd": 0.0, "llm_calls": 0}


def test_facts_lookup_usage_reports_metered_llm_cost(monkeypatch):
    def lookup_with_llm_call(*args, **kw):
        # Stands in for an LLMGateway call: the gateway records cost on the active trace.
        current_trace().record_llm_call(prompt_tokens=100, completion_tokens=20, cost_usd=0.0007)
        return FakeCompany(), [FakeLookupResult()]

    monkeypatch.setattr(service, "lookup_series", lookup_with_llm_call)
    resp = client.post("/api/v1/facts/lookup",
                       json={"company": "NVDA", "concept": "Revenue", "fiscal_years": [2026]})
    assert resp.status_code == 200
    usage = resp.json()["usage"]
    assert usage["cost_usd"] == pytest.approx(0.0007)
    assert usage["llm_calls"] == 1


def test_facts_lookup_unresolved_company(monkeypatch):
    monkeypatch.setattr(
        service, "lookup_series",
        lambda *a, **k: (FakeCompany(cik=None, ticker=None, resolution="unresolved"), []),
    )
    resp = client.post("/api/v1/facts/lookup",
                       json={"company": "NOPE", "concept": "Revenue", "fiscal_years": [2026]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["unresolved"] is True
    assert body["series"] == []


def test_facts_lookup_requires_fiscal_years():
    resp = client.post("/api/v1/facts/lookup",
                       json={"company": "NVDA", "concept": "Revenue", "fiscal_years": []})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Facts: compute
# ---------------------------------------------------------------------------

def test_facts_compute_growth(monkeypatch):
    monkeypatch.setattr(service, "resolve_company", lambda c: FakeCompany())
    monkeypatch.setattr(
        service, "lookup_one",
        lambda cik, ticker, concept, fy, dim: FakeLookupResult(value=120.0 if fy == 2026 else 100.0),
    )
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
    assert body["ok"] is True
    assert body["value"] == pytest.approx(0.2)
    assert set(body["resolved_bindings"]) == {"rev26", "rev25"}


def test_facts_compute_rejects_bad_binding_name():
    req = {
        "expr": "a + b",
        "bindings": [{"name": "1bad", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2026}],
    }
    resp = client.post("/api/v1/facts/compute", json=req)
    assert resp.status_code == 422


def test_facts_compute_division_by_zero_is_clean(monkeypatch):
    monkeypatch.setattr(service, "resolve_company", lambda c: FakeCompany())
    monkeypatch.setattr(service, "lookup_one",
                        lambda *a, **k: FakeLookupResult(value=0.0))
    req = {
        "expr": "a / b",
        "bindings": [
            {"name": "a", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2026},
            {"name": "b", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2025},
        ],
    }
    resp = client.post("/api/v1/facts/compute", json=req)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def test_narrative_search(monkeypatch):
    monkeypatch.setattr(
        service, "narrative_search",
        lambda company, query, fys, hint, top_k: (FakeCompany(), FakeNarrativeResult(chunks=[FakeChunk()])),
    )
    resp = client.post("/api/v1/narrative/search",
                       json={"company": "NVDA", "query": "export controls", "section_hint": "Risk Factors"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"]["ticker"] == "NVDA"
    assert len(body["chunks"]) == 1
    assert body["chunks"][0]["section_path"] == "Item 1A. Risk Factors"
    assert body["chunks"][0]["rerank_score"] == pytest.approx(0.88)
    # Self-hosted embedding + rerank, no LLM pass: free, and the block is present.
    assert body["usage"] == {"cost_usd": 0.0, "llm_calls": 0}


def test_narrative_search_usage_reports_metered_llm_cost(monkeypatch):
    def search_with_llm_calls(*args, **kw):
        # Two gateway calls, as a future query rewriter + a judge would record them.
        current_trace().record_llm_call(cost_usd=0.0004)
        current_trace().record_llm_call(cost_usd=0.0002)
        return FakeCompany(), FakeNarrativeResult(chunks=[FakeChunk()])

    monkeypatch.setattr(service, "narrative_search", search_with_llm_calls)
    resp = client.post("/api/v1/narrative/search", json={"company": "NVDA", "query": "export controls"})
    assert resp.status_code == 200
    usage = resp.json()["usage"]
    assert usage["cost_usd"] == pytest.approx(0.0006)
    assert usage["llm_calls"] == 2


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_coverage(monkeypatch):
    monkeypatch.setattr(service, "coverage", lambda ticker="": [
        {"cik": "0001045810", "ticker": "NVDA", "company_name": "NVIDIA CORP",
         "fiscal_years": [2024, 2025, 2026], "form_types": ["10-K", "10-Q"]},
    ])
    resp = client.get("/api/v1/coverage")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["cik"] == "0001045810"
    assert items[0]["fiscal_years"] == [2024, 2025, 2026]


def test_concepts(monkeypatch):
    monkeypatch.setattr(service, "concepts", lambda: [
        {"concept_family": "Revenue", "example_qname": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"},
    ])
    resp = client.get("/api/v1/concepts")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["concept_family"] == "Revenue"


def test_resolve_company_404(monkeypatch):
    monkeypatch.setattr(service, "resolve_company",
                        lambda c: FakeCompany(cik=None, ticker=None, resolution="unresolved"))
    resp = client.get("/api/v1/companies/NOPE")
    assert resp.status_code == 404


def test_resolve_company_ok(monkeypatch):
    monkeypatch.setattr(service, "resolve_company", lambda c: FakeCompany())
    resp = client.get("/api/v1/companies/NVDA")
    assert resp.status_code == 200
    assert resp.json()["cik"] == "0001045810"


# ---------------------------------------------------------------------------
# Answer (full pipeline) — mock the pipelines passthrough
# ---------------------------------------------------------------------------

def test_answer(monkeypatch):
    from src.api import pipelines

    monkeypatch.setattr(
        pipelines, "run_xbrl",
        lambda query, user_id=None: (
            {"answer_summary": "Revenue grew.", "answer_details": [{"statement": "x"}],
             "metadata": {"fully_answered": True}},
            "qid-123",
        ),
    )
    resp = client.post("/api/v1/answer", json={"query": "How did NVDA revenue change?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_id"] == "qid-123"
    assert body["answer_summary"] == "Revenue grew."
    assert body["metadata"]["fully_answered"] is True


# ---------------------------------------------------------------------------
# Screen (cross-filer ranking)
# ---------------------------------------------------------------------------

def test_screen_maps_rows(monkeypatch):
    monkeypatch.setattr(
        service, "screen",
        lambda **kw: {
            "status": "OK",
            "rows": [
                {"rank": 1, "ticker": "NVDA", "cik": "0001045810", "fiscal_year": 2025,
                 "value": 1.3e10, "unit": "USD", "pct_of_total": 0.62, "label": "China Revenue",
                 "fact_id": "42", "citation": {"fact_id": 42}},
                {"rank": 2, "ticker": "AAPL", "cik": "0000320193", "fiscal_year": 2025,
                 "value": 7.0e9, "unit": "USD", "pct_of_total": 0.18, "label": "China Revenue",
                 "fact_id": "43", "citation": {"fact_id": 43}},
            ],
            "resolution_notes": ["geographic screen: geo_code=CN"],
            "error": None,
        },
    )
    resp = client.post("/api/v1/screen", json={
        "concept_family": "Revenue", "fy": 2025,
        "dimension": {"geography": "China"}, "metric_kind": "value", "top_n": 10,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "OK"
    assert body["n_rows"] == 2
    assert body["rows"][0]["ticker"] == "NVDA"
    assert body["rows"][0]["rank"] == 1
    assert body["rows"][0]["citation"]["fact_id"] == 42


def test_screen_service_builds_step(monkeypatch):
    """The service maps typed args to the engine's `screen` step dict verbatim."""
    captured = {}

    class _FakeScreenTool:
        def screen(self, step):
            captured.update(step)
            class _R:
                def to_dict(self_):
                    return {"status": "NOT_FOUND", "rows": [], "resolution_notes": [], "error": None}
            return _R()

    monkeypatch.setattr(service, "screen_tool", lambda: _FakeScreenTool())
    out = service.screen(concept_family="Debt", fy=2024, dimension=None,
                         metric_kind="value", order="asc", top_n=3, fiscal_period="q2", ytd=True)
    assert out["status"] == "NOT_FOUND"
    assert captured["op"] == "screen"
    assert captured["concept_family"] == "Debt"
    assert captured["dimension"] == "NONE"       # None -> "NONE" sentinel
    assert captured["order"] == "asc"
    assert captured["top_n"] == 3
    assert captured["fiscal_period"] == "Q2"      # upper-cased
    assert captured["ytd"] is True
