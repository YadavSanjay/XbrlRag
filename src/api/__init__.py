"""XbrlRag Backend-for-Frontend (BFF).

A thin FastAPI service that the React UI (``ui/``) talks to. Two concerns:

1. **Run queries** — wrap the existing pipelines
   (:class:`~src.pipelines.xbrl.retrieval_pipeline.XbrlRetrievalPipeline` and
   :class:`~src.pipelines.sec10k.retrieval_pipeline.RetrievalPipeline`).
2. **Read observability** — thin SELECTs over the ``audit`` schema + analyst
   views already defined in ``scripts/ddl/audit/``.

Run with::

    uvicorn src.api.main:app --reload --port 8000

The UI dev server (Vite) proxies ``/api`` here.
"""
