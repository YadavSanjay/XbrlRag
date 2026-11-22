# Retrieval over html10k_text: hybrid search, rerank, neighbour expansion.
#
# Each scope is searched and reranked on its own, so in a comparison one filer cannot crowd out
# another. Every failure is loud: chunks that were not expanded are never returned as if they had
# been, unlike _expand_with_neighbors in the XBRL narrative retriever.
#
# Spec: docs/specs/html-rag-arm.md.

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, NamedTuple

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Condition,
    FieldCondition,
    Filter,
    MatchAny,
    Prefetch,
    Rrf,
    RrfQuery,
    SparseVector,
)

from src.chunking.html10k_chunking import html10k_chunk_id
from src.embeddings.base import BaseEmbeddingGenerator, DenseEmbedding, SparseEmbedding
from src.retriever.html10k.config import Html10kRagConfig
from src.retriever.html10k.rerank_cache import CachedReranker
from src.vectorstores.html10k_qdrant_vectordb_client import COLLECTION_NAME, embedding_input
from src.vectorstores.qdrant_vectordb_client import TEXT_DENSE, TEXT_SPARSE


class RetrievedChunk(NamedTuple):
    """One chunk given to the answerer: its payload, plus how it got here.

    Attributes:
        chunk_id: The chunk's id.
        accession_no: The filing's accession number.
        text: The chunk's text.
        cik: The filer.
        ticker: The filer's ticker.
        company_name: The filer's name.
        fiscal_year: The registry's fiscal year.
        form_type: The form.
        section_key: The innermost section.
        section_label: The innermost section's label.
        contains_table: Whether this is a table chunk.
        table_index: The table's ordinal, ``None`` for prose.
        part: The 0-based part of a split table.
        parts: How many parts the table has.
        html_char_start: Start of the chunk's byte range in the stripped document.
        html_char_end: End of the chunk's byte range.
        prev_chunk_id: The chunk before, or ``None``.
        next_chunk_id: The chunk after, or ``None``.
        rerank_score: The rerank score, ``None`` for an expansion chunk.
        is_expansion: Whether expansion added this chunk.
    """

    chunk_id: str
    accession_no: str
    text: str
    cik: str
    ticker: str | None
    company_name: str | None
    fiscal_year: int
    form_type: str
    section_key: str
    section_label: str
    contains_table: bool
    table_index: int | None
    part: int
    parts: int
    html_char_start: int
    html_char_end: int
    prev_chunk_id: str | None
    next_chunk_id: str | None
    rerank_score: float | None
    is_expansion: bool


class Html10kRetrieval(NamedTuple):
    """One retrieval's chunks and accounting.

    Attributes:
        chunks: The chunks: each scope's ranked ones in scope order, then the expansion.
        rerank_calls: Reranks that ran.
        rerank_cache_hits: Reranks the cache answered.
        rerank_ms: The sum of the original reranks' times, cached ones included.
    """

    chunks: list[RetrievedChunk]
    rerank_calls: int
    rerank_cache_hits: int
    rerank_ms: float


def _to_chunk(
    payload: Mapping[str, Any], rerank_score: float | None, is_expansion: bool
) -> RetrievedChunk:
    """Read a point's payload. A missing field raises: the payload contract is broken."""
    return RetrievedChunk(
        chunk_id=payload["chunk_id"],
        accession_no=payload["accession_no"],
        text=payload["text"],
        cik=payload["cik"],
        ticker=payload["ticker"],
        company_name=payload["company_name"],
        fiscal_year=payload["fiscal_year"],
        form_type=payload["form_type"],
        section_key=payload["section_key"],
        section_label=payload["section_label"],
        contains_table=payload["contains_table"],
        table_index=payload["table_index"],
        part=payload["part"],
        parts=payload["parts"],
        html_char_start=payload["html_char_start"],
        html_char_end=payload["html_char_end"],
        prev_chunk_id=payload["prev_chunk_id"],
        next_chunk_id=payload["next_chunk_id"],
        rerank_score=rerank_score,
        is_expansion=is_expansion,
    )


def _scope_filter(scope: Mapping[str, Sequence[object]]) -> Filter | None:
    """The filter of one scope; an empty list means no condition on that field."""
    conditions: list[Condition] = []
    ciks = [str(cik) for cik in scope["ciks"]]
    years = [int(str(year)) for year in scope["fiscal_years"]]
    if ciks:
        conditions.append(FieldCondition(key="cik", match=MatchAny(any=ciks)))
    if years:
        conditions.append(FieldCondition(key="fiscal_year", match=MatchAny(any=years)))
    return Filter(must=conditions) if conditions else None


def _related_ids(chunk: RetrievedChunk) -> Iterator[str | None]:
    """The chunks that belong with *chunk*: its neighbours, then a split table's parts."""
    yield chunk.prev_chunk_id
    yield chunk.next_chunk_id
    if chunk.contains_table and chunk.parts > 1:
        # A table's parts share a byte range, so the id formula gives each of them.
        for part in range(chunk.parts):
            yield html10k_chunk_id(
                chunk.accession_no, chunk.html_char_start, chunk.html_char_end, part
            )


class Html10kRetriever:
    """Hybrid search, rerank and neighbour expansion over ``html10k_text``."""

    def __init__(
        self,
        *,
        embedder: BaseEmbeddingGenerator,
        client: QdrantClient,
        reranker: CachedReranker,
        config: Html10kRagConfig,
    ) -> None:
        """Keep the dependencies; nothing is contacted.

        Args:
            embedder: Embeds the question, dense and sparse.
            client: The Qdrant client.
            reranker: The cached reranker.
            config: The settings.
        """
        self._embedder = embedder
        self._client = client
        self._reranker = reranker
        self._config = config

    def _search(
        self,
        dense: DenseEmbedding,
        sparse: SparseEmbedding,
        scope: Mapping[str, Sequence[object]],
    ) -> list[Mapping[str, Any]]:
        """Run one scope's hybrid search; return the candidates' payloads, in fused order.

        Qdrant orders equal fused scores arbitrarily, so every fused point is fetched and sorted
        here, ties by ``chunk_id``: the same search then sends the reranker the same list.
        """
        config = self._config
        scope_filter = _scope_filter(scope)
        response = self._client.query_points(
            collection_name=COLLECTION_NAME,
            prefetch=[
                Prefetch(
                    query=dense.values,
                    using=TEXT_DENSE,
                    limit=config.prefetch_limit,
                    filter=scope_filter,
                ),
                Prefetch(
                    query=SparseVector(indices=sparse.indices, values=sparse.values),
                    using=TEXT_SPARSE,
                    limit=config.prefetch_limit,
                    filter=scope_filter,
                ),
            ],
            query=RrfQuery(
                rrf=Rrf(k=config.rrf_k, weights=[config.dense_weight, config.sparse_weight])
            ),
            # Fusion joins two lists of at most prefetch_limit points each.
            limit=2 * config.prefetch_limit,
            with_payload=True,
        )
        scored = [(point.score, point.payload or {}) for point in response.points]
        scored.sort(key=lambda p: (-p[0], str(p[1].get("chunk_id", ""))))
        return [payload for _, payload in scored[: config.rerank_candidates]]

    def _expansion_ids(self, kept: Sequence[RetrievedChunk]) -> list[str]:
        """The ids to fetch, in order, up to the cap."""
        config = self._config
        if not config.expand_neighbors:
            return []
        kept_ids = {chunk.chunk_id for chunk in kept}
        chosen: dict[str, None] = {}
        for chunk in kept:
            if chunk.rerank_score is None or chunk.rerank_score < config.expansion_score_threshold:
                continue
            for chunk_id in _related_ids(chunk):
                if chunk_id is None or chunk_id in kept_ids or chunk_id in chosen:
                    continue
                if len(chosen) >= config.expansion_max_chunks:
                    return list(chosen)
                chosen[chunk_id] = None
        return list(chosen)

    def _expand(self, kept: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
        """Fetch the chunks expansion asks for, in the order asked; a missing id is left out."""
        ids = self._expansion_ids(kept)
        if not ids:
            return []
        records = self._client.retrieve(
            collection_name=COLLECTION_NAME, ids=ids, with_payload=True
        )
        found = {str(record.id): record.payload or {} for record in records}
        return [_to_chunk(found[i], None, True) for i in ids if i in found]

    def retrieve(
        self, query: str, scopes: Sequence[Mapping[str, Sequence[object]]]
    ) -> Html10kRetrieval:
        """Retrieve the chunks for a question.

        Args:
            query: The question.
            scopes: The searches to run, from ``search_scopes``.

        Returns:
            The chunks, ranked ones first, and the rerank accounting.
        """
        dense, sparse = self._embedder.embed_texts([("query", query)])["query"]

        kept: list[RetrievedChunk] = []
        rerank_calls = rerank_cache_hits = 0
        rerank_ms = 0.0
        for scope in scopes:
            candidates = self._search(dense, sparse, scope)
            if not candidates:
                continue
            result = self._reranker.rerank(
                query, [embedding_input(payload) for payload in candidates]
            )
            rerank_cache_hits += int(result.cache_hit)
            rerank_calls += int(not result.cache_hit)
            rerank_ms += result.elapsed_ms
            # sorted() is stable, so ties keep candidate order.
            ranked = sorted(zip(candidates, result.scores, strict=True), key=lambda c: -c[1])
            kept.extend(
                _to_chunk(payload, score, False)
                for payload, score in ranked[: self._config.context_top_k]
            )

        return Html10kRetrieval(
            chunks=[*kept, *self._expand(kept)],
            rerank_calls=rerank_calls,
            rerank_cache_hits=rerank_cache_hits,
            rerank_ms=rerank_ms,
        )
