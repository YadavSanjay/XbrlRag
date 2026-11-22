
from typing import List

from FlagEmbedding import FlagLLMReranker, FlagReranker

from src.logging_config import get_logger
from src.retriever.base_reranker import BaseReranker
from src.retriever.retriever_util import get_pair_for_rerank

logger = get_logger(__name__)


class BGEReranker(BaseReranker):

    def __init__(self, batch_size: int = 4):
        #self.reranker = FlagLLMReranker("BAAI/bge-reranker-v2-Gemma", use_fp16=True, normalize=True)
        self.reranker = FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=True,  normalize=True)
        self.batch_size = batch_size


    def rerank(self, query: str, chunks_with_text: list[str]) -> list[float]:
        pairs_for_rerank = get_pair_for_rerank(query, chunks_with_text)
        rank_results = self.reranker.compute_score(pairs_for_rerank, batch_size=self.batch_size)
        return rank_results
