# CLAUDE.md — `src/mcp_server/` (MCP server)

FastMCP server exposing a **7-tool core subset** of the [v1 Analyst API](../api/v1/)
for LLM-agent hosts (e.g. `scenariogen`). Full docs: [`README.md`](README.md) and
[`docs/guides/xbrl-flow/09-programmatic-access-rest-and-mcp.md`](../../docs/guides/xbrl-flow/09-programmatic-access-rest-and-mcp.md).
Packaged as the `mcp` service alongside `api`/`ui` in `infra/docker/docker-compose.yml` /
`deploy/helm/xbrlrag` — see [`deploy/README.md`](../../deploy/README.md).

Tools: `coverage`, `concepts`, `resolve_company`, `facts_lookup`, `facts_compute`,
`narrative_search`, `answer` — each wraps the **same in-process `src.api.v1.router`
function** (single source of truth). The `bridge` / `metrics*` / `screen` v1
endpoints are REST-only; add a tool here if a host needs them.

## Invariants (do not break)

- **Never log to stdout.** Under the stdio transport, stdout is the JSON-RPC framing
  channel — any stdout line corrupts the protocol and **deadlocks the host**. Logging
  goes to **stderr** (`configs/logging.yaml`). `TOKENIZERS_PARALLELISM=false` is set
  before transformers import.
- Client-error cases return a structured `{"error", "status_code"}` payload (readable by
  the LLM), not a raised exception.
- Transports: `XBRLRAG_MCP_TRANSPORT` = `stdio` (default) | `streamable-http` | `sse`;
  `XBRLRAG_MCP_HOST` (`127.0.0.1`) / `XBRLRAG_MCP_PORT` (`8765`). No auth.
- Tool input schemas are **provider-neutral JSON Schema** — do not emit Anthropic- vs
  OpenAI-specific variants; the host adapts them.
- `mcp` is pinned `>=1.30,<2` with `fastapi==0.162.0`, `starlette>=1.3.1,<2` and
  `pydantic<2.12` in `requirements.txt`; move them together (pydantic 2.12+ breaks FastMCP's
  tool inspector).
