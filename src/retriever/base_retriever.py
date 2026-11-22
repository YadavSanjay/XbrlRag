
import json
from typing import List

from qdrant_client.models import ScoredPoint

from src import METADATA_COLLECTION_NAME, TEXT_COLLECTION_NAME
from src.embeddings.bge_embedding import BGEEmbeddingGenerator
from src.llm.sec10k_table_summary_reader import load_table_json
from src.logging_config import get_logger
from src.retriever.extract_year_util import extract_years_in_range
from src.retriever.retriever_util import maximal_marginal_relevance
from src.vectorstores.qdrant_vectordb_client import QdrantVectorDBClient

logger = get_logger(__name__)

# The legacy PDF corpus's year, searched when a query writes none. The flow's router prompt
# assumes the same year ("use 2024 if no year is specified", llm_query_router.py). The shared
# year reader has no default (docs/specs/no-default-year.md), so the default lives here.
LEGACY_DEFAULT_FISCAL_YEARS = [2024]


class BaseRetriever:

    def __init__(self):
        self.embedding_generator = BGEEmbeddingGenerator()
        self.vector_db_client = QdrantVectorDBClient()
        # Imported here, not at module top: the reranker pulls torch/transformers, and
        # the merge/MMR helpers on this class are unit-tested without them (CI installs
        # requirements-test.txt). BGEReranker (bge_reranker.py) is the drop-in alternative.
        from src.retriever.jina_reranker import JinaReranker

        self.reranker = JinaReranker()


    # Main retrieve method
    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, str]]:
        # Step 1: Retrieve chunks from vector DB
        chunk_search_results = self.retrieve_chunks(query, top_k=top_k)
        # Step 2: Update chunk with text from files
        chunks_with_text = self._retrieve_chunk_texts_from_files(chunk_search_results)
        # Step 3: Rerank chunks using the reranker and return top_k
        reranked_chunks = self.reranker.rerank_chunks(chunk_search_results, chunks_with_text, top_k=top_k)
        # Step 4: select diverse chunks via MMR
        #diverse_chunks = self._select_diverse_chunks_mmr(chunk_search_results, reranked_chunks)
        diverse_chunks = reranked_chunks
        # Step 5: Update chunks with table json info if applicable
        chunks_to_return = self._update_for_table_in_chunks(diverse_chunks, chunks_with_text)
        self._log_chunks_sent_in_llm_context(chunks_to_return, query)

        return chunks_to_return[:top_k]


    def retrieve_chunks(self, query: str, top_k: int = 10, type="original_query") -> dict[str, tuple[list[tuple[str,float]],list[ScoredPoint]]]:
        years_in_query: list[int] = extract_years_in_range(query)
        if not years_in_query:
            # An empty year filter would match no chunk (MatchAny(any=[])).
            years_in_query = list(LEGACY_DEFAULT_FISCAL_YEARS)
            logger.info(f"no year in query; using legacy default years {years_in_query}")
        if len(years_in_query) > 1:
            logger.info(f"\n\n @@@@@@@@@@@ \n\n: more than one year found:{years_in_query} in query: {query}\n\n @@@@@@@@@")
        logger.info(f"extracted years from query: {years_in_query}")
        # TODO - fix data load to have data in fiscal years too
        years_in_db = [str(year) for year in years_in_query]

        # Step 1: Generate embedding for the query
        text_d_s_embedding = self.embedding_generator.embed_texts([("query", query)])["query"]

        # Step 2: Perform vector search in Qdrant
        text_search_results = self.vector_db_client.hybrid_query_text(
            collection_name=TEXT_COLLECTION_NAME,
            vector_d=text_d_s_embedding[0],
            vector_s=text_d_s_embedding[1],
            limit=top_k,
            fiscal_years_in_query=years_in_db
        )
        logger.info(f"{type}_text search returned {len(text_search_results.points)} using years: {years_in_db} results for query: {query}")

        ## TODO: Re-enable once metadata is properly ingested

        # metadata_d_embedding = self.embedding_generator.dense_embed_texts([("query", query)])["query"]
        # metadata_search_results = self.vector_db_client.query_metadata(
        #     collection_name=METADATA_COLLECTION_NAME,
        #     query=metadata_d_embedding.values,
        # )
        # logger.info(f"{type}_metadata search returned {len(metadata_search_results.points)} results for query: {query}")

        search_results = {}
        query_weightage = [(query, 1.0)]
        search_results[type] = (query_weightage, text_search_results.points)

        # for point in metadata_search_results.points:
        #     search_results.append((type + "_metadata", point))

        # TODO: Merge and deduplicate results from both searches. Use RRF or other method to combine scores.

        return search_results


    # Used for debug from UI/CLI
    def retrieve_chunks_with_text(self, query: str, top_k: int = 10) -> dict[str, tuple[str, bool, str]]:
        # Step 1: Retrieve chunks from vector DB
        chunk_search_results = self.retrieve_chunks(query, top_k=top_k)
        # Step 2: Update chunk with text from files
        chunks_with_text = self._retrieve_chunk_texts_from_files(chunk_search_results)
        return chunks_with_text


    def merge_search_results(self, search_results: list[tuple[str, ScoredPoint]], top_k: int=10) -> list[tuple[str, ScoredPoint]]:
        if not search_results:
            return []

        rank_by_source: dict[str, int] = {}
        fused_scores: dict[str, float] = {}
        best_points: dict[str, tuple[str, ScoredPoint, int | None]] = {}

        rrf_constant = 60  # standard RRF dampening constant

        for source, point in search_results:
            rank_by_source[source] = rank_by_source.get(source, 0) + 1
            rank = rank_by_source[source]
            fused_scores[point.id] = fused_scores.get(point.id, 0.0) + 1.0 / (rrf_constant + rank)

            raw_year = point.payload.get("year") if point.payload else None
            try:
                year_value = int(raw_year)
            except (TypeError, ValueError):
                year_value = None

            existing = best_points.get(point.id)
            if existing is None:
                best_points[point.id] = (source, point, year_value)
            else:
                _, existing_point, existing_year = existing
                existing_year_value = existing_year if existing_year is not None else float("-inf")
                current_year_value = year_value if year_value is not None else float("-inf")
                if (current_year_value > existing_year_value) or (
                    current_year_value == existing_year_value and point.score > existing_point.score
                ):
                    best_points[point.id] = (source, point, year_value)

        merged_rankings = sorted(
            [
                (source, point, year_value, fused_scores.get(point_id, 0.0))
                for point_id, (source, point, year_value) in best_points.items()
            ],
            key=lambda item: (
                item[2] if item[2] is not None else float("-inf"),  # prefer latest year
                item[3],  # fused RRF score
                item[1].score,  # original score
            ),
            reverse=True,
        )

        return [(source, point) for source, point, _, _ in merged_rankings[:top_k]]


    def dedup_chunks(self, all_chunks: list[ScoredPoint], limit: int= None) -> list[ScoredPoint]:
        if not limit:
            limit = len(all_chunks)
        limit = int(limit)
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


    def _retrieve_chunk_texts_from_files(self, search_results: dict[str, tuple[list[tuple[str,float]],list[ScoredPoint]]]) -> dict[str, tuple[str, bool, str]]:
        chunk_file_id_map: dict[str, set[str]] = {}
        for type, search_result in search_results.items():
            _, result_points = search_result
            for result_point in result_points:
                chunk_file_path = result_point.payload.get("chunk_file_path")
                logger.debug(f"{type}, id: {result_point.id}, score={result_point.score}, path: {chunk_file_path}")
                if chunk_file_path:
                    if chunk_file_path not in chunk_file_id_map:
                        chunk_file_id_map[chunk_file_path] = set()
                    chunk_file_id_map[chunk_file_path].add(result_point.id)

        for file_path, chunk_ids in chunk_file_id_map.items():
            logger.debug(f"file: {file_path} has {len(chunk_ids)} candidate chunks")

        chunk_texts: dict[str, tuple[str, bool, str]] = {}
        for chunk_file_path, chunk_ids in chunk_file_id_map.items():
            try:
                with open(chunk_file_path, encoding='utf-8') as f:
                    chunks_data = json.load(f)
                    for chunk in chunks_data:
                        chunk_id = chunk['id']
                        if chunk_id in chunk_ids:
                            chunk_text = chunk['text'] + "_" + chunk_id
                            chunk_metadata = chunk['metadata']
                            if chunk_metadata.get('is_table', False):
                                table_json_path = chunk_metadata.get('table_json', '{}')
                                try:
                                    table_json_data = load_table_json(table_json_path)
                                    table_json_str = json.dumps(table_json_data)
                                    chunk_texts[chunk_id] = (chunk_text, True, table_json_str)
                                except json.JSONDecodeError as e:
                                    logger.error(f"error decoding table JSON for chunk {chunk_id}: {e} \n\n setting text to original chunk text.")
                                    chunk_texts[chunk_id] = (chunk_text, False, "")
                            else:
                                chunk_texts[chunk_id] = (chunk_text, False, "")
            except Exception as e:
                logger.error(f"error reading chunk file {chunk_file_path}: {e}")
        logger.info(f"retrieved {len(chunk_texts)} chunk texts for reranking from {len(chunk_file_id_map.values())} chunks in {len(chunk_file_id_map)} files")

        return chunk_texts


    def _update_for_table_in_chunks(self, chunks: list[str], chunk_texts: dict[str, tuple[str, bool, str]]) -> list[tuple[str, str]]:
        chunk_ids = [chunk.split("_")[-1] for chunk in chunks]
        final_chunks = []
        for chunk_id in chunk_ids:
            text, is_table, table_json = chunk_texts[chunk_id]
            if is_table:
                text = "<sec10k_table_in_json>" + table_json + "</sec10k_table_in_json>"
                final_chunks.append((chunk_id, text))
            else:
                text = text.split("_")[0]  # remove appended chunk_id
                final_chunks.append((chunk_id, text))
        return final_chunks


    def _select_diverse_chunks_mmr(self, search_results: dict[str, tuple[list[tuple[str,float]],list[ScoredPoint]]],
                                    reranked_chunks:list[str]) -> list[str]:
        original_query_list, _ = search_results.get("original_query", ([], []))
        original_query, _ = original_query_list[0]
        candidate_id_to_chunk: dict[str, str] = {}
        for chunk in reranked_chunks:
            chunk_id = chunk.split("_")[-1]
            candidate_id_to_chunk[chunk_id] = chunk
        original_query_embedding = self.embedding_generator.dense_embed_texts([("query", original_query)])["query"]
        candidate_embeddings = self.embedding_generator.dense_embed_texts(
            [(chunk_id, candidate_id_to_chunk[chunk_id]) for chunk_id in candidate_id_to_chunk.keys()]
        )
        candidate_tensors = {chunk_id: embedding.values for chunk_id, embedding in candidate_embeddings.items()}
        selected_chunk_ids = maximal_marginal_relevance(query_embedding=original_query_embedding.values,
                                                        candidate_embeddings=candidate_tensors)
        selected_chunks = [candidate_id_to_chunk[chunk_id] for chunk_id in selected_chunk_ids]

        chunk_ids_removed = set(candidate_id_to_chunk.keys()) - set(selected_chunk_ids)
        logger.info(f"\n\n MMR selected {len(selected_chunks)} diverse chunks, removed {len(chunk_ids_removed)} redundant chunks: {chunk_ids_removed}\n\n")
        return selected_chunks


    def _apply_chunk_expansion(self, candidate_chunk_ids: set[str], confidence: float) -> set[str]:
        # lookup by Id and include if confidence threshold met
        pass


    def _log_chunks_sent_in_llm_context(self, chunk: list[tuple[str,str]], user_query: str) -> None:
        logger.info(f"\n\n ---- chunks post the re-ranker stage ---- \n\n{user_query}\n\n")
        for idx, (chunk_id, text) in enumerate(chunk, start=1):
            logger.info(f"{idx}.  {chunk_id} :: {text}")



if __name__ == "__main__":
    retriever = BaseRetriever()
    query_1 = "What are the risk factors mentioned by Google?"
    results_1  = retriever.retrieve_chunks(query_1, top_k=10)
    for chunk_id, text in results_1:
        print(f"Chunk ID: {chunk_id}\nText: {text}\n")

    # query_2 = "Using the 2024 disclosures for revenues by geography and long-lived assets by geographic area, what were the approximate U.S. shares of (a) total revenues and (b) total long-lived assets in 2024, and by how many percentage points was the U.S. asset share higher than the U.S. revenue share?"
    # results_2  = retriever.retrieve_chunks(query_2, top_k=10)
    # for chunk_id, text in results_2:
    #     print(f"Chunk ID: {chunk_id}\nText: {text}\n")
