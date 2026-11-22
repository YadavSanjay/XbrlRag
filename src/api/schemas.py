"""Request/response models for the BFF.

These are deliberately thin: query responses pass through the pipeline's own
``XbrlRagResponse`` / ``RagResponse`` shape as ``dict`` (so the UI sees the real
fields), and observability responses are JSON rows from the audit views.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QueryMode(str, Enum):
    AUTO = "auto"        # XBRL hybrid pipeline (intent-routed)
    XBRL = "xbrl"
    BASIC = "basic"      # sec10k narrative RAG
    HYDE = "hyde"
    SUBQUERY = "subquery"


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    mode: QueryMode = QueryMode.AUTO
    user_id: str | None = None
    top_k: int = 10


class QueryResponse(BaseModel):
    """Pipeline answer plus the correlation id that links to the trace."""
    query_id: str | None = None
    mode: QueryMode
    answer: dict[str, Any]   # the raw RagResponse / XbrlRagResponse dict
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    audit_db: bool
    audit_db_error: str | None = None
    pipelines: dict[str, bool]
