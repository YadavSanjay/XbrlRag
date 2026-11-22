import json
from pathlib import Path
from typing import List

from src import BASE_DATA_DIR, METADATA_COLLECTION_NAME, TEXT_COLLECTION_NAME
from src.chunking.chunk import Chunk
from src.embeddings.bge_embedding import BGEEmbeddingGenerator
from src.logging_config import get_logger
from src.pipelines.sec10k import load_config
from src.vectorstores.qdrant_vectordb_client import QdrantVectorDBClient

logger = get_logger(__name__)


def process_pipeline():
    years = ["2024", "2023", "2022"]
    chunk_style = "base_chunked"
    embedding_generator = BGEEmbeddingGenerator()

    vector_db_loader = QdrantVectorDBClient()
    vector_db_loader.create_collection(collection_name=TEXT_COLLECTION_NAME, include_sparse=True)
    vector_db_loader.create_collection(collection_name=METADATA_COLLECTION_NAME)
    logger.info(f"created qdrant collections: {TEXT_COLLECTION_NAME} and {METADATA_COLLECTION_NAME}")

    vector_db_loader.prepare_for_ingestion(collection_name=TEXT_COLLECTION_NAME)
    vector_db_loader.prepare_for_ingestion(collection_name=METADATA_COLLECTION_NAME)

    for year in years:
        filings_to_process = load_config('filings')
        #folders_to_process = filings_to_process[year]
        folders_to_process = [f"{year}/{year}-10K-COMMUNICATIONSERVICES-GOOGL"]
        for folder in folders_to_process:
            chunk_file_data_list = _read_generated_chunks(folder, chunk_style)
            for chunk_file_path, chunks_per_file in chunk_file_data_list:
                logger.info(f"processing load to vector db for folder: {folder} and file: {chunk_file_path.stem} with {len(chunks_per_file)} chunks")
                text_embeddings, metadata_embeddings, payload = _prepare_chunks_for_vectordb(str(chunk_file_path), chunks_per_file, embedding_generator)
                vector_db_loader.upload_collection(collection_name=TEXT_COLLECTION_NAME,
                                                vector_data=text_embeddings,
                                                payload_data=payload)
                logger.info(f"text embedding loading completed for folder: {folder} and file: {chunk_file_path.stem}")
                vector_db_loader.upload_collection(collection_name=METADATA_COLLECTION_NAME,
                                            vector_data=metadata_embeddings,
                                                    payload_data=payload)
                logger.info(f"metadata embedding loading completed for folder: {folder} and file: {chunk_file_path.stem}")
    vector_db_loader.update_post_ingestion(collection_name=TEXT_COLLECTION_NAME)
    vector_db_loader.update_post_ingestion(collection_name=METADATA_COLLECTION_NAME)


def _read_generated_chunks(folder: str, chunk_style: str) -> list[tuple[Path,list[Chunk]]]:
    chunks_files = BASE_DATA_DIR / folder / f"chunks/{chunk_style}"

    if not chunks_files.exists():
        logger.warning(f"chunks directory not found: {chunks_files}")
        return []

    json_files = list(chunks_files.glob("*.json"))
    chunk_file_data_list = []
    for json_file in json_files:
        try:
            chunks_per_file = []
            with open(json_file, encoding='utf-8') as f:
                file_name = json_file.stem
                chunk_list = json.load(f)
                for chunk_data in chunk_list:
                    chunk = Chunk(**chunk_data)
                    chunks_per_file.append(chunk)
            chunk_file_data_list.append( (json_file, chunks_per_file) )
        except Exception as e:
            logger.error(f"error reading {json_file}: {e}")

    return chunk_file_data_list


def _prepare_chunks_for_vectordb(chunk_file_path: str, chunks: list[Chunk], embedding_generator: BGEEmbeddingGenerator):
    text_embedding_inputs: list[tuple[str,str]] = []
    metadata_embedding_inputs: list[tuple[str,str]] = []
    payload = {}

    for chunk in chunks:
        chunk_id = chunk.id
        meta = chunk.metadata
        text_embedding_input = meta['company'] + " " + meta['year'] + " " + chunk.text
        text_embedding_inputs.append((chunk_id, text_embedding_input))

        metadata_embedding_input = meta['company'] + " " + meta['year'] + " " + meta['section'] + " " + meta['subsection']
        metadata_embedding_inputs.append((chunk_id, metadata_embedding_input))

        payload[chunk_id] = (chunk_file_path, meta)

    text_embeddings = embedding_generator.embed_texts(text_embedding_inputs)
    metadata_embeddings = embedding_generator.dense_embed_texts(metadata_embedding_inputs)
    return text_embeddings, metadata_embeddings, payload


if __name__ == "__main__":
    process_pipeline()
