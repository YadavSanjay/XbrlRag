
from enum import Enum

from src.retriever.base_retriever import BaseRetriever
from src.retriever.hyde_retriever import HyDEQueryRetriever
from src.retriever.llm_rag_query import RagResponse, RAGResultGenerator
from src.retriever.subquery_retriever import SubQueryRetriever


class RetrievalType(Enum):
    BASIC = "basic"
    SUBQUERY = "subquery"
    HYDE = "hyde"


class RetrievalPipeline:

    def __init__(self):
        self.llm_answer_generator = RAGResultGenerator()

    def generate(self, query: str, retrieval_type: RetrievalType, top_k: int = 10) -> RagResponse:
        retriever = self._get_retriever(retrieval_type)
        retrieved_chunks = retriever.retrieve(query, top_k)
        rag_response = self.llm_answer_generator.generate_answer(query, retrieved_chunks)
        return rag_response

    def generate_batch(self, queries: list[str], retrieval_type: RetrievalType, top_k: int = 10) -> list[RagResponse]:
        results = []
        retriever = self._get_retriever(retrieval_type)
        for query in queries:
            retrieved_chunks = retriever.retrieve(query, top_k)
            rag_response = self.llm_answer_generator.generate_answer(query, retrieved_chunks)
            results.append(rag_response)
        return results


    def _get_retriever(self, retrieval_type: RetrievalType):
        if retrieval_type == RetrievalType.BASIC:
            return BaseRetriever()
        elif retrieval_type == RetrievalType.SUBQUERY:
            return SubQueryRetriever()
        elif retrieval_type == RetrievalType.HYDE:
            return HyDEQueryRetriever()
        else:
            raise ValueError(f"Unsupported retrieval type: {retrieval_type}")


if __name__ == "__main__":
    pipeline = RetrievalPipeline()
    #test_query = "Across FY2021, FY2022, and FY2023, in which year were Alphabet’s Class A & C share repurchases the largest as a percentage of net cash provided by operating activities? Show the percentages for all three years and identify the highest."
    #test_query = "For FY2023, what percentage of Alphabet’s total severance and office space exit charges was recorded in Research & Development (R&D) expense? Provide the R&D dollar amount and the percentage of total charges."
    test_query = "If Alphabet had held FY2023 R&D expense at the same percentage of revenues as FY2022 (14%), what would FY2023 R&D expense have been (in $ millions), and how much higher was actual FY2023 R&D than this level?"
    # retrieval_types = [RetrievalType.BASIC, RetrievalType.SUBQUERY, RetrievalType.HYDE]
    retrieval_types = [RetrievalType.HYDE]
    for retrieval_type in retrieval_types:
        response = pipeline.generate(test_query, retrieval_type, top_k=10)
        print(f"{retrieval_type.name} - answer summary: {response.answer_summary}")
        for detail in response.answer_details:
            print(f"detail: {detail}")
            print(f"==========================")

