"""Qdrant vector store client for the XBRL/HTML-canonical ingestion path
(the embed + Qdrant upsert step; ``docs/design/XbrlRag_HTML_Canonical_Source.md``).

Collection lifecycle (:meth:`create_collection`,
:meth:`prepare_for_ingestion`, :meth:`update_post_ingestion`) is inherited
unchanged from
:class:`~src.vectorstores.qdrant_vectordb_client.QdrantVectorDBClient` — same
``BAAI/bge-m3`` 1024-dim dense + sparse config, same bulk-ingest
``hnsw m=0 -> m=16`` toggle.

What's new is the **payload shape**
(:class:`~src.embeddings.xbrl_bge_embedding.XBRLChunkPayload` — keyed on
``cik``/``fiscal_year``/``section_path``/``xbrl_fact_ids``/``xbrl_concepts``
instead of the PDF flow's ``company``/``year``/``section``/``subsection``/
``table_json``) and the **retrieval filters** that exploit it:

* ``cik`` + ``fiscal_year`` instead of ``company`` + ``year``.
* ``section_path_ancestors`` (a Qdrant array field — see
  :attr:`XBRLChunkPayload.section_path_ancestors`) for section-subtree
  filtering, e.g. "everything under Part II > Item 7. MD&A"
  (``XbrlRag_HTML_Canonical_Source.md`` §4).
* ``contains_table`` / ``has_xbrl_facts`` for content-type filtering.
* ``xbrl_fact_ids`` for the reverse HYBRID path — "find prose chunks
  cross-linked to fact X" (``XbrlRag_HTML_Canonical_Source.md`` §4).

Per ``XbrlRag_HTML_Canonical_Source.md`` §4 (the payload carries
``section_path``, ``html_char_start/end`` and the linked fact ids) —
unlike the PDF flow (which stores only a ``chunk_file_path`` and lazily loads
chunk text / ``table_json`` from disk at retrieval time, see
``base_retriever.py:205``), XBRL chunks have no per-chunk file on disk — the
full chunk ``text`` (including the table-cell rendering for
``contains_table=True`` chunks) is written directly into the payload.
"""
from __future__ import annotations

from typing import List, Optional, Union

from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    SparseVector,
)

from src.embeddings.base import DenseEmbedding, EmbeddingMap, SparseEmbedding
from src.embeddings.xbrl_bge_embedding import XBRLChunkPayload
from src.logging_config import get_logger
from src.vectorstores.qdrant_vectordb_client import (
    TEXT_DENSE,
    TEXT_SPARSE,
    QdrantVectorDBClient,
)

logger = get_logger(__name__)


# Payload fields used as retrieval filters -> Qdrant index type
# (see XBRLQdrantVectorDBClient.create_payload_indexes).
_XBRL_PAYLOAD_INDEXES: dict[str, PayloadSchemaType] = {
    "cik":                    PayloadSchemaType.KEYWORD,
    "ticker":                 PayloadSchemaType.KEYWORD,
    "accession_no":           PayloadSchemaType.KEYWORD,
    "fiscal_year":            PayloadSchemaType.INTEGER,
    "fiscal_period":          PayloadSchemaType.KEYWORD,
    "form_type":              PayloadSchemaType.KEYWORD,
    "section_path":           PayloadSchemaType.KEYWORD,
    "section_path_ancestors": PayloadSchemaType.KEYWORD,
    "contains_table":         PayloadSchemaType.BOOL,
    "has_xbrl_facts":         PayloadSchemaType.BOOL,
    "is_financial_statement": PayloadSchemaType.BOOL,
    "xbrl_fact_ids":          PayloadSchemaType.INTEGER,
    "xbrl_concepts":          PayloadSchemaType.KEYWORD,
}


class XBRLQdrantVectorDBClient(QdrantVectorDBClient):
    """XBRL-flavoured Qdrant client — see module docstring for what differs
    from :class:`~src.vectorstores.qdrant_vectordb_client.QdrantVectorDBClient`."""

    # ------------------------------------------------------------------
    # Collection setup
    # ------------------------------------------------------------------

    def create_payload_indexes(self, collection_name: str) -> None:
        """Create payload indexes for every field in
        :data:`_XBRL_PAYLOAD_INDEXES` — the metadata-filter fields on
        :class:`XBRLChunkPayload`. Idempotent: Qdrant no-ops if an identical
        index already exists. Call once per collection after
        :meth:`create_collection`, before bulk upload.
        """
        for field_name, schema in _XBRL_PAYLOAD_INDEXES.items():
            self.client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=schema,
            )
        logger.info(
            "created %d payload indexes on %s", len(_XBRL_PAYLOAD_INDEXES), collection_name
        )

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_collection(
        self,
        collection_name: str,
        vector_data: dict[str, DenseEmbedding] | EmbeddingMap,
        payload_data: dict[str, XBRLChunkPayload],
        wait: bool = True,
    ):
        """Upload XBRL chunk vectors + payloads.

        ``vector_data`` follows the same convention as
        :meth:`QdrantVectorDBClient.upload_collection`: a mapping to
        ``(DenseEmbedding, SparseEmbedding)`` tuples for the ``TEXT_*``
        collection (dense+sparse), or to bare :class:`DenseEmbedding` for the
        ``METADATA_*`` collection (dense only).

        ``payload_data`` maps ``chunk_id -> XBRLChunkPayload`` directly (no
        ``chunk_file_path`` indirection — see module docstring).
        """
        points = []
        if vector_data:
            first_value = next(iter(vector_data.values()))
            has_sparse = isinstance(first_value, tuple) and len(first_value) == 2
        else:
            has_sparse = False

        if has_sparse:
            for chunk_id, (dense_emb, sparse_emb) in vector_data.items():
                payload = payload_data.get(chunk_id)
                point = PointStruct(
                    id=chunk_id,
                    vector={
                        TEXT_DENSE: dense_emb.values,
                        TEXT_SPARSE: {
                            "indices": sparse_emb.indices,
                            "values": sparse_emb.values,
                        },
                    },
                    payload=self._to_payload_dict(payload) if payload else {},
                )
                points.append(point)
        else:
            for chunk_id, dense_emb in vector_data.items():
                payload = payload_data.get(chunk_id)
                point = PointStruct(
                    id=chunk_id,
                    vector={TEXT_DENSE: dense_emb.values},
                    payload=self._to_payload_dict(payload, metadata_only=True) if payload else {},
                )
                points.append(point)

        return self.client.upload_points(
            collection_name=collection_name,
            points=points,
            wait=wait,
            parallel=2,
            max_retries=2,
        )

    @staticmethod
    def _to_payload_dict(payload: XBRLChunkPayload, metadata_only: bool = False) -> dict:
        d = {
            "chunk_id":              payload.chunk_id,
            "cik":                   payload.cik,
            "ticker":                payload.ticker,
            "company_name":          payload.company_name,
            "accession_no":          payload.accession_no,
            "fiscal_year":           payload.fiscal_year,
            "fiscal_period":         payload.fiscal_period,
            "form_type":             payload.form_type,
            "period_end":            payload.period_end,
            "section_path":          payload.section_path,
            "section_path_ancestors": payload.section_path_ancestors,
            "html_char_start":       payload.html_char_start,
            "html_char_end":         payload.html_char_end,
            "page_num":              payload.page_num,
            "prev_chunk_id":         payload.prev_chunk_id,
            "next_chunk_id":         payload.next_chunk_id,
            "contains_table":        payload.contains_table,
            "source_tag":            payload.source_tag,
            "has_xbrl_facts":        payload.has_xbrl_facts,
            "is_financial_statement": payload.is_financial_statement,
            "xbrl_fact_ids":         payload.xbrl_fact_ids,
            "xbrl_concepts":         payload.xbrl_concepts,
            "xbrl_concept_labels":   payload.xbrl_concept_labels,
        }
        if not metadata_only:
            d["text"] = payload.text
        return d

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def hybrid_query_text(
        self,
        collection_name: str,
        vector_d: DenseEmbedding,
        vector_s: SparseEmbedding,
        ciks_in_query: list[str] | None = None,
        fiscal_years_in_query: list[int] | None = None,
        section_path_prefixes: list[str] | None = None,
        contains_table: bool | None = None,
        limit: int = 15,
        tickers_in_query: list[str] | None = None,
    ):
        """Hybrid (dense+sparse, RRF-fused) search over the ``TEXT_*``
        collection — the XBRL analogue of
        :meth:`QdrantVectorDBClient.hybrid_query_text`.

        Filters (all optional, combined with AND):

        * ``ciks_in_query`` — ``cik`` :class:`MatchAny` (replaces the PDF
          flow's ``company`` filter).
        * ``fiscal_years_in_query`` — ``fiscal_year`` :class:`MatchAny`
          (replaces the PDF flow's ``year`` filter).
        * ``section_path_prefixes`` — :class:`MatchAny` against
          ``section_path_ancestors``, i.e. "chunk is at or below any of
          these sections" (e.g. ``["Part II > Item 7. MD&A"]``) — enables
          the section-aware retrieval described in
          ``XbrlRag_HTML_Canonical_Source.md`` §4.
        * ``contains_table`` — restrict to table chunks (numeric lookups) or
          prose chunks (narrative).
        * ``tickers_in_query`` — ``ticker`` :class:`MatchAny`, for callers that
          know the filer by ticker rather than CIK (the eval baselines).
        """
        must_conditions = []
        if ciks_in_query:
            must_conditions.append(FieldCondition(key="cik", match=MatchAny(any=ciks_in_query)))
        if tickers_in_query:
            must_conditions.append(
                FieldCondition(key="ticker", match=MatchAny(any=tickers_in_query))
            )
        if fiscal_years_in_query:
            must_conditions.append(
                FieldCondition(key="fiscal_year", match=MatchAny(any=fiscal_years_in_query))
            )
        if section_path_prefixes:
            must_conditions.append(
                FieldCondition(key="section_path_ancestors", match=MatchAny(any=section_path_prefixes))
            )
        if contains_table is not None:
            must_conditions.append(
                FieldCondition(key="contains_table", match=MatchValue(value=contains_table))
            )

        query_filter = Filter(must=must_conditions) if must_conditions else None

        return self.client.query_points(
            collection_name=collection_name,
            prefetch=[
                Prefetch(
                    query=SparseVector(indices=vector_s.indices, values=vector_s.values),
                    using=TEXT_SPARSE,
                    limit=2 * limit,
                    filter=query_filter,
                ),
                Prefetch(
                    query=vector_d.values,
                    using=TEXT_DENSE,
                    limit=2 * limit,
                    filter=query_filter,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            with_payload=True,
        )

    def query_metadata(
        self,
        collection_name: str,
        query: list[float],
        cik: str | None = None,
        fiscal_years_in_query: list[int] | None = None,
        limit: int = 10,
        score_threshold: float = 0.9,
    ):
        """Dense-only search over the ``METADATA_*`` collection — the XBRL
        analogue of :meth:`QdrantVectorDBClient.query_metadata`, filtering on
        ``cik``/``fiscal_year`` instead of ``company``."""
        must_conditions = []
        if cik:
            must_conditions.append(FieldCondition(key="cik", match=MatchValue(value=cik)))
        if fiscal_years_in_query:
            must_conditions.append(
                FieldCondition(key="fiscal_year", match=MatchAny(any=fiscal_years_in_query))
            )
        query_filter = Filter(must=must_conditions) if must_conditions else None

        return self.client.query_points(
            collection_name=collection_name,
            query=query,
            using=TEXT_DENSE,
            query_filter=query_filter,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
        )

    def query_by_fact_ids(self, collection_name: str, fact_ids: list[int], limit: int = 10):
        """Reverse HYBRID-path lookup (``XbrlRag_Architecture.md`` "fact_branch"/"narrative_branch" fusion): find prose/table
        chunks cross-linked to specific ``financial_facts.fact_id`` values
        (e.g. the facts a ``lookup`` plan step just resolved), via
        :class:`MatchAny` containment on the ``xbrl_fact_ids`` array field.

        No query vector needed — this is a pure metadata fetch, used to pull
        the narrative chunks discussing a fact the plan executor already
        looked up numerically.
        """
        return self.client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[FieldCondition(key="xbrl_fact_ids", match=MatchAny(any=fact_ids))]
            ),
            limit=limit,
            with_payload=True,
        )
