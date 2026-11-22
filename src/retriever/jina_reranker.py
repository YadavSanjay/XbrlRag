
from typing import List

import torch
from transformers import AutoModel

from src.logging_config import get_logger
from src.retriever.base_reranker import BaseReranker

logger = get_logger(__name__)

# jina-reranker-v3 is *listwise*: ``rerank()`` concatenates the query + a block of
# documents into a single sequence, so one forward pass costs O(total_tokens^2)
# memory. The model already has token-budget blocking to cap a block at
# ``tokenizer.model_max_length`` — but that ships as the Qwen3 sentinel (131072),
# which never triggers a flush, so every candidate is packed into one giant
# forward. With long chunks (SEC tables run to ~6k chars) that attention tensor
# exceeds 6 GB and segfaults the process on CPU (Windows access violation).
#
# We bound it explicitly:
#   * _MAX_DOC_TOKENS    — per-document truncation. 512 is top-k neutral vs the
#                          model default (2048) on real candidates, and packing
#                          shorter docs means fewer blocks (less listwise drift).
#   * _BLOCK_TOKEN_BUDGET — tokens per forward. The model re-blocks to honor it,
#                          keeping the largest attention tensor ~O(4096^2) (<1 GB).
_MAX_DOC_TOKENS = 512
_BLOCK_TOKEN_BUDGET = 4096


class JinaReranker(BaseReranker):
    def __init__(self):
        self.reranker = AutoModel.from_pretrained('jinaai/jina-reranker-v3', dtype="auto", trust_remote_code=True,)
        self.reranker.eval()
        # Force the model's internal token-budget blocking to actually engage:
        # its default model_max_length sentinel disables it -> single-forward OOM.
        self.reranker._ensure_tokenizer()
        self.reranker._tokenizer.model_max_length = _BLOCK_TOKEN_BUDGET
        logger.info(
            "loaded Jina re-ranker model - jinaai/jina-reranker-v3 "
            "(block budget=%d tok, per-doc cap=%d tok)",
            _BLOCK_TOKEN_BUDGET, _MAX_DOC_TOKENS,
        )

    def rerank(self, query: str, chunks_with_text: list[str]) -> list[float]:
        """Score each chunk against *query*; returns scores aligned to input order.

        The model handles any number of candidates safely now that its blocking
        is bounded — no outer batching needed here.
        """
        if not chunks_with_text:
            return []
        with torch.inference_mode():
            results = self.reranker.rerank(
                query,
                chunks_with_text,
                max_doc_length=_MAX_DOC_TOKENS,
            )
        scores = [0.0] * len(chunks_with_text)
        for r in results:
            scores[r["index"]] = float(r["relevance_score"])
        return scores
