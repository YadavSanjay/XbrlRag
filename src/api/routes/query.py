"""Query-execution endpoints — wrap the retrieval pipelines."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from src.api import db, pipelines
from src.api.schemas import QueryMode, QueryRequest, QueryResponse
from src.api.streaming import event_stream
from src.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["query"])


@router.post("/query", response_model=QueryResponse)
def run_query(req: QueryRequest) -> QueryResponse:
    """Run a query through the selected pipeline and return the raw answer dict
    (RagResponse / XbrlRagResponse) plus the trace ``query_id`` when available."""
    try:
        if req.mode in (QueryMode.AUTO, QueryMode.XBRL):
            answer, query_id = pipelines.run_xbrl(req.query, user_id=req.user_id)
        else:
            answer, query_id = pipelines.run_sec10k(req.query, req.mode.value, top_k=req.top_k)
        return QueryResponse(query_id=query_id, mode=req.mode, answer=answer)
    except Exception as exc:  # surface as a clean error, not a 500 stack
        logger.exception("query failed")
        return QueryResponse(query_id=None, mode=req.mode, answer={}, error=f"{type(exc).__name__}: {exc}")


@router.get("/query/stream")
def stream_query(query: str, mode: QueryMode = QueryMode.AUTO, user_id: str = ""):
    """SSE stream of live pipeline events for *query*, terminated by a ``result``
    event carrying the answer + ``query_id``. Consumed by the Ask page via
    ``EventSource``. Use GET (EventSource is GET-only); ``query`` is a param.
    """
    return StreamingResponse(
        event_stream(query, mode.value, user_id or None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/companies")
def companies(q: str = "", limit: int = 10):
    """Typeahead over ``entity_master`` for the Ask composer scope filter."""
    if not q:
        return {"items": []}
    rows = db.query(
        """
        SELECT DISTINCT trading_symbol AS ticker, company_name, cik
        FROM entity_master
        WHERE trading_symbol ILIKE %s OR company_name ILIKE %s
        ORDER BY trading_symbol
        LIMIT %s
        """,
        (f"%{q}%", f"%{q}%", limit),
    )
    return {"items": rows}
