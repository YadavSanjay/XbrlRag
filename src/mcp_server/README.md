# XbrlRag MCP server (`src/mcp_server/`)

Exposes XbrlRag's [v1 Analyst API](../api/README.md#v1-analyst-api-apiv1) as
**Model Context Protocol** tools, so an LLM-agent host (e.g. Faultline's
`scenariogen`) can **discover** the catalog automatically via `tools/list` and
invoke it via `tools/call` — no hand-authored tool definitions, no drift.

The tools wrap the *same* in-process v1 router functions the REST API uses
(`src/api/v1/router.py`), so resolution / citation / mapping logic has a single
source of truth. Tool input schemas are provider-neutral JSON Schema; the host
adapts them to its LLM's tool-call format (Anthropic / OpenAI), so both shapes
are supported without the server emitting two variants.

## Tools

| Tool | LLM calls | Purpose |
|------|-----------|---------|
| `coverage(ticker?)` | 0 | Ingested companies × fiscal years × form types — call first to ground arguments |
| `concepts()` | 0 | Valid `concept_family` vocabulary for the fact tools |
| `resolve_company(company)` | 0 | Resolve ticker/name/CIK → entity + metadata |
| `facts_lookup(company, concept, fiscal_years, dimension?)` | 0 | Deterministic fact **time series** + citations + status |
| `facts_compute(expr, bindings)` | 0 | Citation-traced ratio/growth over named facts |
| `narrative_search(company, query, fiscal_years?, section_hint?, top_k?)` | 0 | Risk-factor / MD&A chunks + provenance |
| `answer(query, user_id?)` | 3–4 | Full synthesized NL answer (XBRL hybrid pipeline) |

Client-error cases (unresolved company, malformed compute input) return a
structured `{"error": ..., "status_code": ...}` payload the LLM can read and
react to, rather than raising.

## Run

```bash
# stdio — local subprocess host (Claude Desktop, a local agent)
conda run -n py312 python -m src.mcp_server.server

# streamable-http — networked host (a sister service such as scenariogen)
XBRLRAG_MCP_TRANSPORT=streamable-http XBRLRAG_MCP_HOST=0.0.0.0 XBRLRAG_MCP_PORT=8765 \
    conda run -n py312 python -m src.mcp_server.server
```

Or run the packaged `mcp` service (streamable-http) from compose/Helm — see
[`deploy/README.md`](../../deploy/README.md).

Env (reuses the system's existing config):
- `XBRL_DB_*` / `OPENAI_API_KEY` / `JINA_API_KEY` — same as the rest of XbrlRag
- `XBRLRAG_MCP_TRANSPORT` — `stdio` (default) | `streamable-http` | `sse`
- `XBRLRAG_MCP_HOST` (default `127.0.0.1`) / `XBRLRAG_MCP_PORT` (default `8765`)

## Connecting from an MCP host

**stdio** (host spawns the server as a subprocess):

```json
{
  "mcpServers": {
    "arag-analyst": {
      "command": "conda",
      "args": ["run", "-n", "py312", "python", "-m", "src.mcp_server.server"],
      "cwd": "/path/to/XbrlRag"
    }
  }
}
```

**streamable-http** (host connects to a running server): point the host's MCP
client at `http://<host>:8765/mcp`.

Either way the host calls `tools/list` on connect to discover the catalog +
schemas, then `tools/call`. No auth — run on a trusted private network /
localhost alongside the host (same trust model as the BFF); front it with a
gateway before any public exposure.

## Concurrency

Tools reuse the same heavy cached singletons (BGE/Jina models, DB/LLM clients)
as the BFF, guarded by a process-wide lock; concurrent calls serialize on first
construction and on the models. Load-test before a portfolio-wide fan-out.

## Tests

- `test/unit/test_mcp_server.py` — catalog/schema discovery + delegation (router monkeypatched).
- `test/integration/test_mcp_live.py` — `tools/call` end-to-end against live `xbrl_db` (gated on backend availability).

```bash
conda run -n py312 python -m pytest test/unit/test_mcp_server.py -q
conda run -n py312 python -m pytest test/integration/test_mcp_live.py -v -m integration
```
