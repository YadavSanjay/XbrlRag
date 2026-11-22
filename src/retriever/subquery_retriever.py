
from typing import List

from qdrant_client.models import ScoredPoint

from src.logging_config import get_logger
from src.retriever.base_retriever import BaseRetriever
from src.retriever.llm_query_rewriter import QueryRewriter
from src.retriever.llm_query_router import ROUTER_DECISION, QueryRouter

logger = get_logger(__name__)


class SubQueryRetriever(BaseRetriever):

    def __init__(self):
        super().__init__()
        self.query_router = QueryRouter()
        self.query_rewriter = QueryRewriter()


    def retrieve_chunks(self, query, top_k = 10) -> dict[str, tuple[list[tuple[str,float]],list[ScoredPoint]]]:
        route_decision = self.query_router.route_query(query)
        logger.info(f"routing decision: {route_decision.name}")

        if route_decision != ROUTER_DECISION.DIRECT:
            paraphrased_query = self.query_rewriter.paraphrase_query(query)
        else:
            paraphrased_query = query

        result = {}
        if route_decision == ROUTER_DECISION.PARAPHRASE:
            # 25 chunks from paraphrased query
            result =  super().retrieve_chunks(paraphrased_query, 2.5*top_k)
        elif route_decision == ROUTER_DECISION.DECOMPOSE or route_decision == ROUTER_DECISION.DECOMPOSE_AND_PARAPHRASE:
            # 10 chunks from paraphrased query and 30 chunks from subqueries
            merged_result = {}
            _, original_query_results =  super().retrieve_chunks(paraphrased_query, top_k)
            merged_result["original_query"] = original_query_results.values()

            subqueries = self.query_rewriter.decompose_paraphrase_query(query)
            all_chunks = []
            for _, subquery in subqueries:
                _, chunks = super().retrieve_chunks(subquery, top_k, type="subquery")
                all_chunks.extend(chunks.values())
            merged_chunks = super().dedup_chunks(all_chunks, limit=3*top_k)
            logger.info(f"using DECOMPOSE retrieval with {len(subqueries)} subqueries, retrieved {len(merged_chunks)} of {len(all_chunks)} chunks")

            merged_result["subquery"] = tuple(subqueries, merged_chunks)
            result = merged_result
        else:
            # 25 chunks from original query
            logger.info("using DIRECT retrieval")
            result = super().retrieve_chunks(query, 2.5*top_k)

        return result


if __name__ == "__main__":
    retriever = SubQueryRetriever()
    test_query = "Using the 2024 disclosures for revenues by geography and long-lived assets by geographic area, what were the approximate U.S. shares of (a) total revenues and (b) total long-lived assets in 2024, and by how many percentage points was the U.S. asset share higher than the U.S. revenue share?"
    paraphrased_query, results = retriever.retrieve_chunks(test_query, top_k=10)
    for source, chunk in results:
        print(f"Source: {source}, Chunk ID: {chunk.id}")


