"""Chunk utilities shared by the reranker flows.

**torch-free at module scope, on purpose.** `BaseReranker` imports this module,
so every reranker — including `RemoteReranker`, whose whole point is *not*
loading a model in-process — pulls it in. The serving and ingest images install
no torch (`requirements-serve.txt` / `requirements-ingest.txt`), so a top-level
`import torch` here breaks them at run time with `ModuleNotFoundError: No module
named 'torch'`. Only the two vector-math helpers below actually need torch, and
neither is on the remote-rerank path, so they import it lazily. Enforced by
`test/unit/test_torch_free_contract.py`; do NOT hoist these imports.
"""
from typing import List

from qdrant_client.models import ScoredPoint

from src.logging_config import get_logger

logger = get_logger(__name__)


def get_cosine_similarity(a, b, are_normalized: bool = True) -> float:
    import torch.nn.functional as F

    if not are_normalized:
        a = F.normalize(a, p=2, dim=1)
        b = F.normalize(b, p=2, dim=1)
    return F.cosine_similarity(a, b).item()


def maximal_marginal_relevance(
    query_embedding,
    candidate_embeddings: dict,
    lambda_param: float = 0.5,
    top_k: int = None
) -> list:
    """
    Implements Maximal Marginal Relevance (MMR) algorithm.
    
    Args:
        query_embedding: Query embedding tensor or numpy array
        candidate_embeddings: Dict mapping candidate IDs to their embedding tensors or numpy arrays
        lambda_param: Trade-off parameter between relevance and diversity (0-1)
        top_k: Number of top candidates to return (default: all candidates)
    
    Returns:
        List of candidate IDs ranked by MMR score
    """
    import numpy as np
    import torch
    import torch.nn.functional as F

    if not candidate_embeddings:
        return []

    # Convert query embedding to torch tensor if it's a numpy array
    if isinstance(query_embedding, np.ndarray):
        query_embedding = torch.from_numpy(query_embedding).float()

    # Normalize query embedding
    query_norm = F.normalize(query_embedding, p=2, dim=-1)

    # Normalize all candidate embeddings (convert to torch if needed)
    candidates = list(candidate_embeddings.keys())
    candidate_tensors = {}
    for cid, emb in candidate_embeddings.items():
        if isinstance(emb, np.ndarray):
            emb = torch.from_numpy(emb).float()
        candidate_tensors[cid] = F.normalize(emb, p=2, dim=-1)

    # Calculate relevance scores for all candidates
    relevance_scores = {
        cid: F.cosine_similarity(query_norm, emb, dim=-1).item()
        for cid, emb in candidate_tensors.items()
    }

    selected = []
    remaining = set(candidates)

    if top_k is None:
        top_k = len(candidates)

    # Select first candidate with highest relevance
    first_candidate = max(remaining, key=lambda x: relevance_scores[x])
    selected.append(first_candidate)
    remaining.remove(first_candidate)

    # Iteratively select remaining candidates
    while remaining and len(selected) < top_k:
        mmr_scores = {}

        for candidate in remaining:
            # Relevance to query
            relevance = relevance_scores[candidate]

            # Maximum similarity to already selected candidates
            max_similarity = max(
                F.cosine_similarity(
                    candidate_tensors[candidate],
                    candidate_tensors[selected_id],
                    dim=-1
                ).item()
                for selected_id in selected
            )

            # MMR score
            mmr_scores[candidate] = (
                lambda_param * relevance - (1 - lambda_param) * max_similarity
            )

        # Select candidate with highest MMR score
        next_candidate = max(mmr_scores, key=mmr_scores.get)
        selected.append(next_candidate)
        remaining.remove(next_candidate)

    return selected


def dedup_chunks(all_chunks: list[ScoredPoint], limit: int= None) -> list[ScoredPoint]:
    if not limit:
        limit = len(all_chunks)
    chunk_id_set = set()
    deduped_chunks = []
    sorted_chunks = sorted(all_chunks, key=lambda x: x.score, reverse=True)
    for chunk in sorted_chunks:
        if chunk.id not in chunk_id_set:
            deduped_chunks.append(chunk)
            chunk_id_set.add(chunk.id)
            if len(deduped_chunks) >= limit:
                break
    logger.info(f"limit: {limit} deduplicated chunks from {len(all_chunks)} to {len(deduped_chunks)}")
    return deduped_chunks


def get_pair_for_rerank(query: str, chunk_texts: dict[str, tuple[str, bool, str]]) -> list[tuple[str, str]]:
    pairs = []
    for chunk_id, (text, is_table, table_json) in chunk_texts.items():
        pairs.append((query, text))
    return pairs


def get_text_chunks(chunk_texts: dict[str, tuple[str, bool, str]]) -> list[str]:
    texts = []
    for chunk_id, (text, is_table, table_json) in chunk_texts.items():
        texts.append(text)
    return texts


if __name__ == "__main__":
    import torch

    vec1 = torch.tensor([[0.231, 0.333, 0.987]])
    vec2 = torch.tensor([[0.321, 0.543, 0.878]])

    sim = get_cosine_similarity(vec1, vec2, are_normalized=True)
    print(f"Cosine Similarity: {sim}")

