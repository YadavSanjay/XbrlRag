"""XbrlRag v1 Analyst API — granular, mostly-LLM-free services for programmatic
(LLM-agent) callers such as the Faultline ``scenariogen`` module.

Unlike the UI BFF (``src/api/routes/``), which exposes a single monolithic
natural-language ``/api/query`` plus observability dashboards, this package
exposes the *individually useful* building blocks of the XBRL pipeline:

- ``GET  /api/v1/coverage``            — what companies / fiscal years / forms are ingested
- ``GET  /api/v1/concepts``            — the ``concept_family`` vocabulary fact lookups accept
- ``GET  /api/v1/companies/{id}``      — resolve a ticker/CIK + metadata
- ``POST /api/v1/facts/lookup``        — deterministic fact time series (0 LLM calls)
- ``POST /api/v1/facts/compute``       — citation-traced ratios/growth over facts (0 LLM calls)
- ``POST /api/v1/facts/bridge``        — YoY variance/bridge decomposition to segment/geo children (0 LLM calls)
- ``GET  /api/v1/metrics/catalog``     — the pre-computed derived-ratio library (0 LLM calls)
- ``POST /api/v1/metrics``             — cited metric × period grid for N companies (0 LLM calls)
- ``POST /api/v1/metrics.csv``         — the same grid as a CSV download (0 LLM calls)
- ``POST /api/v1/screen``              — cross-filer ranking by a metric, each row cited (0 LLM calls)
- ``POST /api/v1/narrative/search``    — risk-factor / MD&A chunk retrieval (0 LLM calls)
- ``POST /api/v1/answer``              — full synthesized NL answer (reuses the XBRL pipeline)

Every endpoint wraps an existing component (``EntityExtractor``,
``XbrlFactstoreLookup``, ``XbrlFactstoreScreen``, ``XbrlNarrativeRetriever``,
``XbrlCalculatorTool``, ``XbrlRetrievalPipeline``) so resolution/citation logic has
one source of truth. ``router.py`` is the authoritative endpoint surface.
"""
from __future__ import annotations

# Export the APIRouter under a distinct name so it does NOT shadow the `router`
# submodule: callers (and tests) rely on `from src.api.v1 import router` /
# `import src.api.v1.router` resolving to the *module* (whose functions they
# call/monkeypatch), while app wiring uses the APIRouter object below.
from src.api.v1.router import router as analyst_router

__all__ = ["analyst_router"]
