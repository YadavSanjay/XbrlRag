"""FastMCP server for the XbrlRag v1 Analyst API.

Each ``@mcp.tool()`` wraps the corresponding in-process v1 router function
(``src.api.v1.router``) and returns a plain JSON-able dict (the pydantic
response ``model_dump``). Client-error cases (unresolved company, invalid
compute input) are returned as structured ``{"error": ...}`` payloads rather
than raised, so the calling LLM can read and react to them; only unexpected
server faults raise.

Run:
    # stdio (local subprocess host, e.g. Claude Desktop / a local agent)
    conda run -n py312 python -m src.mcp_server.server

    # streamable-http (networked host such as a sister service)
    XBRLRAG_MCP_TRANSPORT=streamable-http XBRLRAG_MCP_PORT=8765 \
        conda run -n py312 python -m src.mcp_server.server

Reuses the same ``XBRL_DB_*`` / ``OPENAI_API_KEY`` env as the rest of the system.
"""
from __future__ import annotations

import os

# Disable HuggingFace tokenizers fork-parallelism *before* any transformers /
# FlagEmbedding import. The MCP tools are plain ``def``, so FastMCP runs them in
# worker threads; the first BGE/Jina tokenizer load can otherwise fork and stall
# the threaded handler. Harmless when the models are never touched.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from typing import Annotated, Any, Dict, List, Optional

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from src.logging_config import get_logger

logger = get_logger(__name__)

_INSTRUCTIONS = (
    "XbrlRag Analyst tools for SEC 10-K/10-Q fundamental analysis from tagged iXBRL "
    "filings. Workflow: call `coverage` to see which companies/fiscal years are "
    "ingested and `concepts` for the valid `concept` vocabulary, THEN use "
    "`facts_lookup` (deterministic numbers), `facts_compute` (ratios/growth), or "
    "`narrative_search` (qualitative prose). Use `answer` only for open-ended "
    "questions you can't decompose into the granular tools. Numbers are never "
    "invented — every fact carries a citation and a resolution status."
)

mcp = FastMCP(
    name="arag-analyst",
    instructions=_INSTRUCTIONS,
    host=os.getenv("XBRLRAG_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("XBRLRAG_MCP_PORT", "8765")),
)


def _client_error(exc: HTTPException) -> dict[str, Any]:
    """Map a 4xx HTTPException to a structured payload the LLM can read."""
    return {"error": str(exc.detail), "status_code": exc.status_code}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@mcp.tool()
def coverage(
    ticker: Annotated[str, Field(description="Optional exact-ticker filter, e.g. 'NVDA'. Empty = all.")] = "",
) -> dict[str, Any]:
    """List the companies, fiscal years, and form types currently ingested and
    queryable. Call this first to ground company/year arguments for the other
    tools — asking for an un-ingested company or year returns NOT_FOUND."""
    from src.api.v1 import router

    return router.coverage(ticker=ticker).model_dump(mode="json")


@mcp.tool()
def concepts() -> dict[str, Any]:
    """List the `concept_family` vocabulary accepted by `facts_lookup` /
    `facts_compute` (e.g. 'Revenue', 'NetIncome', 'TotalAssets'), each with an
    example XBRL QName. Use the returned `concept_family` strings verbatim."""
    from src.api.v1 import router

    return router.concepts().model_dump(mode="json")


@mcp.tool()
def resolve_company(
    company: Annotated[str, Field(description="Ticker, company name, or 10-digit CIK.")],
) -> dict[str, Any]:
    """Resolve a ticker / name / CIK to its canonical entity (CIK, ticker, fiscal
    year-end, last filed fiscal year). Returns an error payload if unresolved."""
    from src.api.v1 import router

    try:
        return router.resolve_company(company).model_dump(mode="json")
    except HTTPException as exc:
        return _client_error(exc)


# ---------------------------------------------------------------------------
# Facts (deterministic — zero LLM)
# ---------------------------------------------------------------------------

@mcp.tool()
def facts_lookup(
    company: Annotated[str, Field(description="Ticker or CIK, e.g. 'NVDA'.")],
    concept: Annotated[str, Field(description="A concept_family from `concepts`, e.g. 'Revenue'.")],
    fiscal_years: Annotated[list[int], Field(description="One call returns the full time series, e.g. [2024, 2025, 2026].")],
    dimension: Annotated[
        dict[str, str] | None,
        Field(description='Optional disaggregation hint, e.g. {"product": "data center"} or {"geography": "united states"}. Omit for the undisaggregated total.'),
    ] = None,
) -> dict[str, Any]:
    """Deterministic financial fact time series (no LLM). Returns one entry per
    fiscal year with `value`, `unit`, a `citation` (fact_id + filing anchor),
    `confidence`, and a `status`: OK | AMBIGUOUS | NOT_FOUND |
    UNRESOLVED_DIMENSION | ERROR. Always check `status` before trusting `value`;
    AMBIGUOUS means several facts matched (inspect, don't average)."""
    from src.api.v1 import router, schemas

    req = schemas.FactLookupRequest(
        company=company, concept=concept, fiscal_years=fiscal_years, dimension=dimension
    )
    return router.facts_lookup(req).model_dump(mode="json")


@mcp.tool()
def facts_compute(
    expr: Annotated[str, Field(description="Arithmetic over binding names: + - * / and parentheses only, e.g. '(rev26 - rev25) / rev25'.")],
    bindings: Annotated[
        list[dict[str, Any]],
        Field(description='Named facts. Each: {"name": "rev26", "company": "NVDA", "concept": "Revenue", "fiscal_year": 2026, "dimension": {..}?}. `name` must be a valid identifier used in `expr`.'),
    ],
) -> dict[str, Any]:
    """Compute a ratio / growth / spread over named facts with full provenance
    (no LLM does the arithmetic; a sandboxed evaluator does). Returns `value`,
    `ok`, and `resolved_bindings` (the cited fact behind each name). If any fact
    is missing or `expr` is malformed, `ok` is false and `error` explains."""
    from src.api.v1 import router, schemas

    try:
        req = schemas.ComputeRequest(
            expr=expr,
            bindings=[schemas.ComputeBinding(**b) for b in bindings],
        )
    except Exception as exc:  # pydantic validation of binding shape
        return {"ok": False, "error": f"invalid bindings: {exc}", "expr": expr, "resolved_bindings": {}}
    try:
        return router.facts_compute(req).model_dump(mode="json")
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail), "expr": expr, "resolved_bindings": {}}


# ---------------------------------------------------------------------------
# Narrative (qualitative — zero synthesis LLM)
# ---------------------------------------------------------------------------

@mcp.tool()
def narrative_search(
    company: Annotated[str, Field(description="Ticker or CIK.")],
    query: Annotated[str, Field(description="What to find, e.g. 'export control restrictions on China sales'.")],
    fiscal_years: Annotated[list[int] | None, Field(description="Restrict to these fiscal years, e.g. [2026].")] = None,
    section_hint: Annotated[
        str | None,
        Field(description="Soft topical steer folded into the query, e.g. 'Risk Factors', \"Management's Discussion and Analysis\". Not a hard filter."),
    ] = None,
    top_k: Annotated[int, Field(description="Max chunks to return.")] = 8,
) -> dict[str, Any]:
    """Retrieve qualitative passages (risk factors, MD&A, footnotes) with
    provenance — chunk text plus cik/fiscal_year/section_path/accession. No
    synthesis: returns the source chunks for you to read and reason over."""
    from src.api.v1 import router, schemas

    req = schemas.NarrativeSearchRequest(
        company=company, query=query, fiscal_years=fiscal_years,
        section_hint=section_hint, top_k=top_k,
    )
    return router.narrative_search(req).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Answer (full synthesized pipeline — 3-4 LLM calls)
# ---------------------------------------------------------------------------

@mcp.tool()
def answer(
    query: Annotated[str, Field(description="A full natural-language question.")],
    user_id: Annotated[str | None, Field(description="Optional caller id for audit correlation.")] = None,
) -> dict[str, Any]:
    """Full natural-language answer via XbrlRag's XBRL hybrid pipeline (intent-route
    -> plan -> execute -> synthesize -> verify; 3-4 LLM calls). Prefer the
    granular tools for structured analysis; use this for open-ended questions
    that mix numbers and prose. Returns `answer_summary`, cited `answer_details`,
    and a `query_id` for audit lookup."""
    from src.api.v1 import router, schemas

    try:
        return router.answer(schemas.AnswerRequest(query=query, user_id=user_id)).model_dump(mode="json")
    except HTTPException as exc:
        return {"error": str(exc.detail), "status_code": exc.status_code, "answer_summary": ""}


# ---------------------------------------------------------------------------
# Entrypoint / factory
# ---------------------------------------------------------------------------

def build_server() -> FastMCP:
    """Return the configured FastMCP server (tools registered at import)."""
    return mcp


def _warmup() -> None:
    """Eagerly load the heavy singletons so the first real tool call is on the
    warm path instead of paying the ~50s cold model-load latency.

    The narrative tools dominate latency: ``jina-reranker-v3`` (a local
    ``transformers`` model — *not* an API call, so no ``JINA_API_KEY`` /
    network is involved) plus ``BAAI/bge-m3`` are ~9GB of CPU model and the
    first call would otherwise load them inline. We force-load them here (plus
    the Qdrant client and the fact/entity DB components) and run a tiny dummy
    inference to warm the torch graph.

    Runs in a daemon thread launched from :func:`main` so the server can answer
    ``initialize`` / ``tools/list`` and the light tools immediately while the
    models load in the background. Every step is best-effort: a missing backend
    is logged and swallowed so warm-up never prevents the server from serving.
    """
    import time

    t0 = time.time()
    logger.info("MCP warm-up: preloading models/clients in background ...")
    from src.api.v1 import service

    try:
        service.entity_extractor()
        service.factstore()
        service.calculator()
        logger.info("MCP warm-up: fact/entity/calculator ready (%.1fs)", time.time() - t0)
    except Exception:
        logger.exception("MCP warm-up: fact/entity components failed (non-fatal)")

    try:
        nr = service.narrative()
        nr.embedding_generator.embed_texts([("warmup", "warmup")])  # loads BGE-M3
        _ = nr.vector_db_client                                     # connects Qdrant
        nr.reranker.rerank("warmup", ["warmup document text"])      # loads + warms Jina
        logger.info("MCP warm-up: narrative models ready")
    except Exception:
        logger.exception("MCP warm-up: narrative models failed (non-fatal)")

    logger.info("MCP warm-up: complete in %.1fs", time.time() - t0)


def _warmup_enabled() -> bool:
    return os.getenv("XBRLRAG_MCP_WARMUP", "1").strip().lower() not in ("0", "false", "no", "off")


def main() -> None:
    transport = os.getenv("XBRLRAG_MCP_TRANSPORT", "stdio")
    logger.info("starting XbrlRag MCP server (transport=%s)", transport)
    if _warmup_enabled():
        import threading

        threading.Thread(target=_warmup, name="xbrlrag-mcp-warmup", daemon=True).start()
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http")
    elif transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
