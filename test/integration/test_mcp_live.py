"""Live-backend smoke test for the MCP server (`src/mcp_server/`).

Proves that an MCP `tools/call` for a deterministic tool flows all the way
through to the live `xbrl_db` and returns the correct cited value — i.e. the
catalog a scenariogen MCP host discovers is actually wired to real data, not
just to mocks. Heavier tools (narrative / answer) are validated via the REST
path in ``test_api_v1_live.py``; here we cover the MCP plumbing on the cheap,
deterministic path.

Run:
    conda run -n py312 python -m pytest test/integration/test_mcp_live.py -v -m integration -s
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.mcp_server.server import mcp

from .conftest import close_enough, requires_backends

pytestmark = [pytest.mark.integration, requires_backends]


def _call(name: str, args: dict) -> dict:
    content, _structured = asyncio.run(mcp.call_tool(name, args))
    return json.loads(content[0].text)


def test_mcp_catalog_is_complete():
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == {
        "coverage", "concepts", "resolve_company",
        "facts_lookup", "facts_compute", "narrative_search", "answer",
    }


def test_mcp_coverage_live():
    out = _call("coverage", {"ticker": "NVDA"})
    assert out["items"], "no coverage for NVDA"
    assert out["items"][0]["cik"] == "0001045810"


def test_mcp_facts_lookup_live(ground_truth):
    out = _call("facts_lookup", {"company": "NVDA", "concept": "Revenue", "fiscal_years": [2026]})
    point = out["series"][0]
    assert point["status"] == "OK"
    assert point["unit"] == "USD"
    assert point["citation"]["fact_id"] is not None
    expected = ground_truth("NVDA", 2026, "Revenue")
    assert expected is not None
    assert close_enough(point["value"], expected)


def test_mcp_facts_compute_live(ground_truth):
    out = _call("facts_compute", {
        "expr": "(rev26 - rev25) / rev25",
        "bindings": [
            {"name": "rev26", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2026},
            {"name": "rev25", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2025},
        ],
    })
    assert out["ok"] is True, out
    g26, g25 = ground_truth("NVDA", 2026, "Revenue"), ground_truth("NVDA", 2025, "Revenue")
    assert g26 and g25
    assert close_enough(out["value"], (g26 - g25) / g25)


def test_mcp_resolve_company_unresolved_is_error_payload():
    out = _call("resolve_company", {"company": "ZZZZ-NOT-A-TICKER"})
    assert out["status_code"] == 404
