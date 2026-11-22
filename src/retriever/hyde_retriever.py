import time
from typing import List

from qdrant_client.models import ScoredPoint

from src.logging_config import get_logger
from src.retriever.base_retriever import BaseRetriever
from src.retriever.llm_hyde_generator import HyDEAnswerGenerator
from src.retriever.llm_query_rewriter import QueryRewriter
from src.retriever.llm_query_router import ROUTER_DECISION, QueryRouter
from src.retriever.subquery_retriever import SubQueryRetriever

logger = get_logger(__name__)


class HyDEQueryRetriever(BaseRetriever):

    def __init__(self):
        super().__init__()
        self.query_router = QueryRouter()
        self.query_rewriter = QueryRewriter()
        self.hyde_generator = HyDEAnswerGenerator()


    def retrieve_chunks(self, query, top_k = 10) -> dict[str,tuple[list[tuple[str,float]], list[ScoredPoint]]]:
        route_decision = self.query_router.route_query(query)
        logger.info(f"routing decision: {route_decision.name}")

        if route_decision != ROUTER_DECISION.DIRECT:
            paraphrased_query = self.query_rewriter.paraphrase_query(query)
        else:
            paraphrased_query = query

        if route_decision == ROUTER_DECISION.PARAPHRASE:
            # keeping 25 chunks for paraphrased query and 10(max) for hyde answers (70/30 split)
            start_time = time.perf_counter()
            merged_chunks = super().retrieve_chunks(paraphrased_query, 2.5* top_k)
            _, paraphrased_q_chunks = merged_chunks.get("original_query", ([], []))

            hyde_answers = self.hyde_generator.generate_hyde_answer(query)
            hyde_chunks = []
            for hyde_answer in hyde_answers:
                hyde_results = super().retrieve_chunks(hyde_answer, top_k, type="hyde")
                _, chunks = hyde_results.get("hyde", ([], []))
                hyde_chunks.extend(chunks)
            deduped_hyde_chunks = super().dedup_chunks(hyde_chunks, limit=top_k)

            hyde_query_val = [(query,1.0)]
            merged_chunks["hyde"] = (hyde_query_val, deduped_hyde_chunks)

            time_taken = time.perf_counter() - start_time
            logger.info(f"using PARAPHRASE + HyDE retrieval with {len(paraphrased_q_chunks)}/25 paraphrased chunks and {len(deduped_hyde_chunks)}/10 HyDE chunks - took {time_taken:.2f} seconds")

            return merged_chunks

        elif route_decision == ROUTER_DECISION.DECOMPOSE or route_decision == ROUTER_DECISION.DECOMPOSE_AND_PARAPHRASE:
            # 12 chunks for paraphrased query, 24 chunks from subqueries and 12 from hyde
            start_time = time.perf_counter()
            merged_chunks = super().retrieve_chunks(paraphrased_query, 1.2* top_k)
            _, paraphrased_q_chunks = merged_chunks.get("original_query", ([], []))

            # Decompose into subqueries, retrieve and merge
            subqueries = self.query_rewriter.decompose_paraphrase_query(query)
            subquery_chunks = []
            for subquery, weight in subqueries:
                subquery_results = super().retrieve_chunks(subquery, top_k, type="subquery")
                _, chunks = subquery_results.get("subquery")
                subquery_chunks.extend(chunks)
            sub_query_merged_results = super().dedup_chunks(subquery_chunks, limit=2.4*top_k)
            merged_chunks["subquery"] = (subqueries, sub_query_merged_results)
            # HyDE on original query, retrieve and merge
            hyde_answers = self.hyde_generator.generate_hyde_answer(query)
            hyde_chunks = []
            for hyde_answer in hyde_answers:
                hyde_results = super().retrieve_chunks(hyde_answer, top_k, type="hyde")
                _, chunks = hyde_results.get("hyde", ([], []))
                hyde_chunks.extend(chunks)
            hyde_merged_results = super().dedup_chunks(hyde_chunks, limit=1.2*top_k)
            hyde_query_val = [(query,1.0)]
            merged_chunks["hyde"] = (hyde_query_val, hyde_merged_results)

            time_taken = time.perf_counter() - start_time
            logger.info(f"\n using DECOMPOSE_AND_HYDE retrieval with {len(subqueries)} subqueries and {len(hyde_answers)} HyDE answers, \
                        retrieved {len(paraphrased_q_chunks)} : {len(sub_query_merged_results)} : {len(hyde_merged_results)} chunks - took {time_taken:.2f} seconds \n")

            return merged_chunks

        else:
            logger.info("using DIRECT retrieval")
            # 25 chunks from direct query and 10 (max) from hyde answers (70/30 split)
            start_time = time.perf_counter()
            merged_chunks = super().retrieve_chunks(query, 2.5*top_k)
            _, direct_query_chunks = merged_chunks.get("original_query", ([], []))

            hyde_answers = self.hyde_generator.generate_hyde_answer(query)
            hyde_chunks = []
            for hyde_answer in hyde_answers:
                hyde_results = super().retrieve_chunks(hyde_answer, top_k, type="hyde")
                _, chunks = hyde_results.get("hyde", ([], []))
                hyde_chunks.extend(chunks)
            deduped_hyde_chunks = super().dedup_chunks(hyde_chunks, top_k)
            hyde_query_val = [(query,1.0)]
            merged_chunks["hyde"] = (hyde_query_val, deduped_hyde_chunks)

            time_taken = time.perf_counter() - start_time
            logger.info(f"using DIRECT + HyDE retrieval with {len(direct_query_chunks)}/25 directquery chunks and {len(deduped_hyde_chunks)}/10 HyDE chunks - took {time_taken:.2f} seconds")

            return merged_chunks


if __name__ == "__main__":
    retriever = HyDEQueryRetriever()
    test_query = "Using the 2024 disclosures for revenues by geography and long-lived assets by geographic area, what were the approximate U.S. shares of (a) total revenues and (b) total long-lived assets in 2024, and by how many percentage points was the U.S. asset share higher than the U.S. revenue share?"
    results = retriever.retrieve_chunks(test_query, top_k=10)
    for source, chunk in results.items()[1]:
        print(f"Source: {source}, Chunk ID: {chunk.id}")
