"""MCP server exposing XbrlRag's v1 Analyst API as Model Context Protocol tools.

This is the protocol-native discovery layer for LLM-agent callers such as
Faultline's ``scenariogen``: an MCP host connects, calls ``tools/list`` to
discover the catalog + JSON-Schema arguments automatically, and ``tools/call``
to invoke them — no hand-authored tool definitions, no drift.

The MCP tools wrap the *same* in-process v1 router functions the REST API uses
(``src/api/v1/router.py``), so resolution / citation / mapping logic has one
source of truth. MCP tool schemas are provider-neutral JSON Schema; the host
adapts them to its LLM's tool-call shape (Anthropic / OpenAI), so both shapes
are supported without the server emitting two variants.
"""
from __future__ import annotations

from src.mcp_server.server import build_server, mcp

__all__ = ["build_server", "mcp"]
