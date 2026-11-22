import os
from typing import List, Optional, Union

from qdrant_client import QdrantClient
from qdrant_client.models import (
    DatetimeExpression,
    DatetimeKeyExpression,
    DecayParamsExpression,
    Distance,
    FieldCondition,
    Filter,
    FormulaQuery,
    Fusion,
    FusionQuery,
    HnswConfig,
    HnswConfigDiff,
    LinDecayExpression,
    MatchAny,
    MatchValue,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    SumExpression,
    VectorParams,
)

from src.chunking.chunk import ChunkMetadata
from src.embeddings.base import DenseEmbedding, EmbeddingMap, SparseEmbedding
from src.logging_config import get_logger

logger = get_logger(__name__)


TEXT_DENSE = "text_dense"
TEXT_SPARSE = "text_sparse"


class QdrantVectorDBClient:

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        timeout: int = 60,
        api_key: str | None = None,
        https: bool | None = None,
        verify: bool | None = None,
    ):
        # Connection params fall back to env when not passed explicitly, so a
        # non-localhost Qdrant (in-VPC on AWS, or the `qdrant` service in the
        # local compose stack) is reachable without editing call sites. Env
        # unset => localhost:6333, i.e. local behavior is unchanged.
        #   QDRANT_HOST / QDRANT_PORT / QDRANT_API_KEY / QDRANT_HTTPS / QDRANT_VERIFY
        host = host if host is not None else os.environ.get("QDRANT_HOST", "localhost")
        port = (
            int(port)
            if port is not None
            else int(os.environ.get("QDRANT_PORT", "6333"))
        )
        if api_key is None:
            api_key = os.environ.get("QDRANT_API_KEY") or None
        if https is None:
            env_https = os.environ.get("QDRANT_HTTPS", "")
            https = (
                env_https.strip().lower() in ("1", "true", "yes") if env_https else None
            )
        # TLS certificate verification. The in-VPC Qdrant on AWS fronts a
        # SELF-SIGNED cert (CN qdrant.<namespace>.internal), so QDRANT_VERIFY=false
        # skips verification there while TLS stays ON; it defaults to verifying
        # everywhere else. Only meaningful when https is on. Env unset => not passed
        # => qdrant-client's default (verify), so local behavior is unchanged.
        if verify is None:
            env_verify = os.environ.get("QDRANT_VERIFY", "")
            verify = (
                env_verify.strip().lower() not in ("0", "false", "no")
                if env_verify
                else None
            )

        # Default REST timeout is 5s; payload-index builds on a freshly created
        # on-disk-HNSW collection can exceed that and raise ReadTimeout, so use
        # a generous client-wide timeout (matches create_collection's timeout=60).
        client_kwargs = {"host": host, "port": port, "timeout": timeout}
        if api_key:
            client_kwargs["api_key"] = api_key
        if https is not None:
            client_kwargs["https"] = https
        if verify is not None:
            client_kwargs["verify"] = verify
        self.client = QdrantClient(**client_kwargs)

    def create_collection(
        self, collection_name: str, include_sparse: bool = False, dimension: int = 1024
    ):
        if self.client.collection_exists(collection_name):
            logger.info(f"collection {collection_name} already exists, not creating.")
            return

        vectors_config = {
            TEXT_DENSE: VectorParams(size=dimension, distance=Distance.COSINE)
        }
        sparse_vectors_config = None
        if include_sparse:
            sparse_vectors_config = {TEXT_SPARSE: SparseVectorParams()}
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_vectors_config,
            hnsw_config=HnswConfigDiff(m=16, on_disk=True),
            timeout=60,
        )

    def upload_collection(
        self,
        collection_name: str,
        vector_data: dict[str, DenseEmbedding] | EmbeddingMap,
        payload_data: dict[str, tuple[str, ChunkMetadata]],
        wait: bool = True,
    ):
        points = []
        # Check if the first item in vector_data contains a tuple (dense + sparse)
        if vector_data:
            first_value = next(iter(vector_data.values()))
            has_sparse = isinstance(first_value, tuple) and len(first_value) == 2
        else:
            has_sparse = False

        if has_sparse:
            # dense + sparse embedding of actual text chunks
            for chunk_id, dense_sparse_embedding in vector_data.items():
                chunk_file_path, metadata = payload_data.get(chunk_id)
                payload = (
                    self._get_text_payload(chunk_file_path, metadata)
                    if metadata
                    else {}
                )
                point = PointStruct(
                    id=chunk_id,
                    vector={
                        TEXT_DENSE: dense_sparse_embedding[0].values,
                        TEXT_SPARSE: {
                            "indices": dense_sparse_embedding[1].indices,
                            "values": dense_sparse_embedding[1].values,
                        },
                    },
                    payload=payload,
                )
                points.append(point)
        else:
            # dense embedding of text chunk metadata
            for chunk_id, embedding in vector_data.items():
                chunk_file_path, metadata = payload_data.get(chunk_id)
                payload = (
                    self._get_metadata_payload(chunk_file_path, metadata)
                    if metadata
                    else {}
                )
                point = PointStruct(
                    id=chunk_id, vector={TEXT_DENSE: embedding.values}, payload=payload
                )
                points.append(point)

        return self.client.upload_points(
            collection_name=collection_name,
            points=points,
            wait=wait,
            parallel=2,
            max_retries=2,
        )

    def query_by_id(self, collection_name: str, id: str):
        return self.client.query_points(collection_name=collection_name, query=id)

    def query_metadata(
        self,
        collection_name: str,
        query: list[float],
        company: str = None,
        limit: int = 10,
        score_threshold: float = 0.9,
    ):
        query_filter = None
        if company:
            query_filter = Filter(
                must=[FieldCondition(key="company", match=MatchValue(value=company))]
            )
        return self.client.query_points(
            collection_name=collection_name,
            query=query,
            using=TEXT_DENSE,
            query_filter=query_filter,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
            ## Disabled time decay boost based query for now
            # prefetch = Prefetch(
            #     query = query,
            #     using=TEXT_DENSE,
            #     limit = limit * 2
            # ),
            # query= FormulaQuery(
            #     formula = SumExpression(
            #         sum = [
            #             "$score",
            #                 LinDecayExpression(
            #                 lin_decay = DecayParamsExpression(
            #                     x = DatetimeKeyExpression(datetime_key="year_datetime"),
            #                     target = DatetimeExpression(datetime="2024-12-01T00:00:00Z"),
            #                     scale=2*31536000,  # 1 year in seconds
            #                     midpoint=0.5
            #                 )
            #             )
            #         ]
            #     )
            # ),
            # query_filter=query_filter,
            # limit=limit,
            # score_threshold=score_threshold,
            # with_payload=True
        )

    def hybrid_query_text(
        self,
        collection_name: str,
        vector_d: DenseEmbedding,
        vector_s: SparseEmbedding,
        fiscal_years_in_query: list[str],
        limit: int = 15,
    ):

        return self.client.query_points(
            collection_name=collection_name,
            # query = vector_d.values,
            # using=TEXT_DENSE,
            # limit=limit,
            # with_payload=True
            prefetch=[
                Prefetch(
                    query=SparseVector(
                        indices=vector_s.indices, values=vector_s.values
                    ),
                    using=TEXT_SPARSE,
                    limit=2 * limit,
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="year", match=MatchAny(any=fiscal_years_in_query)
                            )
                        ]
                    ),
                ),
                Prefetch(
                    query=vector_d.values,
                    using=TEXT_DENSE,
                    limit=2 * limit,
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="year", match=MatchAny(any=fiscal_years_in_query)
                            )
                        ]
                    ),
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            with_payload=True,
            ## Disabled time decay boost based query for now
            # stage 1: prefetch both dense and sparse vectors
            # prefetch=[
            #     Prefetch(
            #         prefetch=[
            #             Prefetch(query=SparseVector(indices = vector_s.indices, values=vector_s.values), using=TEXT_SPARSE, limit=2*limit),
            #             Prefetch(query=vector_d.values, using=TEXT_DENSE, limit=2*limit)
            #         ],
            #         query = FusionQuery(fusion=Fusion.RRF),
            #         limit=2*limit
            #     )
            # ],
            # Stage 2: rescore fused candidates with time decay boost
            # query = FormulaQuery(
            #     formula = SumExpression(
            #         sum = [
            #             "$score",
            #             LinDecayExpression(
            #                 lin_decay = DecayParamsExpression(
            #                     x = DatetimeKeyExpression(datetime_key="year_datetime"),
            #                     target = DatetimeExpression(datetime="2024-12-01T00:00:00Z"),
            #                     scale=2*31536000,  # 1 year in seconds
            #                     midpoint=0.5
            #                 )
            #             )
            #         ]
            #     )
            # ),
            # limit=limit,
            # with_payload=True
        )

    def prepare_for_ingestion(self, collection_name: str):
        self.client.update_collection(
            collection_name=collection_name, hnsw_config=HnswConfigDiff(m=0)
        )

    def update_post_ingestion(self, collection_name: str):
        self.client.update_collection(
            collection_name=collection_name, hnsw_config=HnswConfigDiff(m=16)
        )

    def _get_text_payload(self, chunk_file_path: str, metadata: ChunkMetadata) -> dict:
        return {
            "doctype": metadata["doctype"],
            "year": metadata["year"],
            "year_datetime": f"{metadata['year']}-12-30T00:00:00Z",
            "company": metadata["company"],
            "section": metadata["section"],
            "subsection": metadata["subsection"],
            "is_table": metadata["is_table"],
            # 'table_json': metadata['table_json'],
            "prev_chunk_id": metadata["prev_chunk_id"],
            "next_chunk_id": metadata["next_chunk_id"],
            "chunk_file_path": chunk_file_path,
        }

    def _get_metadata_payload(
        self, chunk_file_path: str, metadata: ChunkMetadata
    ) -> dict:
        return {
            "doctype": metadata["doctype"],
            "year": metadata["year"],
            "year_datetime": f"{metadata['year']}-12-30T00:00:00Z",
            "company": metadata["company"],
            "section": metadata["section"],
            "subsection": metadata["subsection"],
            "is_table": metadata["is_table"],
            "chunk_file_path": chunk_file_path,
        }


if __name__ == "__main__":
    client = QdrantClient(host="localhost", port=6333)
    print("Qdrant client initialized:", client)
    client.create_collection(
        collection_name="test_collection",
        vectors_config=VectorParams(size=4, distance=Distance.DOT),
    )
    operation_info = client.upsert(
        collection_name="test_collection",
        wait=True,
        points=[
            PointStruct(
                id=1, vector=[0.05, 0.61, 0.76, 0.74], payload={"city": "Berlin"}
            ),
            PointStruct(
                id=2, vector=[0.19, 0.81, 0.75, 0.11], payload={"city": "London"}
            ),
            PointStruct(
                id=3, vector=[0.36, 0.55, 0.47, 0.94], payload={"city": "Moscow"}
            ),
            PointStruct(
                id=4, vector=[0.18, 0.01, 0.85, 0.80], payload={"city": "New York"}
            ),
            PointStruct(
                id=5, vector=[0.24, 0.18, 0.22, 0.44], payload={"city": "Beijing"}
            ),
            PointStruct(
                id=6, vector=[0.35, 0.08, 0.11, 0.44], payload={"city": "Mumbai"}
            ),
        ],
    )
    print(operation_info)
    search_result = client.query_points(
        collection_name="test_collection",
        query=[0.2, 0.1, 0.9, 0.7],
        with_payload=False,
        limit=3,
    ).points
    print(search_result)

    search_result = client.query_points(
        collection_name="test_collection",
        query=[0.2, 0.1, 0.9, 0.7],
        query_filter=Filter(
            must=[FieldCondition(key="city", match=MatchValue(value="London"))]
        ),
        with_payload=True,
        limit=3,
    ).points
    print(search_result)
