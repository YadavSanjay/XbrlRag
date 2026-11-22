"""Client-side embedder that delegates BGE-M3 dense+sparse encoding to the shared
RagInference service instead of loading ``BAAI/bge-m3`` (~2.5GB) in-process.

Set ``RAGINFER_URL`` (e.g. ``http://127.0.0.1:8799``) and XbrlRag's serving
processes (MCP / API) stop loading their own embedder copy — the model lives
once in the RagInference service. Implements the same ``embed_texts`` /
``dense_embed_texts`` / ``sparse_embed_texts`` contract as
:class:`~src.embeddings.bge_embedding.BGEEmbeddingGenerator`, rebuilding the
:class:`DenseEmbedding` / :class:`SparseEmbedding` dataclasses from the JSON
response, so it is a drop-in for the XBRL narrative retriever's query embedding.

Network failures raise by default; set ``RAGINFER_FALLBACK_LOCAL=1`` to fall
back to an in-process :class:`XBRLBGEEmbeddingGenerator`.
"""

from __future__ import annotations

import os
from typing import List, Optional

import httpx

from src.embeddings.base import (
    BaseEmbeddingGenerator,
    DenseEmbedding,
    EmbeddingMap,
    SparseEmbedding,
)
from src.logging_config import get_logger
from src.retriever.endpoints import resolve_endpoint

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_S = float(os.getenv("RAGINFER_TIMEOUT_S", "300"))


class RemoteEmbedder(BaseEmbeddingGenerator):
    """Delegates embedding to the RagInference ``/embed`` endpoint."""

    model_name = "BAAI/bge-m3 (remote)"

    def __init__(
        self,
        url: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        model: str | None = None,
    ):
        self.url = resolve_endpoint("/embed", url)
        if not self.url:
            raise ValueError(
                "RemoteEmbedder needs RAGINFER_URL (or an explicit url=...)"
            )
        self.timeout_s = timeout_s
        self.model = model or os.getenv("RAGINFER_EMBED_MODEL") or None
        self._fallback = os.getenv("RAGINFER_FALLBACK_LOCAL", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self._local: BaseEmbeddingGenerator | None = None
        logger.info(
            "RemoteEmbedder -> %s (timeout=%.0fs, model=%s, fallback_local=%s)",
            self.url,
            self.timeout_s,
            self.model,
            self._fallback,
        )

    # ------------------------------------------------------------------ #
    # Wire call
    # ------------------------------------------------------------------ #
    def _embed(self, id_texts: list[tuple], dense: bool, sparse: bool) -> list:
        texts = [t for _, t in id_texts]
        payload = {"texts": texts, "dense": dense, "sparse": sparse}
        if self.model:
            payload["model"] = self.model
        resp = httpx.post(self.url, json=payload, timeout=self.timeout_s)
        resp.raise_for_status()
        items = resp.json()["embeddings"]
        if len(items) != len(texts):
            raise ValueError(
                f"service returned {len(items)} embeddings for {len(texts)} texts"
            )
        return items

    @staticmethod
    def _to_dense(item) -> DenseEmbedding:
        return DenseEmbedding(values=list(item["dense"]))

    @staticmethod
    def _to_sparse(item) -> SparseEmbedding:
        s = item["sparse"]
        return SparseEmbedding(
            indices=[int(i) for i in s["indices"]],
            values=[float(v) for v in s["values"]],
        )

    # ------------------------------------------------------------------ #
    # BaseEmbeddingGenerator contract
    # ------------------------------------------------------------------ #
    def embed_texts(self, id_texts: list[tuple[str, str]]) -> EmbeddingMap:
        if not id_texts:
            return {}
        try:
            items = self._embed(id_texts, dense=True, sparse=True)
            return {
                id_texts[i][0]: (self._to_dense(items[i]), self._to_sparse(items[i]))
                for i in range(len(id_texts))
            }
        except Exception:
            if self._fallback:
                logger.exception(
                    "RemoteEmbedder: service call failed, falling back to local embedder"
                )
                return self._local_embedder().embed_texts(id_texts)
            logger.error("RemoteEmbedder: service call to %s failed", self.url)
            raise

    def dense_embed_texts(
        self, id_texts: list[tuple[str, str]]
    ) -> dict[str, DenseEmbedding]:
        if not id_texts:
            return {}
        try:
            items = self._embed(id_texts, dense=True, sparse=False)
            return {
                id_texts[i][0]: self._to_dense(items[i]) for i in range(len(id_texts))
            }
        except Exception:
            if self._fallback:
                logger.exception(
                    "RemoteEmbedder: dense service call failed, falling back to local"
                )
                return self._local_embedder().dense_embed_texts(id_texts)
            raise

    def sparse_embed_texts(
        self, id_texts: list[tuple[str, str]]
    ) -> dict[str, SparseEmbedding]:
        if not id_texts:
            return {}
        try:
            items = self._embed(id_texts, dense=False, sparse=True)
            return {
                id_texts[i][0]: self._to_sparse(items[i]) for i in range(len(id_texts))
            }
        except Exception:
            if self._fallback:
                logger.exception(
                    "RemoteEmbedder: sparse service call failed, falling back to local"
                )
                return self._local_embedder().sparse_embed_texts(id_texts)
            raise

    def _local_embedder(self) -> BaseEmbeddingGenerator:
        if self._local is None:
            from src.embeddings.xbrl_bge_embedding import XBRLBGEEmbeddingGenerator

            self._local = XBRLBGEEmbeddingGenerator()
        return self._local
