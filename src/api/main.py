"""XbrlRag BFF entrypoint.

    uvicorn src.api.main:app --reload --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import db
from src.api.config import cors_origins
from src.api.pipelines import pipelines_importable
from src.api.routes import eval as eval_routes
from src.api.routes import metrics_prom, observability, query
from src.api.schemas import HealthResponse
from src.api.v1 import analyst_router

app = FastAPI(
    title="XbrlRag UI BFF",
    version="0.1.0",
    description="Backend-for-frontend for the XbrlRag React UI: wraps the retrieval "
    "pipelines and reads the observability/audit views.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query.router)
app.include_router(observability.router)
app.include_router(eval_routes.router)
app.include_router(metrics_prom.router)
app.include_router(analyst_router)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    ok, err = db.db_available()
    return HealthResponse(
        status="ok",
        audit_db=ok,
        audit_db_error=err,
        pipelines=pipelines_importable(),
    )
