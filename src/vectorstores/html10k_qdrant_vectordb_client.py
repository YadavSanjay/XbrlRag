# The Qdrant collection html10k_text of the HTML 10-K flow, and its payload contract.
#
# Nothing in this collection comes from XBRL: the payload is the chunk and the filing's registry
# row, which the caller reads. This module reads no database and derives no fiscal label.
#
# Spec: docs/specs/html10k-chunks.md.

from __future__ import annotations

from collections.abc import Mapping, Sequence

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from src.chunking.html10k_chunking import Html10kChunk
from src.embeddings.base import DenseEmbedding, SparseEmbedding
from src.vectorstores.qdrant_vectordb_client import TEXT_DENSE, TEXT_SPARSE

COLLECTION_NAME = "html10k_text"
_DIMENSION = 1024
_FILING_KEYS = frozenset(
    {
        "cik",
        "ticker",
        "company_name",
        "accession_no",
        "fiscal_year",
        "fiscal_period",
        "form_type",
        "period_end",
    }
)
_PAYLOAD_INDEXES: dict[str, PayloadSchemaType] = {
    "cik": PayloadSchemaType.KEYWORD,
    "ticker": PayloadSchemaType.KEYWORD,
    "accession_no": PayloadSchemaType.KEYWORD,
    "fiscal_year": PayloadSchemaType.INTEGER,
    "fiscal_period": PayloadSchemaType.KEYWORD,
    "form_type": PayloadSchemaType.KEYWORD,
    "section_key": PayloadSchemaType.KEYWORD,
    "section_keys": PayloadSchemaType.KEYWORD,
    "contains_table": PayloadSchemaType.BOOL,
}


def build_payload(chunk: Html10kChunk, filing: Mapping[str, object]) -> dict[str, object]:
    """Build the payload of one chunk.

    Args:
        chunk: The chunk.
        filing: The filing's registry row, with the fiscal label both flows share.

    Returns:
        The payload.

    Raises:
        ValueError: ``filing`` has a missing or unknown key, a non-int ``fiscal_year``, or another
            filing's ``accession_no``.
    """
    if set(filing) != _FILING_KEYS:
        raise ValueError(f"bad filing keys: {sorted(set(filing) ^ _FILING_KEYS)}")
    if type(filing["fiscal_year"]) is not int:
        raise ValueError(f"fiscal_year must be an int, not {filing['fiscal_year']!r}")
    if filing["accession_no"] != chunk.accession_no:
        raise ValueError(f"{filing['accession_no']} is not {chunk.accession_no}")
    return {
        "chunk_id": chunk.chunk_id,
        "accession_no": chunk.accession_no,
        "text": chunk.text,
        "cik": filing["cik"],
        "ticker": filing["ticker"],
        "company_name": filing["company_name"],
        "fiscal_year": filing["fiscal_year"],
        "fiscal_period": filing["fiscal_period"],
        "form_type": filing["form_type"],
        "period_end": filing["period_end"],
        "section_key": chunk.section_key,
        "section_label": chunk.section_label,
        "section_keys": list(chunk.section_keys),
        "html_char_start": chunk.char_start,
        "html_char_end": chunk.char_end,
        "contains_table": chunk.kind == "table",
        "table_index": chunk.table_index,
        "part": chunk.part,
        "parts": chunk.parts,
        "prev_chunk_id": chunk.prev_chunk_id,
        "next_chunk_id": chunk.next_chunk_id,
    }


def embedding_input(payload: Mapping[str, object]) -> str:
    """Return the text to embed for a payload.

    Args:
        payload: A payload from ``build_payload``.

    Returns:
        The company, fiscal year and section label, then the chunk's text.
    """
    company = payload["ticker"] or payload["company_name"] or payload["cik"]
    head = f"{company} FY{payload['fiscal_year']}"
    if payload["section_label"]:
        head = f"{head} {payload['section_label']}"
    return f"{head} :: {payload['text']}"


class Html10kQdrantVectorDBClient:
    """The ``html10k_text`` collection, over a Qdrant client passed in."""

    def __init__(self, client: QdrantClient) -> None:
        """Keep the client; nothing is contacted.

        Args:
            client: The Qdrant client.
        """
        self.client = client

    def ensure_collection(self) -> None:
        """Create the collection when it doesn't exist, then its payload indexes."""
        if not self.client.collection_exists(collection_name=COLLECTION_NAME):
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config={
                    TEXT_DENSE: VectorParams(size=_DIMENSION, distance=Distance.COSINE)
                },
                sparse_vectors_config={TEXT_SPARSE: SparseVectorParams()},
            )
        for field_name, schema in _PAYLOAD_INDEXES.items():
            self.client.create_payload_index(
                collection_name=COLLECTION_NAME, field_name=field_name, field_schema=schema
            )

    def upload_chunks(
        self,
        payloads: Sequence[Mapping[str, object]],
        vectors: Mapping[str, tuple[DenseEmbedding, SparseEmbedding]],
    ) -> None:
        """Write one point per payload.

        Args:
            payloads: The payloads from ``build_payload``.
            vectors: ``chunk_id`` to its dense and sparse vectors.

        Raises:
            ValueError: A payload has no vector, or a vector has no payload.
        """
        ids = [str(payload["chunk_id"]) for payload in payloads]
        if set(ids) != set(vectors) or len(set(ids)) != len(ids):
            raise ValueError(
                f"{len(ids)} payloads do not match {len(vectors)} vectors one for one"
            )
        points = []
        for chunk_id, payload in zip(ids, payloads, strict=True):
            dense, sparse = vectors[chunk_id]
            points.append(
                PointStruct(
                    id=chunk_id,
                    vector={
                        TEXT_DENSE: dense.values,
                        TEXT_SPARSE: SparseVector(indices=sparse.indices, values=sparse.values),
                    },
                    payload=dict(payload),
                )
            )
        self.client.upload_points(collection_name=COLLECTION_NAME, points=points, wait=True)
