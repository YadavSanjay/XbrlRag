"""v1 Analyst API routes — thin adapters over :mod:`src.api.v1.service`.

Each route maps a typed request to a service call and the domain result back to
a typed response. Failures surface as real HTTP status codes (not the BFF's
swallow-to-200 behavior) so a programmatic caller can branch on them.

No authentication: this API is intended to run on a trusted private network /
localhost alongside its callers (e.g. Faultline's ``scenariogen``), the
same trust model as the UI BFF. Put it behind a gateway before any public
exposure.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from src.api import pipelines
from src.api.v1 import service
from src.api.v1.schemas import (
    AnswerRequest,
    AnswerResponse,
    BridgeComponent,
    BridgeRequest,
    BridgeResponse,
    ComputeRequest,
    ComputeResponse,
    ConceptItem,
    ConceptsResponse,
    CoverageItem,
    CoverageResponse,
    FactLookupRequest,
    FactLookupResponse,
    FactPoint,
    MetricCatalogItem,
    MetricCatalogResponse,
    MetricCell,
    MetricsGridRequest,
    MetricsGridResponse,
    NarrativeChunkModel,
    NarrativeSearchRequest,
    NarrativeSearchResponse,
    ResolvedCompanyModel,
    ScreenRequest,
    ScreenResponse,
    ScreenRow,
    UsageModel,
)
from src.logging_config import get_logger
from src.observability.usage_meter import UsageMeter

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["analyst"])

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ---------------------------------------------------------------------------
# Mappers (domain -> wire)
# ---------------------------------------------------------------------------

def _company_model(rc) -> ResolvedCompanyModel:
    return ResolvedCompanyModel(
        cik=rc.cik, ticker=rc.ticker, company_name=rc.company_name,
        fye_month=rc.fye_month, fye_day=rc.fye_day, last_seen_fy=rc.last_seen_fy,
        resolution=rc.resolution, similarity=round(getattr(rc, "similarity", 0.0) or 0.0, 3),
    )


def _fact_point(fiscal_year: int, result) -> FactPoint:
    return FactPoint(
        fiscal_year=fiscal_year,
        status=result.status.value,
        value=result.value,
        unit=result.unit,
        confidence=round(result.confidence, 3),
        citation=result.citation.to_dict() if result.citation else None,
        notes=list(result.resolution_notes or []),
        error=result.error,
    )


def _chunk_model(c) -> NarrativeChunkModel:
    return NarrativeChunkModel(
        chunk_id=c.chunk_id, text=c.text, section_path=c.section_path,
        cik=c.cik, ticker=c.ticker, company_name=c.company_name,
        fiscal_year=c.fiscal_year, form_type=c.form_type, accession_no=c.accession_no,
        page_num=c.page_num,
        rerank_score=round(c.rerank_score, 4) if c.rerank_score is not None else None,
        is_expansion=c.is_expansion,
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@router.get("/coverage", response_model=CoverageResponse)
def coverage(ticker: str = Query("", description="Optional exact-ticker filter")):
    """What companies / fiscal years / form types are ingested and queryable."""
    rows = service.coverage(ticker)
    items = [
        CoverageItem(
            cik=r["cik"], ticker=r.get("ticker"), company_name=r.get("company_name"),
            fiscal_years=list(r.get("fiscal_years") or []),
            form_types=list(r.get("form_types") or []),
        )
        for r in rows
    ]
    return CoverageResponse(items=items)


@router.get("/concepts", response_model=ConceptsResponse)
def concepts():
    """The ``concept_family`` vocabulary accepted by the fact endpoints."""
    rows = service.concepts()
    return ConceptsResponse(
        items=[ConceptItem(concept_family=r["concept_family"], example_qname=r.get("example_qname")) for r in rows]
    )


@router.get("/companies/{company}", response_model=ResolvedCompanyModel)
def resolve_company(company: str):
    """Resolve a ticker / name / CIK to its canonical entity + metadata."""
    rc = service.resolve_company(company)
    if not rc.cik:
        raise HTTPException(status_code=404, detail=f"could not resolve company {company!r}")
    return _company_model(rc)


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

@router.post("/facts/lookup", response_model=FactLookupResponse)
def facts_lookup(req: FactLookupRequest):
    """Deterministic fact time series — zero synthesis LLM calls. Supports 10-Q
    quarters (fiscal_period/ytd) and point-in-time (as_of / as_originally_reported).
    ``usage.cost_usd`` is the metered LLM spend (0.0: SQL only)."""
    with UsageMeter() as meter:
        rc, results = service.lookup_series(
            req.company, req.concept, req.fiscal_years, req.dimension,
            req.fiscal_period, req.ytd, req.as_of, req.as_originally_reported)
    series = [_fact_point(fy, r) for fy, r in zip(req.fiscal_years, results)]
    return FactLookupResponse(
        company=_company_model(rc), concept=req.concept,
        unresolved=not rc.cik, series=series,
        usage=UsageModel.model_validate(meter.usage()),
    )


@router.post("/facts/compute", response_model=ComputeResponse)
def facts_compute(req: ComputeRequest):
    """Evaluate an arithmetic expr over named facts; returns the value plus the
    resolved fact behind each binding for a full provenance trail."""
    names = [b.name for b in req.bindings]
    for n in names:
        if not _IDENT_RE.match(n):
            raise HTTPException(status_code=422, detail=f"invalid binding name {n!r} (must be an identifier)")
    if len(set(names)) != len(names):
        raise HTTPException(status_code=422, detail="duplicate binding names")

    resolved: dict = {}
    values: dict = {}
    for b in req.bindings:
        rc = service.resolve_company(b.company)
        if not rc.cik:
            resolved[b.name] = FactPoint(fiscal_year=b.fiscal_year, status="NOT_FOUND",
                                         notes=[f"unresolved company {b.company!r}"])
            values[b.name] = None
            continue
        result = service.lookup_one(rc.cik, rc.ticker, b.concept, b.fiscal_year, b.dimension)
        resolved[b.name] = _fact_point(b.fiscal_year, result)
        values[b.name] = result.value

    from src.retriever.xbrl.xbrl_calculator_tool import CalculatorError

    try:
        value = service.calculator().evaluate(req.expr, values)
        return ComputeResponse(expr=req.expr, value=value, ok=True, resolved_bindings=resolved)
    except CalculatorError as exc:
        return ComputeResponse(expr=req.expr, value=None, ok=False,
                               error=str(exc), resolved_bindings=resolved)


# ---------------------------------------------------------------------------
# Variance / bridge
# ---------------------------------------------------------------------------

@router.post("/facts/bridge", response_model=BridgeResponse)
def facts_bridge(req: BridgeRequest):
    """Decompose a total metric's YoY change into its segment/geography children,
    reconciling to an ``unexplained`` residual. Deterministic — no LLM."""
    rc, bridge = service.variance_bridge(
        req.company, req.concept, req.fiscal_year, req.dimension, req.fiscal_period)
    if bridge is None:
        raise HTTPException(
            status_code=404,
            detail=f"no {req.dimension} YoY data for {req.company} {req.concept} FY{req.fiscal_year} "
                   f"{req.fiscal_period} (unresolved company, or dimension not tagged)")
    return BridgeResponse(
        company=_company_model(rc),
        concept=bridge["concept"], fiscal_year=bridge["fiscal_year"],
        fiscal_period=bridge["fiscal_period"], dimension=bridge["dimension"],
        total_prior=bridge["total_prior"], total_current=bridge["total_current"],
        total_change=bridge["total_change"], explained_change=bridge["explained_change"],
        unexplained=bridge["unexplained"],
        components=[BridgeComponent(**c) for c in bridge["components"]],
        year_length_note=bridge.get("year_length_note"),
    )


# ---------------------------------------------------------------------------
# Metrics grid (tabular / CSV)
# ---------------------------------------------------------------------------

@router.get("/metrics/catalog", response_model=MetricCatalogResponse)
def metrics_catalog():
    """The derived-ratio library available in the metrics grid. Raw line-items
    are additionally addressable by any ``concept_family`` from ``/concepts``."""
    rows = service.metrics_catalog()
    return MetricCatalogResponse(
        items=[MetricCatalogItem(metric_name=r["metric_name"], formula=r.get("formula")) for r in rows]
    )


def _grid_cells(req: MetricsGridRequest):
    resolved, cells = service.metrics_grid(req.companies, req.metrics, req.fiscal_years, req.fiscal_period)
    return resolved, cells


@router.post("/metrics", response_model=MetricsGridResponse)
def metrics_grid(req: MetricsGridRequest):
    """A cited metric x period grid (long form): each requested metric is a
    derived ratio or a raw concept_family line-item, per company per fiscal year.
    Deterministic — no synthesis LLM call. See ``/metrics.csv`` for a spreadsheet."""
    resolved, cells = _grid_cells(req)
    return MetricsGridResponse(
        companies=[_company_model(rc) for rc in resolved],
        fiscal_period=(req.fiscal_period or "FY").upper(),
        cells=[MetricCell(**c) for c in cells],
    )


@router.post("/metrics.csv", response_class=PlainTextResponse)
def metrics_grid_csv(req: MetricsGridRequest):
    """Same grid as ``POST /metrics`` but pivoted to CSV for direct download /
    Excel: one row per (ticker, fiscal_year, fiscal_period), one column per
    metric. ``text/csv`` so a browser/``curl -o`` saves it straight to a file."""
    import csv
    import io

    _resolved, cells = _grid_cells(req)
    metrics = list(dict.fromkeys(req.metrics))  # preserve requested order, de-dup
    # key: (ticker, fy, fp) -> {metric: value}
    grid: dict = {}
    order: list = []
    for c in cells:
        key = (c["ticker"] or c["cik"], c["fiscal_year"], c["fiscal_period"])
        if key not in grid:
            grid[key] = {}
            order.append(key)
        grid[key][c["metric"]] = c["value"]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ticker", "fiscal_year", "fiscal_period", *metrics])
    for (ticker, fy, fp) in sorted(order):
        w.writerow([ticker, fy, fp, *(grid[(ticker, fy, fp)].get(m, "") for m in metrics)])

    filename = f"metrics_{(req.fiscal_period or 'FY').upper()}.csv"
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Screen (cross-filer ranking)
# ---------------------------------------------------------------------------

@router.post("/screen", response_model=ScreenResponse)
def screen(req: ScreenRequest):
    """Rank all filers by a metric — the deterministic cross-filer screen engine
    (previously reachable only through a natural-language ``NUMERIC_SCREEN`` query),
    now a typed endpoint. Each row carries its own citation anchor. No LLM."""
    result = service.screen(
        concept_family=req.concept_family, fy=req.fy, dimension=req.dimension,
        metric_kind=req.metric_kind, order=req.order, top_n=req.top_n,
        min_threshold=req.min_threshold, fiscal_period=req.fiscal_period, ytd=req.ytd,
    )
    rows = [ScreenRow(**r) for r in result.get("rows", [])]
    return ScreenResponse(
        status=result.get("status", "ERROR"),
        concept_family=req.concept_family,
        fy=req.fy,
        metric_kind=req.metric_kind,
        n_rows=len(rows),
        rows=rows,
        resolution_notes=result.get("resolution_notes", []),
        error=result.get("error"),
    )


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

@router.post("/narrative/search", response_model=NarrativeSearchResponse)
def narrative_search(req: NarrativeSearchRequest):
    """Retrieve risk-factor / MD&A / other qualitative chunks with provenance —
    no synthesis LLM call. ``usage.cost_usd`` is the metered LLM spend; the
    self-hosted embedding and rerank are not priced."""
    with UsageMeter() as meter:
        rc, result = service.narrative_search(
            req.company, req.query, req.fiscal_years, req.section_hint, req.top_k
        )
    chunks = [_chunk_model(c) for c in getattr(result, "chunks", [])]
    return NarrativeSearchResponse(company=_company_model(rc), query=req.query, chunks=chunks,
                                   usage=UsageModel.model_validate(meter.usage()))


# ---------------------------------------------------------------------------
# Answer (full synthesized pipeline)
# ---------------------------------------------------------------------------

@router.post("/answer", response_model=AnswerResponse)
def answer(req: AnswerRequest):
    """Full natural-language answer via the XBRL hybrid pipeline (3-4 LLM calls).
    Reuses the same cached pipeline as the UI BFF."""
    try:
        ans, query_id = pipelines.run_xbrl(req.query, user_id=req.user_id)
    except Exception as exc:  # pragma: no cover - surfaced as typed error
        logger.exception("v1 answer failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return AnswerResponse(
        query_id=query_id,
        answer_summary=ans.get("answer_summary", ""),
        answer_details=ans.get("answer_details", []),
        unanswered_parts=ans.get("unanswered_parts", []),
        metadata=ans.get("metadata", {}),
    )
