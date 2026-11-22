"""On-prem BAAI BGE-M3 embedding implementation."""

from __future__ import annotations

from collections.abc import Sequence

from src.embeddings.base import (
    BaseEmbeddingGenerator,
    DenseEmbedding,
    EmbeddingMap,
    SparseEmbedding,
)
from src.logging_config import get_logger

logger = get_logger(__name__)


class BGEEmbeddingGenerator(BaseEmbeddingGenerator):

    def __init__(
        self,
        model: str = "BAAI/bge-m3",
        batch_size: int = 12,
        max_length: int = 8192,
    ):
        self.model_name = model
        # FlagEmbedding's encode() defaults to batch_size=256, which builds one
        # huge forward pass and segfaults the process (0xC0000005 / silent exit,
        # no traceback) on CPU. Cap it to a memory-safe value; pass it (and an
        # explicit max_length) to every encode call below.
        self.batch_size = batch_size
        self.max_length = max_length
        # Import FlagEmbedding (→ torch) lazily, only when the local model is
        # actually instantiated. This keeps `import bge_embedding` (and every
        # module that references these classes) torch-free, so the ingest image
        # can stay slim and embed over HTTP via RAGINFER_URL (RemoteEmbedder)
        # without pulling torch.
        from FlagEmbedding import BGEM3FlagModel

        self.model = BGEM3FlagModel("BAAI/bge-m3")
        logger.info(
            "Initialized BGEEmbeddingGenerator with model=%s batch_size=%d max_length=%d",
            model,
            batch_size,
            max_length,
        )

    def dense_embed_texts(
        self, id_texts: list[tuple[str, str]]
    ) -> dict[str, DenseEmbedding]:
        if not id_texts:
            return []

        texts_only = [text for _, text in id_texts]
        vectors = self.model.encode(
            texts_only, batch_size=self.batch_size, max_length=self.max_length
        )
        dense_vectors = vectors["dense_vecs"]
        embedding_map = {}
        for idx in range(len(id_texts)):
            id = id_texts[idx][0]
            dense_emb = DenseEmbedding(values=dense_vectors[idx])
            embedding_map[id] = dense_emb
        logger.debug("Produced %s BGE dense embeddings", len(dense_vectors))
        return embedding_map

    def sparse_embed_texts(
        self, id_texts: list[tuple[str, str]]
    ) -> dict[str, SparseEmbedding]:
        if not id_texts:
            return []

        logger.debug("Generating sparse embeddings for %s texts", len(id_texts))
        texts_only = [text for _, text in id_texts]
        vectors = self.model.encode(
            texts_only,
            return_dense=False,
            return_sparse=True,
            batch_size=self.batch_size,
            max_length=self.max_length,
        )
        sparse_vectors = vectors["lexical_weights"]
        logger.debug("Produced %s BGE sparse embeddings", len(sparse_vectors))
        embedding_map = {}
        for idx in range(len(id_texts)):
            id = id_texts[idx][0]
            sparse_emb = self.dict_to_sparse_list(sparse_vectors[idx])
            embedding_map[id] = sparse_emb
        return embedding_map

    def embed_texts(self, id_texts: list[tuple[str, str]]) -> EmbeddingMap:
        """
        generates both dense and sparse embeddings for the given text items
        """
        if not id_texts:
            return []

        logger.debug("Generating dense embeddings for %s texts", len(id_texts))
        texts_only = [text for _, text in id_texts]
        vectors = self.model.encode(
            texts_only,
            return_dense=True,
            return_sparse=True,
            batch_size=self.batch_size,
            max_length=self.max_length,
        )
        dense_vectors = vectors["dense_vecs"]
        sparse_vectors = vectors["lexical_weights"]
        logger.debug("Produced %s BGE dense embeddings", len(dense_vectors))
        logger.debug("Produced %s BGE sparse embeddings", len(sparse_vectors))
        embedding_map = {}
        for idx in range(len(id_texts)):
            id = id_texts[idx][0]
            dense_emb = DenseEmbedding(values=dense_vectors[idx])
            sparse_emb = self.dict_to_sparse_list(sparse_vectors[idx])
            embedding_map[id] = (dense_emb, sparse_emb)
        return embedding_map


if __name__ == "__main__":
    from FlagEmbedding import BGEM3FlagModel

    model = BGEM3FlagModel("BAAI/bge-m3")
    sentences_1 = ["What is BGE M3?", "Definition of BM25"]
    sentences_2 = [
        "BGE M3 is an embedding model supporting dense retrieval, lexical matching and multi-vector interaction.",
        "BM25 is a bag-of-words retrieval function that ranks a set of documents based on the query terms appearing in each document",
    ]
    embeddings_1 = model.encode(sentences_1 + sentences_2, return_sparse=True)
    dense_1 = embeddings_1["dense_vecs"]
    print(dense_1[0][:10])
    sparse_1 = embeddings_1["lexical_weights"]
    print(sparse_1[0])

    embeddings_2 = model.encode(
        sentences_2 + sentences_1, return_dense=False, return_sparse=True
    )
    dense_2 = embeddings_2["dense_vecs"]
    print(dense_2)
    sparse_2 = embeddings_2["lexical_weights"]
    print(sparse_2[2])
    if 1 == 2:
        from src.embeddings.bge_embedding import BGEEmbeddingGenerator

        bge_generator = BGEEmbeddingGenerator()
        sample_chunk_file = "data/pagebreakdown/2024/2024-10K-COMMUNICATIONSERVICES-GOOGL/chunks/base_chunked/page_5_6_283e68cafc24af7a5bb1604e5.json"
        import json

        chunk_data = {}
        id_texts = []
        with open(sample_chunk_file, encoding="utf-8") as f:
            chunk_data = json.load(f)
        for idx in range(len(chunk_data)):
            chunk = chunk_data[idx]
            id = chunk["id"]
            text = chunk["text"]
            id_texts.append((id, text))
        embeddings = bge_generator.embed_texts(id_texts)
        for id, (dense_emb, sparse_emb) in embeddings.items():
            print(f"Chunk ID: {id}")
            print(f"  Dense Embedding (first 10 values): {dense_emb.values[:10]}")
            print(f"  Sparse Embedding (first 10 values): {sparse_emb.values[:10]}")
