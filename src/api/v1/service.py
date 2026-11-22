"""Service layer for the v1 Analyst API.

Holds lazily-constructed, lock-guarded singletons of the existing XBRL
components (same pattern as ``src/api/pipelines.py``) and thin wrappers that
adapt their call signatures to the endpoint shapes. No business logic lives
here that isn't already in the wrapped component — this is glue, not a reimpl.

Construction is expensive (DB clients, BGE/Jina models, LLM clients), so each
component is built once on first use. The heavy models are shared process-wide,
so concurrent external callers serialize on them — see the README note.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

from src.api import db
from src.logging_config import get_logger
from src.retriever.xbrl.period_disclosure import year_length_disclosure

logger = get_logger(__name__)

_lock = threading.Lock()
_entity_extractor = None
_factstore = None
_narrative = None
_calculator = None
_screen = None


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

def entity_extractor():
    global _entity_extractor
    with _lock:
        if _entity_extractor is None:
            from src.retriever.xbrl.entity_extractor import EntityExtractor

            _entity_extractor = EntityExtractor.from_env()
        return _entity_extractor


def factstore():
    global _factstore
    with _lock:
        if _factstore is None:
            from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup

            _factstore = XbrlFactstoreLookup.from_env()
        return _factstore


def narrative():
    global _narrative
    with _lock:
        if _narrative is None:
            from src.retriever.xbrl.xbrl_narrative_retriever import XbrlNarrativeRetriever

            _narrative = XbrlNarrativeRetriever()
        return _narrative


def calculator():
    global _calculator
    with _lock:
        if _calculator is None:
            from src.retriever.xbrl.xbrl_calculator_tool import XbrlCalculatorTool

            _calculator = XbrlCalculatorTool()
        return _calculator


def screen_tool():
    global _screen
    with _lock:
        if _screen is None:
            from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

            _screen = XbrlFactstoreScreen.from_env()
        return _screen


def screen(*, concept_family: str = "Revenue", fy: int, dimension: dict[str, str] | None = None,
           metric_kind: str = "value", order: str = "desc", top_n: int | None = None,
           min_threshold: float | None = None, fiscal_period: str = "FY",
           ytd: bool = False) -> dict:
    """Run one deterministic cross-filer screen and return its ranked rows.

    Thin typed wrapper over :class:`XbrlFactstoreScreen` — the same engine the NL
    pipeline uses for a ``screen`` plan step, now reachable directly. Returns the
    ``ScreenResult.to_dict()`` shape (``status`` / ``rows`` / ``resolution_notes``
    / ``error``). No LLM.
    """
    step = {
        "id": "api_screen",
        "op": "screen",
        "concept_family": concept_family,
        "fy": fy,
        "dimension": dimension if dimension else "NONE",
        "metric_kind": metric_kind,
        "order": order,
        "top_n": top_n,
        "min_threshold": min_threshold,
        "fiscal_period": (fiscal_period or "FY").upper(),
        "ytd": ytd,
    }
    return screen_tool().screen(step).to_dict()


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

def resolve_company(company: str):
    """Resolve one ticker/name/CIK to a ``ResolvedCompany`` (or unresolved)."""
    results = entity_extractor().resolve_companies([company])
    if results:
        return results[0]
    from src.retriever.xbrl.entity_extractor import ResolvedCompany

    return ResolvedCompany(mention=company, resolution="unresolved")


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def _lookup_step(cik: str, ticker: str | None, concept: str, fy: int,
                 dimension: dict[str, str] | None,
                 fiscal_period: str = "FY", ytd: bool = False,
                 as_of: str | None = None, as_originally_reported: bool = False):
    """Build the ``lookup`` plan step the factstore expects. ``period_type`` is
    derived inside the factstore from the concept family."""
    step = {
        "id": f"{concept}-{fy}",
        "op": "lookup",
        "cik": cik,
        "ticker": ticker,
        "fy": fy,
        "fiscal_period": (fiscal_period or "FY").upper(),
        "ytd": ytd,
        "concept_family": concept,
        "dimensions": dimension or "NONE",
    }
    if as_of:
        step["as_of"] = as_of
    if as_originally_reported:
        step["as_originally_reported"] = True
    return step


def lookup_one(cik: str, ticker: str | None, concept: str, fy: int,
               dimension: dict[str, str] | None = None,
               fiscal_period: str = "FY", ytd: bool = False,
               as_of: str | None = None, as_originally_reported: bool = False):
    """Resolve a single (cik, concept, fy[, dimension]) to a ``FactLookupResult``."""
    return factstore().lookup(_lookup_step(
        cik, ticker, concept, fy, dimension, fiscal_period, ytd, as_of, as_originally_reported))


def lookup_series(company: str, concept: str, fiscal_years: list[int],
                  dimension: dict[str, str] | None = None,
                  fiscal_period: str = "FY", ytd: bool = False,
                  as_of: str | None = None,
                  as_originally_reported: bool = False) -> tuple[object, list]:
    """Resolve *company*, then look up *concept* for each fiscal year.

    Returns ``(resolved_company, results)`` where ``results`` is aligned with
    *fiscal_years*. If the company can't be resolved, ``results`` is empty.
    """
    rc = resolve_company(company)
    if not rc.cik:
        logger.info("v1 facts/lookup: unresolved company %r", company)
        return rc, []
    results = [lookup_one(rc.cik, rc.ticker, concept, fy, dimension,
                          fiscal_period, ytd, as_of, as_originally_reported)
               for fy in fiscal_years]
    return rc, results


# ---------------------------------------------------------------------------
# Variance / bridge — decompose a total's YoY change into its dimension children
# ---------------------------------------------------------------------------

# The duration a bridge compares: the year for FY, the discrete quarter for a quarter.
_BRIDGE_DURATION_KIND = {"FY": "annual", "Q1": "quarter", "Q2": "quarter", "Q3": "quarter"}

_BRIDGE_AXIS = {
    "segment": "%StatementBusinessSegmentsAxis",
    "business": "%StatementBusinessSegmentsAxis",
    "product": "%ProductOrServiceAxis",
    "geography": "%StatementGeographicalAxis",
    "geographic": "%StatementGeographicalAxis",
    "region": "%StatementGeographicalAxis",
}


def variance_bridge(company: str, concept: str, fy: int, dimension: str = "segment",
                    fiscal_period: str = "FY") -> tuple[object, dict | None]:
    """Explain a total metric's YoY change by its dimension children (segment or
    geography), fully from tagged facts (``mv_yoy_comparison``).

    Returns ``(resolved_company, bridge)`` where *bridge* reconciles the
    undimensioned total change against the sum of per-child changes (any gap is
    surfaced as an ``unexplained`` residual — new/discontinued members or
    rounding). Deterministic — no LLM.
    """
    rc = resolve_company(company)
    if not rc.cik:
        return rc, None
    axis_like = _BRIDGE_AXIS.get(dimension.strip().lower(), "%StatementBusinessSegmentsAxis")
    fiscal_period = (fiscal_period or "FY").upper()
    # One duration per side: a 10-K can tag quarter-length member facts and a 10-Q
    # files the quarter beside its year-to-date (docs/specs/period-disclosure.md R7.3).
    duration_kind = _BRIDGE_DURATION_KIND.get(fiscal_period)

    total = db.query_one(
        """
        SELECT value_current, value_prior, yoy_change, fact_id_current, fact_id_prior,
               period_start_current, period_end_current, period_start_prior, period_end_prior
        FROM mv_yoy_comparison
        WHERE cik = %s AND concept_family = %s AND fy_current = %s AND fiscal_period = %s
          AND (%s::text IS NULL OR duration_kind = %s)
          AND segment_axis IS NULL
        ORDER BY abs(yoy_change) DESC NULLS LAST
        LIMIT 1
        """,
        (rc.cik, concept, fy, fiscal_period, duration_kind, duration_kind),
    )
    children = db.query(
        """
        SELECT segment_member, segment_label, value_current, value_prior, yoy_change,
               yoy_pct_change, fact_id_current, fact_id_prior
        FROM mv_yoy_comparison
        WHERE cik = %s AND concept_family = %s AND fy_current = %s AND fiscal_period = %s
          AND (%s::text IS NULL OR duration_kind = %s)
          AND segment_axis LIKE %s
        ORDER BY yoy_change DESC NULLS LAST
        """,
        (rc.cik, concept, fy, fiscal_period, duration_kind, duration_kind, axis_like),
    )
    if not total and not children:
        return rc, None

    total_change = float(total["yoy_change"]) if total and total.get("yoy_change") is not None else None
    child_sum = sum(float(c["yoy_change"]) for c in children if c.get("yoy_change") is not None)
    components = [
        {
            "member": c.get("segment_label") or c.get("segment_member"),
            "prior": float(c["value_prior"]) if c.get("value_prior") is not None else None,
            "current": float(c["value_current"]) if c.get("value_current") is not None else None,
            "change": float(c["yoy_change"]) if c.get("yoy_change") is not None else None,
            "pct_change": float(c["yoy_pct_change"]) if c.get("yoy_pct_change") is not None else None,
            "fact_id_current": c.get("fact_id_current"),
            "fact_id_prior": c.get("fact_id_prior"),
        }
        for c in children
    ]
    bridge = {
        "concept": concept, "fiscal_year": fy, "fiscal_period": fiscal_period,
        "dimension": dimension,
        "total_prior": float(total["value_prior"]) if total and total.get("value_prior") is not None else None,
        "total_current": float(total["value_current"]) if total and total.get("value_current") is not None else None,
        "total_change": total_change,
        "explained_change": child_sum,
        "unexplained": (total_change - child_sum) if total_change is not None else None,
        "components": components,
        "year_length_note": _year_length_note(total),
    }
    return rc, bridge


def _year_length_note(total: dict | None) -> str | None:
    """The note when the bridged total's two periods differ in length, else None.

    A 53-week year against a 52-week one is compared on the reported basis and said so
    (docs/specs/period-disclosure.md R3, R7.4).
    """
    if not total:
        return None
    keys = ("period_start_current", "period_end_current", "period_start_prior", "period_end_prior")
    if any(total.get(k) is None for k in keys):
        return None
    disclosure = year_length_disclosure(
        {"period_start": total["period_start_current"], "period_end": total["period_end_current"]},
        {"period_start": total["period_start_prior"], "period_end": total["period_end_prior"]},
    )
    return disclosure["note"]


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def narrative_search(company: str, query: str, fiscal_years: list[int] | None,
                     section_hint: str | None, top_k: int) -> tuple[object, object]:
    """Resolve *company* and run the narrative retriever.

    ``section_hint`` is folded into the query text as a *soft* signal rather
    than a hard ``section_path`` prefix filter: filing section paths can be
    non-ASCII ToC labels, so an exact prefix can spuriously return nothing
    (see ``XbrlNarrativeRetriever`` docstring).
    """
    rc = resolve_company(company)
    effective_query = f"{section_hint}: {query}" if section_hint else query
    result = narrative().retrieve(
        effective_query,
        ciks=[rc.cik] if rc.cik else None,
        fiscal_years=fiscal_years,
        top_k=top_k,
    )
    return rc, result


# ---------------------------------------------------------------------------
# Discovery (coverage / concepts) — thin SQL over Layer-0 tables
# ---------------------------------------------------------------------------

def coverage(ticker: str = "") -> list[dict]:
    """Ingested companies with their canonical fiscal years + form types.

    Source of truth is ``filing_registry`` (canonical rows only), joined to
    ``entity_master`` for the human-readable name/ticker.
    """
    where = "fr.superseded_by IS NULL"
    params: tuple = ()
    if ticker:
        where += " AND upper(em.trading_symbol) = upper(%s)"
        params = (ticker,)
    return db.query(
        f"""
        SELECT fr.cik,
               em.trading_symbol AS ticker,
               em.company_name,
               array_agg(DISTINCT fr.fiscal_year ORDER BY fr.fiscal_year) AS fiscal_years,
               array_agg(DISTINCT fr.form_type) AS form_types
        FROM filing_registry fr
        LEFT JOIN entity_master em ON em.cik = fr.cik
        WHERE {where}
        GROUP BY fr.cik, em.trading_symbol, em.company_name
        ORDER BY em.trading_symbol NULLS LAST
        """,
        params,
    )


def concepts() -> list[dict]:
    """The ``concept_family`` vocabulary the fact endpoints accept, each with
    its preferred (rank-1) example QName."""
    return db.query(
        """
        SELECT DISTINCT ON (concept_family)
               concept_family, qname AS example_qname
        FROM concept_mapping
        WHERE NOT COALESCE(is_deprecated, FALSE)
        ORDER BY concept_family, preference_rank
        """
    )


# ---------------------------------------------------------------------------
# Metrics grid (tabular / CSV) — derived ratios + raw line-items, cited
# ---------------------------------------------------------------------------

def metrics_catalog() -> list[dict]:
    """The pre-computed derived metric names + their formulas (the ratio library
    in ``mv_derived_metrics``). Raw line-items are additionally addressable by
    any ``concept_family`` from :func:`concepts`."""
    return db.query(
        "SELECT DISTINCT metric_name, formula FROM mv_derived_metrics ORDER BY metric_name"
    )


def _derived_metric_names() -> set:
    return {r["metric_name"] for r in metrics_catalog()}


def metrics_grid(companies: list[str], metrics: list[str], fiscal_years: list[int],
                 fiscal_period: str = "FY") -> tuple[list, list[dict]]:
    """Build a cited metric x period grid (long form).

    Each requested *metric* is either a derived ratio (served from
    ``mv_derived_metrics``) or a raw ``concept_family`` line-item (served from
    ``mv_financial_facts``, current-period, primary duration). Returns
    ``(resolved_companies, cells)`` where each cell carries value + provenance.
    """
    fiscal_period = (fiscal_period or "FY").upper()
    resolved = [resolve_company(c) for c in companies]
    ciks = [rc.cik for rc in resolved if rc.cik]
    if not ciks or not metrics or not fiscal_years:
        return resolved, []

    derived_names = _derived_metric_names()
    derived_req = [m for m in metrics if m in derived_names]
    fact_req = [m for m in metrics if m not in derived_names]
    cells: list[dict] = []

    if derived_req:
        for r in db.query(
            """
            SELECT cik, ticker, fiscal_year, fiscal_period, metric_name,
                   value, formula, component_fact_ids
            FROM mv_derived_metrics
            WHERE cik = ANY(%s) AND metric_name = ANY(%s)
              AND fiscal_year = ANY(%s) AND fiscal_period = %s
            """,
            (ciks, derived_req, fiscal_years, fiscal_period),
        ):
            cells.append({
                "cik": r["cik"], "ticker": r.get("ticker"),
                "fiscal_year": r["fiscal_year"], "fiscal_period": r["fiscal_period"],
                "metric": r["metric_name"], "kind": "derived",
                "value": float(r["value"]) if r.get("value") is not None else None,
                "unit": None, "formula": r.get("formula"),
                "component_fact_ids": r.get("component_fact_ids"),
                "citation": None,
            })

    if fact_req:
        for r in db.query(
            """
            SELECT DISTINCT ON (cik, fiscal_year, concept_family)
                   cik, ticker, fiscal_year, fiscal_period,
                   concept_family AS metric, value, unit,
                   fact_id, html_anchor, page_num, accession_no, section_path
            FROM mv_financial_facts
            WHERE cik = ANY(%s) AND concept_family = ANY(%s)
              AND fiscal_year = ANY(%s) AND fiscal_period = %s
              AND is_canonical_filing = TRUE AND is_current_period = TRUE
              AND segment_axis IS NULL
              AND (period_type = 'instant'
                   OR duration_kind = CASE WHEN %s = 'FY' THEN 'annual' ELSE 'quarter' END)
            ORDER BY cik, fiscal_year, concept_family, concept_pref_rank NULLS LAST, fact_id
            """,
            (ciks, fact_req, fiscal_years, fiscal_period, fiscal_period),
        ):
            cells.append({
                "cik": r["cik"], "ticker": r.get("ticker"),
                "fiscal_year": r["fiscal_year"], "fiscal_period": r["fiscal_period"],
                "metric": r["metric"], "kind": "fact",
                "value": float(r["value"]) if r.get("value") is not None else None,
                "unit": r.get("unit"), "formula": None, "component_fact_ids": None,
                "citation": {
                    "fact_id": r.get("fact_id"), "html_anchor": r.get("html_anchor"),
                    "page_num": r.get("page_num"), "accession_no": r.get("accession_no"),
                    "section_path": r.get("section_path"),
                },
            })

    return resolved, cells
