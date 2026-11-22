"""Unit tests for the XbrlRag MCP server (`src/mcp_server/`).

These verify (a) the tool catalog the MCP host discovers via `tools/list`, and
(b) that each tool correctly delegates to the v1 router and serializes the
result — with the router monkeypatched so no DB / models / LLM are needed. The
tools call `mcp.call_tool(...)`, i.e. the real MCP dispatch path.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from src.api.v1 import router as v1router
from src.api.v1 import schemas
from src.mcp_server.server import mcp


def _call(name: str, args: dict):
    """Invoke a tool through MCP dispatch and return the JSON payload the host
    surfaces to the LLM.

    FastMCP's ``call_tool`` returns ``(content_blocks, structured)``. The first
    text content block carries the tool's return value as clean (unwrapped)
    JSON — that is what an MCP client renders for the model — so we parse it.
    (``structured`` nests the same dict under a ``result`` key, a FastMCP
    convention for plain-``dict`` returns.)
    """
    content, _structured = asyncio.run(mcp.call_tool(name, args))
    return json.loads(content[0].text)


# ---------------------------------------------------------------------------
# Catalog / discovery
# ---------------------------------------------------------------------------

def test_tool_catalog():
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "coverage", "concepts", "resolve_company",
        "facts_lookup", "facts_compute", "narrative_search", "answer",
    }


def test_tool_schemas_are_described():
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    # every tool carries an LLM-facing description
    assert all(t.description for t in tools.values())
    # facts_lookup advertises its required args as JSON Schema
    fl = tools["facts_lookup"].inputSchema
    assert set(fl["required"]) == {"company", "concept", "fiscal_years"}
    assert fl["properties"]["fiscal_years"]["type"] == "array"


# ---------------------------------------------------------------------------
# Delegation (router monkeypatched)
# ---------------------------------------------------------------------------

def test_facts_lookup_delegates(monkeypatch):
    captured = {}

    def fake(req: schemas.FactLookupRequest):
        captured["req"] = req
        return schemas.FactLookupResponse(
            company=schemas.ResolvedCompanyModel(cik="0001045810", ticker="NVDA", resolution="exact_ticker"),
            concept=req.concept, unresolved=False,
            series=[schemas.FactPoint(fiscal_year=fy, status="OK", value=1.0, unit="USD") for fy in req.fiscal_years],
            usage=schemas.UsageModel(cost_usd=0.0, llm_calls=0),
        )

    monkeypatch.setattr(v1router, "facts_lookup", fake)
    out = _call("facts_lookup", {"company": "NVDA", "concept": "Revenue", "fiscal_years": [2025, 2026]})
    assert captured["req"].concept == "Revenue"
    assert captured["req"].fiscal_years == [2025, 2026]
    assert out["company"]["ticker"] == "NVDA"
    assert [p["fiscal_year"] for p in out["series"]] == [2025, 2026]


def test_resolve_company_unresolved_returns_error_payload(monkeypatch):
    def fake(company):
        raise HTTPException(status_code=404, detail=f"could not resolve company {company!r}")

    monkeypatch.setattr(v1router, "resolve_company", fake)
    out = _call("resolve_company", {"company": "NOPE"})
    assert out["status_code"] == 404
    assert "could not resolve" in out["error"]


def test_facts_compute_bad_binding_returns_error(monkeypatch):
    # Reaches router, which raises 422 for an invalid identifier name.
    def fake(req):
        raise HTTPException(status_code=422, detail="invalid binding name '1bad'")

    monkeypatch.setattr(v1router, "facts_compute", fake)
    out = _call("facts_compute", {
        "expr": "a", "bindings": [{"name": "1bad", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2026}],
    })
    assert out["ok"] is False
    assert "invalid binding name" in out["error"]


def test_facts_compute_malformed_binding_shape():
    # Missing required binding fields -> caught before the router.
    out = _call("facts_compute", {"expr": "a", "bindings": [{"name": "a"}]})
    assert out["ok"] is False
    assert "invalid bindings" in out["error"]


def test_coverage_delegates(monkeypatch):
    monkeypatch.setattr(v1router, "coverage",
                        lambda ticker="": schemas.CoverageResponse(items=[
                            schemas.CoverageItem(cik="0001045810", ticker="NVDA",
                                                 fiscal_years=[2025, 2026], form_types=["10-K"])]))
    out = _call("coverage", {})
    assert out["items"][0]["ticker"] == "NVDA"
