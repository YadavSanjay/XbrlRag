"""The MCP server writes nothing to stdout.

Test strategy area K (docs/design/XbrlRag_Test_Strategy.md); src/mcp_server/CLAUDE.md:
under the stdio transport stdout is the JSON-RPC channel, so one stray line corrupts the
protocol and deadlocks the host. Checked twice: a fresh interpreter that imports the
server, lists its tools and logs at every level (so the process's real logging
configuration is what runs), and in-process at the file-descriptor level while every tool
is invoked through MCP dispatch with only the v1 service layer (DB, models) and the
pipeline faked.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from src.api import pipelines
from src.api.v1 import service
from src.llm.run_budget import CircuitBreakerOpen
from src.retriever.xbrl.xbrl_factstore_lookup import Citation, LookupStatus

REPO = Path(__file__).resolve().parents[2]

_CHILD = """
import asyncio, logging
from src.mcp_server.server import mcp
from src.logging_config import get_logger
tools = asyncio.run(mcp.list_tools())
log = get_logger("src.mcp_server.server")
log.debug("debug line"); log.info("info line"); log.warning("warning line")
log.error("error line")
logging.getLogger("xbrlrag.events").warning('{"kind": "probe"}')
import sys
sys.stderr.write("TOOLS=%d\\n" % len(tools))
"""


def test_import_list_and_log_write_nothing_to_stdout():
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], cwd=REPO, capture_output=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert b"TOOLS=7" in proc.stderr
    assert proc.stdout == b""


@dataclass
class _Company:
    cik: str | None = "0001045810"
    ticker: str | None = "NVDA"
    company_name: str | None = "NVIDIA CORP"
    fye_month: int | None = 1
    fye_day: int | None = 28
    last_seen_fy: int | None = 2026
    resolution: str = "exact_ticker"
    similarity: float = 1.0


@dataclass
class _Lookup:
    status: LookupStatus = LookupStatus.OK
    value: float | None = 1000.0
    unit: str | None = "USD"
    citation: Citation | None = field(default_factory=lambda: Citation(fact_id=42))
    confidence: float = 0.95
    resolution_notes: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class _Chunk:
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
class _Narrative:
    chunks: list = field(default_factory=lambda: [_Chunk()])


class _HaltingPipeline:
    def generate(self, query: str, user_id: str | None = None):
        raise CircuitBreakerOpen("3 consecutive LLM failures")


@pytest.fixture
def faked_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "coverage", lambda ticker="": [
        {"cik": "0001045810", "ticker": "NVDA", "company_name": "NVIDIA CORP",
         "fiscal_years": [2026], "form_types": ["10-K"]}])
    monkeypatch.setattr(service, "concepts", lambda: [
        {"concept_family": "Revenue", "example_qname": "us-gaap:Revenues"}])
    monkeypatch.setattr(service, "resolve_company", lambda company: _Company())
    monkeypatch.setattr(
        service, "lookup_series",
        lambda company, concept, fys, *a, **k: (_Company(), [_Lookup() for _ in fys]))
    monkeypatch.setattr(service, "lookup_one", lambda *a, **k: _Lookup())
    monkeypatch.setattr(service, "narrative_search", lambda *a, **k: (_Company(), _Narrative()))
    monkeypatch.setattr(pipelines, "get_xbrl_pipeline", lambda: _HaltingPipeline())


_CALLS = [
    ("coverage", {}),
    ("concepts", {}),
    ("resolve_company", {"company": "NVDA"}),
    ("facts_lookup", {"company": "NVDA", "concept": "Revenue", "fiscal_years": [2025, 2026]}),
    ("facts_compute", {"expr": "a / b", "bindings": [
        {"name": "a", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2026},
        {"name": "b", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2025}]}),
    ("narrative_search", {"company": "NVDA", "query": "export controls"}),
    ("answer", {"query": "What was NVDA revenue?"}),
    # The client-error path (a structured error payload) must be silent too.
    ("facts_compute", {"expr": "a", "bindings": [{"name": "a"}]}),
]


def test_invoking_every_tool_writes_nothing_to_stdout(faked_backends, capfd):
    from src.mcp_server.server import mcp

    capfd.readouterr()  # discard anything from fixture setup
    outputs = {}
    for name, args in _CALLS:
        content, _ = asyncio.run(mcp.call_tool(name, args))
        outputs[name] = json.loads(content[0].text)

    captured = capfd.readouterr()
    assert {name for name, _ in _CALLS} == {t.name for t in asyncio.run(mcp.list_tools())}
    assert outputs["answer"]["metadata"]["halted"] is True  # the tools really ran
    assert outputs["facts_lookup"]["series"][0]["value"] == 1000.0
    assert captured.out == ""
