"""Typed request/response contracts for the v1 Analyst API.

These are deliberately concrete (no passthrough ``dict`` answers) so a
programmatic caller — e.g. an LLM tool-calling flow — can pin a stable schema
and codegen tool definitions from ``/openapi.json``.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

class ResolvedCompanyModel(BaseModel):
    """Outcome of resolving a ticker/name/CIK against ``entity_master``."""
    cik: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    fye_month: int | None = None
    fye_day: int | None = None
    last_seen_fy: int | None = None
    resolution: str = "unresolved"   # exact_ticker | alias | exact_name | fuzzy_name | ambiguous | unresolved
    similarity: float = 0.0


class UsageModel(BaseModel):
    """Paid-model spend behind one response (docs/specs/usage-cost.md).

    ``cost_usd`` is ``0.0`` when no paid call was made — never omitted, so a
    caller can tell "free" from "unknown". Self-hosted embedding/rerank compute
    is not priced; only ``LLMGateway`` calls carry a cost.
    """
    cost_usd: float = Field(..., ge=0, allow_inf_nan=False,
                            description="Total USD cost of the paid model calls behind this response.")
    llm_calls: int = Field(..., ge=0, description="Number of LLM gateway calls made.")


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# Coverage / concepts (discovery)
# ---------------------------------------------------------------------------

class CoverageItem(BaseModel):
    cik: str
    ticker: str | None = None
    company_name: str | None = None
    fiscal_years: list[int] = []
    form_types: list[str] = []


class CoverageResponse(BaseModel):
    items: list[CoverageItem] = []


class ConceptItem(BaseModel):
    concept_family: str
    example_qname: str | None = None


class ConceptsResponse(BaseModel):
    items: list[ConceptItem] = []


# ---------------------------------------------------------------------------
# Facts: lookup (deterministic time series)
# ---------------------------------------------------------------------------

class FactLookupRequest(BaseModel):
    company: str = Field(..., description="Ticker or CIK, e.g. 'NVDA' or '0001045810'")
    concept: str = Field(..., description="A concept_family value, e.g. 'Revenue' (see GET /api/v1/concepts)")
    fiscal_years: list[int] = Field(..., min_length=1, description="One call returns the full series, ordered")
    dimension: dict[str, str] | None = Field(
        None, description='Optional dimension hint, e.g. {"product": "data center"}. Omit for the undisaggregated total.'
    )
    fiscal_period: str = Field("FY", description="'FY' (annual, default) or 'Q1'|'Q2'|'Q3'|'Q4' for a 10-Q quarter")
    ytd: bool = Field(False, description="For a quarter, return the cumulative year-to-date figure instead of the discrete quarter")
    as_of: str | None = Field(
        None, description="Point-in-time: value as it stood in the latest filing available on this ISO date (YYYY-MM-DD)")
    as_originally_reported: bool = Field(
        False, description="Point-in-time: read the original (non-amendment) filing, ignoring later restatements")


class FactPoint(BaseModel):
    fiscal_year: int
    status: str                          # OK | AMBIGUOUS | NOT_FOUND | UNRESOLVED_DIMENSION | ERROR
    value: float | None = None
    unit: str | None = None
    confidence: float = 0.0
    citation: dict | None = None      # FactLookupResult.citation.to_dict()
    notes: list[str] = []
    error: str | None = None


class FactLookupResponse(BaseModel):
    company: ResolvedCompanyModel
    concept: str
    unresolved: bool = False             # company could not be resolved to a CIK
    series: list[FactPoint] = []
    usage: UsageModel


# ---------------------------------------------------------------------------
# Facts: compute (citation-traced ratios / growth)
# ---------------------------------------------------------------------------

class ComputeBinding(BaseModel):
    """One named fact bound to an identifier usable in the compute ``expr``."""
    name: str = Field(..., description="Identifier referenced in expr, e.g. 'rev26'. Letters/digits/underscore.")
    company: str
    concept: str
    fiscal_year: int
    dimension: dict[str, str] | None = None


class ComputeRequest(BaseModel):
    expr: str = Field(
        ...,
        description="Arithmetic over binding names: + - * / and parentheses only, "
                    "e.g. '(rev26 - rev25) / rev25'.",
    )
    bindings: list[ComputeBinding] = Field(..., min_length=1)


class ComputeResponse(BaseModel):
    expr: str
    value: float | None = None
    ok: bool = False
    error: str | None = None
    # The resolved fact behind each binding, for the caller's provenance trail.
    resolved_bindings: dict[str, FactPoint] = {}


# ---------------------------------------------------------------------------
# Metrics grid (tabular / CSV) — derived ratios + raw line-items, cited
# ---------------------------------------------------------------------------

class MetricCatalogItem(BaseModel):
    metric_name: str
    formula: str | None = None


class MetricCatalogResponse(BaseModel):
    # Derived ratios (mv_derived_metrics). Raw line-items are addressable by any
    # concept_family from GET /api/v1/concepts.
    items: list[MetricCatalogItem] = []


class MetricsGridRequest(BaseModel):
    companies: list[str] = Field(..., min_length=1, description="Tickers/names/CIKs, one column group each")
    metrics: list[str] = Field(
        ..., min_length=1,
        description="Derived metric names (GET /api/v1/metrics/catalog) and/or "
                    "concept_family line-items (GET /api/v1/concepts).",
    )
    fiscal_years: list[int] = Field(..., min_length=1)
    fiscal_period: str = Field("FY", description="'FY' (annual) or 'Q1'|'Q2'|'Q3'|'Q4'")


class MetricCell(BaseModel):
    cik: str | None = None
    ticker: str | None = None
    fiscal_year: int
    fiscal_period: str
    metric: str
    kind: str                            # 'derived' | 'fact'
    value: float | None = None
    unit: str | None = None
    formula: str | None = None
    component_fact_ids: dict | None = None
    citation: dict | None = None


class MetricsGridResponse(BaseModel):
    companies: list[ResolvedCompanyModel] = []
    fiscal_period: str = "FY"
    cells: list[MetricCell] = []


# ---------------------------------------------------------------------------
# Variance / bridge
# ---------------------------------------------------------------------------

class BridgeRequest(BaseModel):
    company: str
    concept: str = Field("Revenue", description="concept_family to decompose (e.g. 'Revenue')")
    fiscal_year: int
    dimension: str = Field("segment", description="'segment' | 'product' | 'geography'")
    fiscal_period: str = "FY"


class BridgeComponent(BaseModel):
    member: str | None = None
    prior: float | None = None
    current: float | None = None
    change: float | None = None
    pct_change: float | None = None
    fact_id_current: int | None = None
    fact_id_prior: int | None = None


class BridgeResponse(BaseModel):
    company: ResolvedCompanyModel
    concept: str
    fiscal_year: int
    fiscal_period: str
    dimension: str
    total_prior: float | None = None
    total_current: float | None = None
    total_change: float | None = None
    explained_change: float | None = None       # sum of component changes
    unexplained: float | None = None            # total_change - explained (new/exited members, rounding)
    components: list[BridgeComponent] = []
    # Set when the two compared periods differ by 6+ days (a 53-week year); the change
    # stays on the reported basis (docs/specs/period-disclosure.md R3, R7.4).
    year_length_note: str | None = None


# ---------------------------------------------------------------------------
# Screen (cross-filer ranking)
# ---------------------------------------------------------------------------

class ScreenRequest(BaseModel):
    """Rank filers by a metric across all companies — 'which companies have…'."""
    concept_family: str = Field("Revenue", description="e.g. 'Revenue', 'Debt' (see GET /api/v1/concepts)")
    fy: int = Field(..., description="Fiscal-year ceiling — latest FY ≤ fy per filer")
    dimension: dict[str, str] | None = Field(
        None,
        description='Slice: {"geography":"China"} or {"segment":"Data Center"}. Omit for the undisaggregated total.',
    )
    metric_kind: str = Field(
        "value",
        description="'value' (default) | 'pct_of_total' (share) | 'derived:<MetricName>' (a ratio from the catalog)",
    )
    order: str = Field("desc", description="'desc' (default) | 'asc'")
    top_n: int | None = Field(None, description="Cap the ranked list to N rows")
    min_threshold: float | None = Field(
        None, description="Floor on the sort key; self-heals (ignored) if it would exclude every filer")
    fiscal_period: str = Field("FY", description="'FY' or 'Q1'|'Q2'|'Q3'|'Q4'")
    ytd: bool = Field(False, description="For a quarter, use the cumulative year-to-date figure")


class ScreenRow(BaseModel):
    rank: int | None = None
    ticker: str | None = None
    cik: str | None = None
    fiscal_year: int | None = None
    value: float | None = None
    unit: str | None = None
    pct_of_total: float | None = None
    label: str | None = None
    fact_id: str | None = None          # per-row citation anchor (synthetic for derived rows)
    formula: str | None = None          # derived screens only
    component_fact_ids: dict | None = None
    citation: dict | None = None


class ScreenResponse(BaseModel):
    status: str                            # OK | NOT_FOUND | ERROR
    concept_family: str
    fy: int
    metric_kind: str
    n_rows: int = 0
    rows: list[ScreenRow] = []
    resolution_notes: list[str] = []
    error: str | None = None


# ---------------------------------------------------------------------------
# Narrative search (qualitative chunks, no synthesis)
# ---------------------------------------------------------------------------

class NarrativeSearchRequest(BaseModel):
    company: str
    query: str = Field(..., min_length=1)
    fiscal_years: list[int] | None = None
    section_hint: str | None = Field(
        None,
        description="Soft hint folded into the query, e.g. 'Risk Factors', "
                    "'Management's Discussion and Analysis'. Not a hard filter.",
    )
    top_k: int = 8


class NarrativeChunkModel(BaseModel):
    chunk_id: str
    text: str
    section_path: str | None = None
    cik: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    fiscal_year: int | None = None
    form_type: str | None = None
    accession_no: str | None = None
    page_num: int | None = None
    rerank_score: float | None = None
    is_expansion: bool = False


class NarrativeSearchResponse(BaseModel):
    company: ResolvedCompanyModel
    query: str
    chunks: list[NarrativeChunkModel] = []
    usage: UsageModel


# ---------------------------------------------------------------------------
# Answer (full synthesized pipeline)
# ---------------------------------------------------------------------------

class AnswerRequest(BaseModel):
    query: str = Field(..., min_length=1)
    user_id: str | None = None


class AnswerResponse(BaseModel):
    query_id: str | None = None
    answer_summary: str = ""
    answer_details: list[dict] = []
    unanswered_parts: list[dict] = []
    metadata: dict = {}
    error: str | None = None
