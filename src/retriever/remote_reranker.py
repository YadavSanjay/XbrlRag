"""Client-side reranker that delegates scoring to the shared RagInference
service instead of loading ``jina-reranker-v3`` in-process.

Set ``RAGINFER_URL`` (e.g. ``http://127.0.0.1:8799``) and XbrlRag stops loading its
own ~0.9GB reranker copy — the model lives once in the RagInference service and
is shared with MacroRAG. Implements the same ``rerank(query, list[str]) ->
list[float]`` contract as :class:`~src.retriever.jina_reranker.JinaReranker`, so
it is a drop-in for both the XBRL narrative path and the PDF ``BaseReranker``
flow.

Network failures raise by default (the service is the intended single owner of
the model); set ``RAGINFER_FALLBACK_LOCAL=1`` to fall back to an in-process
:class:`JinaReranker` if the service is unreachable.
"""

from __future__ import annotations

import os
from typing import List, Optional

import httpx

from src.logging_config import get_logger
from src.retriever.base_reranker import BaseReranker

# Re-exported for back-compat. The definition lives in the torch-free
# src.retriever.endpoints so RemoteEmbedder can import it without dragging in
# BaseReranker -> retriever_util -> torch; do NOT move it back here.
from src.retriever.endpoints import resolve_endpoint  # noqa: F401

logger = get_logger(__name__)

# Rerank of ~30 long docs on CPU can take ~90s; keep the client patient.
_DEFAULT_TIMEOUT_S = float(os.getenv("RAGINFER_TIMEOUT_S", "300"))

# RagInference's /rerank takes only these short names. Anything else is sent as
# given, so the service refuses a typo with a 422 instead of the client guessing.
_SERVICE_MODEL_NAMES = {
    "jinaai/jina-reranker-v3": "jina",
    "BAAI/bge-reranker-v2-m3": "bge",
}


class RemoteReranker(BaseReranker):
    """Delegates ``rerank`` to the RagInference ``/rerank`` endpoint."""

    def __init__(
        self,
        url: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        model: str | None = None,
    ):
        self.url = resolve_endpoint("/rerank", url)
        if not self.url:
            raise ValueError(
                "RemoteReranker needs RAGINFER_URL (or an explicit url=...)"
            )
        self.timeout_s = timeout_s
        self.model = model or os.getenv("RAGINFER_RERANK_MODEL") or None
        self._fallback = os.getenv("RAGINFER_FALLBACK_LOCAL", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self._local: BaseReranker | None = None
        logger.info(
            "RemoteReranker -> %s (timeout=%.0fs, model=%s, fallback_local=%s)",
            self.url,
            self.timeout_s,
            self.model,
            self._fallback,
        )

    def rerank(self, query: str, chunks_with_text: list[str]) -> list[float]:
        if not chunks_with_text:
            return []
        payload = {"query": query, "documents": list(chunks_with_text)}
        if self.model:
            payload["model"] = _SERVICE_MODEL_NAMES.get(self.model, self.model)
        try:
            resp = httpx.post(self.url, json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            scores = resp.json()["scores"]
            if len(scores) != len(chunks_with_text):
                raise ValueError(
                    f"service returned {len(scores)} scores for {len(chunks_with_text)} docs"
                )
            return [float(s) for s in scores]
        except Exception:
            if self._fallback:
                logger.exception(
                    "RemoteReranker: service call failed, falling back to local JinaReranker"
                )
                return self._local_reranker().rerank(query, chunks_with_text)
            logger.error("RemoteReranker: service call to %s failed", self.url)
            raise

    def _local_reranker(self) -> BaseReranker:
        if self._local is None:
            from src.retriever.jina_reranker import JinaReranker

            self._local = JinaReranker()
        return self._local
