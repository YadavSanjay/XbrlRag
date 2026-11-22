"""XBRL narrative RAG branch — `step.op == "narrative"` (design doc §2.2).

Implements the "existing RAG path: hybrid Qdrant + Jina rerank" referenced by
``XbrlRag_Architecture.md``'s ``plan_executor`` pseudocode:

    elif step.op == "narrative":
        existing RAG branch (hybrid Qdrant + Jina rerank)

This is the XBRL-canonical analogue of
:class:`~src.retriever.base_retriever.BaseRetriever`, rebuilt on top of the
Phase-10 ingestion components:

* :class:`~src.embeddings.xbrl_bge_embedding.XBRLBGEEmbeddingGenerator` —
  same ``BAAI/bge-m3`` dense+sparse encoder.
* :class:`~src.vectorstores.xbrl_qdrant_vectordb_client.XBRLQdrantVectorDBClient`
  — hybrid (RRF) search over ``xbrl_sec10k_text``, filtered by
  ``cik``/``fiscal_year``/``section_path_ancestors``.
* :class:`~src.retriever.jina_reranker.JinaReranker` — unchanged, reused
  as-is (``rerank(query, texts) -> List[float]``).

Differences from the PDF flow's :class:`BaseRetriever`:

* No per-chunk file-on-disk hydration — :class:`XBRLChunkPayload` already
  carries the full chunk ``text`` (including the table-cell rendering for
  ``contains_table=True`` chunks) directly in the Qdrant payload (see
  ``src/vectorstores/xbrl_qdrant_vectordb_client.py`` module docstring), so
  reranking operates on payload text directly.
* Filters by ``cik``/``fiscal_year`` (resolved deterministically by
  ``EntityExtractor`` — see ``intent_router.py``) instead of fuzzy
  ``company``/``year`` string matches.
* :meth:`XbrlNarrativeRetriever.execute_step` is the ``NarrativeFn`` the
  :class:`~src.retriever.xbrl.plan_executor.PlanExecutor` stub expects for
  ``step.op == "narrative"`` — wraps :meth:`retrieve` and returns a
  ``StepResult`` whose ``detail`` carries the full
  :class:`NarrativeRetrievalResult` (chunks + payload) for the synthesizer
  (``src/retriever/xbrl/llm_xbrl_synthesizer.py``).
* Implements the PDF review's "chunk expansion" recommendation (§"Chunk
  expansion (`_apply_chunk_expansion`) is a stub"): for any chunk above
  ``expansion_score_threshold``, also pulls its ``prev_chunk_id`` /
  ``next_chunk_id`` neighbors — 10-K answers commonly span chunk boundaries
  (table -> footnote -> continuation paragraph).
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from src import XBRL_TEXT_COLLECTION_NAME
from src.embeddings.xbrl_bge_embedding import XBRLBGEEmbeddingGenerator
from src.logging_config import get_logger
from src.vectorstores.xbrl_qdrant_vectordb_client import XBRLQdrantVectorDBClient

if TYPE_CHECKING:
    # `jina_reranker` imports torch/transformers, which the slim serving image does
    # not install — it reranks over HTTP via RAGINFER_URL. Keep this import
    # type-only (module has `from __future__ import annotations`); the local
    # reranker is imported inside `_build_reranker` on the no-RAGINFER_URL path.
    from src.retriever.base_reranker import BaseReranker
    from src.retriever.xbrl.intent_router import IntentRouterResult
    from src.retriever.xbrl.plan_executor import StepResult

logger = get_logger(__name__)

# Candidates fetched from Qdrant and handed to the reranker. This is the single
# biggest lever on narrative latency: jina-reranker-v3 is listwise, so it packs all
# candidates into 4096-token blocks and pays an O(tokens^2) attention cost on top of
# the linear one. At 30 candidates x 512 tokens (~15k tokens, ~4 blocks) a rerank ran
# over 10 MINUTES on 4-vCPU CPU Fargate, blowing every client timeout. 10 candidates
# is ~3x less work, and reranking 10 to return DEFAULT_TOP_K=10 loses little.
# Raise again only alongside more rerank throughput (vCPU, GPU, or quantization).
DEFAULT_PREFETCH_LIMIT = 10
DEFAULT_TOP_K = 10
DEFAULT_EXPANSION_SCORE_THRESHOLD = 0.5


@dataclass
class NarrativeChunk:
    """One retrieved (and reranked) chunk from ``xbrl_sec10k_text``.

    Mirrors :class:`~src.embeddings.xbrl_bge_embedding.XBRLChunkPayload`'s
    fields — populated directly from the Qdrant point's payload, which (per
    ``XBRLQdrantVectorDBClient`` module docstring) already carries the full
    chunk ``text``.
    """

    chunk_id: str
    text: str
    retrieval_score: float = 0.0
    rerank_score: float | None = None
    cik: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    accession_no: str | None = None
    fiscal_year: int | None = None
    form_type: str | None = None
    section_path: str | None = None
    html_char_start: int | None = None
    html_char_end: int | None = None
    page_num: int | None = None
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None
    contains_table: bool = False
    has_xbrl_facts: bool = False
    xbrl_fact_ids: list[int] = field(default_factory=list)
    xbrl_concepts: list[str] = field(default_factory=list)
    is_expansion: bool = False
    """True if this chunk was pulled in as a ``prev``/``next`` neighbor of a
    top-ranked chunk, rather than a direct retrieval/rerank hit."""

    @classmethod
    def from_point(cls, point, rerank_score: float | None = None, is_expansion: bool = False) -> NarrativeChunk:
        payload = point.payload or {}
        return cls(
            chunk_id=str(point.id),
            text=payload.get("text", "") or "",
            retrieval_score=float(getattr(point, "score", 0.0) or 0.0),
            rerank_score=rerank_score,
            cik=payload.get("cik"),
            ticker=payload.get("ticker"),
            company_name=payload.get("company_name"),
            accession_no=payload.get("accession_no"),
            fiscal_year=payload.get("fiscal_year"),
            form_type=payload.get("form_type"),
            section_path=payload.get("section_path"),
            html_char_start=payload.get("html_char_start"),
            html_char_end=payload.get("html_char_end"),
            page_num=payload.get("page_num"),
            prev_chunk_id=payload.get("prev_chunk_id"),
            next_chunk_id=payload.get("next_chunk_id"),
            contains_table=bool(payload.get("contains_table", False)),
            has_xbrl_facts=bool(payload.get("has_xbrl_facts", False)),
            xbrl_fact_ids=list(payload.get("xbrl_fact_ids") or []),
            xbrl_concepts=list(payload.get("xbrl_concepts") or []),
            is_expansion=is_expansion,
        )

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "retrieval_score": round(self.retrieval_score, 4),
            "rerank_score": round(self.rerank_score, 4) if self.rerank_score is not None else None,
            "cik": self.cik,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "accession_no": self.accession_no,
            "fiscal_year": self.fiscal_year,
            "form_type": self.form_type,
            "section_path": self.section_path,
            "html_char_start": self.html_char_start,
            "html_char_end": self.html_char_end,
            "page_num": self.page_num,
            "contains_table": self.contains_table,
            "has_xbrl_facts": self.has_xbrl_facts,
            "xbrl_fact_ids": self.xbrl_fact_ids,
            "xbrl_concepts": self.xbrl_concepts,
            "is_expansion": self.is_expansion,
        }


@dataclass
class NarrativeRetrievalResult:
    """Result of one :meth:`XbrlNarrativeRetriever.retrieve` call."""

    query: str
    chunks: list[NarrativeChunk] = field(default_factory=list)
    ciks: list[str] = field(default_factory=list)
    fiscal_years: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "ciks": self.ciks,
            "fiscal_years": self.fiscal_years,
            "chunks": [c.to_dict() for c in self.chunks],
        }

    def to_step_result(self, step_id: str | None) -> StepResult:
        """Adapt to a :class:`~src.retriever.xbrl.plan_executor.StepResult`
        for ``PlanExecutor``'s ``narrative_fn`` contract. ``detail`` carries
        ``self`` (the full chunk list + payload) for the synthesizer."""
        from src.retriever.xbrl.plan_executor import StepResult, StepStatus

        if not self.chunks:
            return StepResult(
                step_id=step_id, op="narrative", status=StepStatus.NOT_FOUND,
                error="no narrative chunks retrieved", detail=self,
            )
        best = self.chunks[0]
        return StepResult(
            step_id=step_id, op="narrative", status=StepStatus.OK,
            confidence=best.rerank_score if best.rerank_score is not None else best.retrieval_score,
            linked_chunk_ids=[c.chunk_id for c in self.chunks],
            detail=self,
        )


class XbrlNarrativeRetriever:
    """Hybrid Qdrant (dense+sparse RRF) + Jina rerank retrieval over
    ``xbrl_sec10k_text``.

    Example:
        >>> retriever = XbrlNarrativeRetriever()
        >>> result = retriever.retrieve(
        ...     "what drove FY23 R&D growth at Alphabet?",
        ...     ciks=["0001652044"], fiscal_years=[2023],
        ... )
        >>> [c.chunk_id for c in result.chunks]
    """

    def __init__(
        self,
        embedding_generator: XBRLBGEEmbeddingGenerator | None = None,
        vector_db_client: XBRLQdrantVectorDBClient | None = None,
        reranker: BaseReranker | None = None,
        text_collection: str = XBRL_TEXT_COLLECTION_NAME,
        prefetch_limit: int = DEFAULT_PREFETCH_LIMIT,
        expand_neighbors: bool = True,
        expansion_score_threshold: float = DEFAULT_EXPANSION_SCORE_THRESHOLD,
    ):
        self._embedding_generator = embedding_generator
        self._vector_db_client = vector_db_client
        self._reranker = reranker
        self.text_collection = text_collection
        self.prefetch_limit = prefetch_limit
        self.expand_neighbors = expand_neighbors
        self.expansion_score_threshold = expansion_score_threshold
        # Guards the lazy model loads below: the MCP server warms these in a
        # background thread while real tool calls may arrive concurrently, and
        # the heavy models (BGE-M3 ~6.9GB, jina-reranker-v3 ~2.4GB) must not be
        # double-constructed. Double-checked locking keeps the warm path lock-free.
        self._model_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazily-constructed, model-loading dependencies
    # ------------------------------------------------------------------

    @property
    def embedding_generator(self) -> XBRLBGEEmbeddingGenerator:
        if self._embedding_generator is None:
            with self._model_lock:
                if self._embedding_generator is None:
                    self._embedding_generator = self._build_embedding_generator()
        return self._embedding_generator

    @staticmethod
    def _build_embedding_generator():
        """Use the shared RagInference service when ``RAGINFER_URL`` is set (so
        BGE-M3 isn't loaded in this process); otherwise load it locally."""
        if os.getenv("RAGINFER_URL", "").strip():
            from src.embeddings.remote_embedder import RemoteEmbedder

            return RemoteEmbedder()
        return XBRLBGEEmbeddingGenerator()

    @property
    def vector_db_client(self) -> XBRLQdrantVectorDBClient:
        if self._vector_db_client is None:
            with self._model_lock:
                if self._vector_db_client is None:
                    self._vector_db_client = XBRLQdrantVectorDBClient()
        return self._vector_db_client

    @property
    def reranker(self) -> BaseReranker:
        if self._reranker is None:
            with self._model_lock:
                if self._reranker is None:
                    self._reranker = self._build_reranker()
        return self._reranker

    @staticmethod
    def _build_reranker():
        """Use the shared RagInference service when ``RAGINFER_URL`` is set (so
        the reranker isn't loaded in this process); otherwise load Jina locally.
        """
        if os.getenv("RAGINFER_URL", "").strip():
            from src.retriever.remote_reranker import RemoteReranker

            return RemoteReranker()
        from src.retriever.jina_reranker import JinaReranker

        return JinaReranker()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        ciks: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        section_path_prefixes: list[str] | None = None,
        contains_table: bool | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> NarrativeRetrievalResult:
        """Embed *query*, run hybrid RRF search (optionally filtered), rerank
        with Jina, and (if enabled) expand top hits with their
        ``prev``/``next`` neighbors.
        """
        dense_emb, sparse_emb = self.embedding_generator.embed_texts([("query", query)])["query"]

        search_results = self.vector_db_client.hybrid_query_text(
            collection_name=self.text_collection,
            vector_d=dense_emb,
            vector_s=sparse_emb,
            ciks_in_query=ciks,
            fiscal_years_in_query=fiscal_years,
            section_path_prefixes=section_path_prefixes,
            contains_table=contains_table,
            limit=self.prefetch_limit,
        )
        points = list(search_results.points)
        if not points:
            logger.info(
                "xbrl narrative retrieve: 0 candidates for query=%r (ciks=%s, fiscal_years=%s, "
                "section_path_prefixes=%s)",
                query, ciks, fiscal_years, section_path_prefixes,
            )
            return NarrativeRetrievalResult(query=query, ciks=ciks or [], fiscal_years=fiscal_years or [])

        texts = [(p.payload or {}).get("text", "") for p in points]
        rerank_scores = self.reranker.rerank(query, texts)

        ranked = sorted(zip(points, rerank_scores), key=lambda ps: ps[1], reverse=True)[:top_k]
        chunks = [NarrativeChunk.from_point(p, rerank_score=float(s)) for p, s in ranked]

        if self.expand_neighbors:
            chunks = self._expand_with_neighbors(chunks)

        logger.info(
            "xbrl narrative retrieve: query=%r -> %d candidate(s), %d chunk(s) after rerank/expand "
            "(ciks=%s, fiscal_years=%s)",
            query, len(points), len(chunks), ciks, fiscal_years,
        )
        return NarrativeRetrievalResult(query=query, chunks=chunks, ciks=ciks or [], fiscal_years=fiscal_years or [])

    def execute_step(
        self,
        step: dict,
        query: str,
        intent_result: IntentRouterResult | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> StepResult:
        """``NarrativeFn`` for :class:`~src.retriever.xbrl.plan_executor.PlanExecutor`.

        ``step`` is a ``{"op": "narrative", "id": ..., "section_hint": ...}``
        plan step (``xbrl_query_plan_generator.txt`` / intent_router fast
        path). ``intent_result`` (when supplied) provides the resolved
        ``cik``/``fiscal_year`` filters from ``EntityExtractor`` —
        deterministic, never re-derived here.

        ``section_hint`` is a fuzzy phrase (e.g. "Management's Discussion and
        Analysis", "Disaggregated Revenue") that may not match any filing's
        exact ``section_path`` string (which can also be a non-ASCII ToC
        label — see the NVDA Shareholders' Equity ``section_path`` mojibake
        noted in earlier sessions). Rather than risk an over-strict/incorrect
        hard filter, it is folded into the embedding/rerank query text as a
        soft relevance signal.
        """
        ciks: list[str] = []
        fiscal_years: list[int] = []
        if intent_result is not None:
            ciks = sorted({c.cik for c in intent_result.companies if c.cik})
            fiscal_years = sorted({p.fiscal_year for p in intent_result.periods if p.fiscal_year is not None})

        section_hint = step.get("section_hint")
        retrieval_query = f"{query} — focus on: {section_hint}" if section_hint else query

        result = self.retrieve(
            retrieval_query,
            ciks=ciks or None,
            fiscal_years=fiscal_years or None,
            top_k=top_k,
        )
        return result.to_step_result(step.get("id"))

    # ------------------------------------------------------------------
    # Chunk expansion (PDF review: "_apply_chunk_expansion is a stub")
    # ------------------------------------------------------------------

    def _expand_with_neighbors(self, chunks: list[NarrativeChunk]) -> list[NarrativeChunk]:
        """For every chunk scoring >= ``expansion_score_threshold``, fetch
        its ``prev_chunk_id``/``next_chunk_id`` neighbor(s) by point ID and
        append them (deduplicated, marked ``is_expansion=True``)."""
        existing_ids = {c.chunk_id for c in chunks}
        neighbor_ids: set[str] = set()
        for c in chunks:
            score = c.rerank_score if c.rerank_score is not None else c.retrieval_score
            if score < self.expansion_score_threshold:
                continue
            for nid in (c.prev_chunk_id, c.next_chunk_id):
                if nid and nid not in existing_ids:
                    neighbor_ids.add(nid)

        if not neighbor_ids:
            return chunks

        try:
            records = self.vector_db_client.client.retrieve(
                collection_name=self.text_collection,
                ids=list(neighbor_ids),
                with_payload=True,
            )
        except Exception:
            logger.exception("xbrl narrative retrieve: neighbor expansion fetch failed")
            return chunks

        expanded = list(chunks)
        for rec in records:
            expanded.append(NarrativeChunk.from_point(rec, rerank_score=None, is_expansion=True))
        logger.debug(
            "xbrl narrative retrieve: expanded %d -> %d chunk(s) with %d neighbor(s)",
            len(chunks), len(expanded), len(records),
        )
        return expanded


if __name__ == "__main__":
    retriever = XbrlNarrativeRetriever()

    for q, ciks, fys in [
        ("what drove the increase in NVIDIA's Data Center revenue?", ["0001045810"], [2026]),
        ("describe Alphabet's risk factors related to AI regulation", ["0001652044"], None),
    ]:
        result = retriever.retrieve(q, ciks=ciks, fiscal_years=fys, top_k=5)
        print(q)
        for c in result.chunks:
            print(f"  {c.chunk_id}  rerank={c.rerank_score!r:>8}  section={c.section_path!r}  expansion={c.is_expansion}")
        print()
