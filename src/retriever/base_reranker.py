
import math
import time
from typing import List

from qdrant_client.models import ScoredPoint

from src.logging_config import get_logger
from src.retriever.retriever_util import dedup_chunks, get_pair_for_rerank, get_text_chunks

logger = get_logger(__name__)


class BaseReranker:

    def __init__(self):
        pass


    def rerank_chunks(self, chunk_search_results: dict[str, tuple[list[tuple[str,float]],list[ScoredPoint]]],
                    chunks_with_text:dict[str, tuple[str, bool, str]], top_k=10) -> list[str]:
        return self._rerank_with_single_pass(chunk_search_results, chunks_with_text, top_k=top_k)


    def _rerank_with_single_pass(self,chunk_search_results: dict[str, tuple[list[tuple[str,float]],list[ScoredPoint]]],
                      chunks_with_text:dict[str, tuple[str, bool, str]], top_k=10) -> list[str]:
        rerank_start_time = time.perf_counter()
        original_query_list, original_query_chunks  = chunk_search_results.get("original_query", ([], []))
        subquery_list, subquery_chunks = chunk_search_results.get("subquery", ([], []))
        _, hyde_query_chunks = chunk_search_results.get("hyde", ([], []))

        all_chunks = []
        all_chunks.extend(original_query_chunks)
        all_chunks.extend(subquery_chunks)
        all_chunks.extend(hyde_query_chunks)
        if not all_chunks:
            return []
        chunks_for_reranking = dedup_chunks(all_chunks, limit= top_k * 3)
        chunk_id_to_chunk = {chunk.id: chunk for chunk in chunks_for_reranking}
        original_query, _ = original_query_list[0]

        start_time = time.perf_counter()
        logger.info(f"re-ranking started for {len(chunks_for_reranking)} chunks with original/paraphrased query: {original_query}")
        chunks_with_text_for_reranking = {chunk.id: chunks_with_text[chunk.id] for chunk in chunks_for_reranking if chunk.id in chunks_with_text}
        original_query_rerank_scores = self._get_score_with_query(original_query, chunks_with_text_for_reranking)
        time_taken = time.perf_counter() - start_time
        logger.info(f"re-ranked {len(chunks_for_reranking)} chunks with original/paraphrased query: in {time_taken:.2f} seconds\n")

        start_time = time.perf_counter()
        logger.info(f"re-ranking started for {len(chunks_for_reranking)} chunks with {len(subquery_list)} sub-queries")
        subquery_rerank_scores = self._get_score_with_subqueries(subquery_list, chunks_with_text_for_reranking)
        time_taken = time.perf_counter() - start_time
        logger.info(f"re-ranked {len(chunks_for_reranking)} chunks with sub-queries in {time_taken:.2f} seconds\n")

        recency_boosts = self._get_recency_boost(chunk_id_to_chunk)
        table_relevance_boosts = self._get_table_relevance_boost(chunk_id_to_chunk)

        result = self._get_chunks_in_final_rerank_scores(
            original_query,
            original_query_rerank_scores,
            subquery_rerank_scores,
            recency_boosts,
            table_relevance_boosts,
            chunks_with_text
        )
        time_taken = time.perf_counter() - rerank_start_time
        logger.info(f"TOTAL time re-ranking of {len(chunks_for_reranking)} chunks took {time_taken:.2f} seconds")
        return result


    def _rerank_with_two_pass(self,chunk_search_results: dict[str, tuple[list[tuple[str,float]],list[ScoredPoint]]],
                      chunks_with_text:dict[str, tuple[str, bool, str]], top_k=10) -> list[str]:
        pass


    def rerank(self, query: str, chunks_with_text: list[str]) -> list[float]:
        raise NotImplementedError("Subclasses must implement the 'rerank' method")


    def _get_score_with_query(self,query, chunks_with_text) -> dict[str, float]:
        pairs_for_rerank = get_pair_for_rerank(query, chunks_with_text)
        rank_results = self.rerank(query, get_text_chunks(chunks_with_text))
        query_rerank_scores: dict[str,float] = {chunk_text.split("_")[-1]: score for (_, chunk_text), score in zip(pairs_for_rerank, rank_results)}
        return query_rerank_scores


    def _get_score_with_subqueries(self, subquery_list, chunks_with_text) -> dict[str, float]:
        subquery_rerank_scores: dict[str, float] = {}
        for subquery, weight in subquery_list:
            pairs_for_rerank = get_pair_for_rerank(subquery, chunks_with_text)
            rank_results = self.rerank(subquery, get_text_chunks(chunks_with_text))
            for (_, chunk_text), score in zip(pairs_for_rerank, rank_results):
                chunk_id = chunk_text.split("_")[-1]
                if chunk_id not in subquery_rerank_scores:
                    subquery_rerank_scores[chunk_id] = 0.0
                subquery_rerank_scores[chunk_id] += weight * score
        return subquery_rerank_scores


    def _get_recency_boost(self, chunk_id_to_chunk: dict[str, ScoredPoint]) -> dict[str, float]:
        chunk_id_recency_boost: dict[str, float] = {}
        for chunk_id, chunk in chunk_id_to_chunk.items():
            if 'year' in chunk.payload and chunk.payload['year'] is not None:
                year = int(chunk.payload['year'])
                chunk_id_recency_boost[chunk_id] = math.exp(-(2025 - year) / 3)
            else:
                chunk_id_recency_boost[chunk_id] = 0.0

        return chunk_id_recency_boost


    def _get_table_relevance_boost(self, chunk_id_to_chunk: dict[str, ScoredPoint]) -> dict[str, float]:
        chunk_id_table_relevance_boost: dict[str, float] = {}
        for chunk_id, chunk in chunk_id_to_chunk.items():
            if 'is_table' in chunk.payload and chunk.payload['is_table']:
                chunk_id_table_relevance_boost[chunk_id] = 0.50
            else:
                chunk_id_table_relevance_boost[chunk_id] = 0.0
        return chunk_id_table_relevance_boost


    def _get_chunks_in_final_rerank_scores(self,original_query, original_query_rerank_scores, subquery_rerank_scores, recency_boosts,
                                            table_relevance_boosts,chunks_with_text) -> list[str]:
        final_rerank_scores: dict[str, float] = {}
        for chunk_id in original_query_rerank_scores.keys():
            original_query_score = original_query_rerank_scores.get(chunk_id, 0.0)
            subquery_score = subquery_rerank_scores.get(chunk_id, 0.0)
            recency_boost = recency_boosts.get(chunk_id, 0.0)
            table_relevance_boost = table_relevance_boosts.get(chunk_id, 0.0)
            final_score = 0.45* original_query_score + 0.35* subquery_score + 0.2*table_relevance_boost
            final_rerank_scores[chunk_id] = final_score

        sorted_final_chunks = sorted(final_rerank_scores.items(), key=lambda x: x[1], reverse=True)
        sorted_final_chunk_ids = [chunk_id for chunk_id, _ in sorted_final_chunks]
        self._log_chunk_rerank_scores(original_query, sorted_final_chunk_ids, final_rerank_scores,original_query_rerank_scores,
                                       subquery_rerank_scores, table_relevance_boosts, recency_boosts)
        sorted_chunks = []
        for chunk_id in sorted_final_chunk_ids:
            chunk_text, _, _ = chunks_with_text[chunk_id]
            sorted_chunks.append(chunk_text)
        return sorted_chunks


    def _log_chunk_rerank_scores(self,original_query, sorted_final_chunk_ids: list[str],
                                 rerank_scores: dict[str, float], original_query_rerank_scores: dict[str, float],
                                 subquery_rerank_scores: dict[str, float], table_relevance_boosts: dict[str, float],
                                 recency_boosts: dict[str, float]) -> None:
        logger.info(f"\n\n---- chunks rerank scores for query: \n{original_query} ----\n\n")
        header = "chunk_id,final_score,original_contrib,subquery_contrib,table_rel_contrib,original,subquery,table_rel"
        logger.info(header)
        for chunk_id in sorted_final_chunk_ids:
            final_score = rerank_scores.get(chunk_id, 0.0)
            original_contrib = 0.50 * original_query_rerank_scores.get(chunk_id, 0.0)
            subquery_contrib = 0.35 * subquery_rerank_scores.get(chunk_id, 0.0)
            table_rel_contrib = 0.15 * table_relevance_boosts.get(chunk_id, 0.0)
            original_score = original_query_rerank_scores.get(chunk_id, 0.0)
            subquery_score = subquery_rerank_scores.get(chunk_id, 0.0)
            table_rel_score = table_relevance_boosts.get(chunk_id, 0.0)
            log_line = f"{chunk_id},{final_score:.4f},{original_contrib:.4f},{subquery_contrib:.4f},{table_rel_contrib:.4f}," + \
                       f"{original_score:.4f},{subquery_score:.4f},{table_rel_score:.4f}"
            logger.info(log_line)
        logger.info(f"\n\n-------------\n")
