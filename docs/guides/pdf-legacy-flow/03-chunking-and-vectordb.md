# PDF Legacy Flow — 3. Chunking & Vector DB Load

With page/table summaries in place, you produce chunks and load them into Qdrant.

## 3.1 Chunking

Pipeline: [`src/pipelines/sec10k/chunking_pipeline.py`](../../../src/pipelines/sec10k/chunking_pipeline.py)
Engine: [`src/chunking/base_chunking.py`](../../../src/chunking/base_chunking.py)

```bash
python -m src.pipelines.sec10k.chunking_pipeline
```

What it does, per folder:

1. **TOC-based pass** — `extract_subsections_per_toc(year, folder)` →
   `generate_and_persist_chunks(..., isTocBased=True)`.
2. **Outside-TOC pass** — for each non-TOC page, reads the section label from
   `pages_summary/page_<p>_summary.txt` (via `Sec10kPageSummaryReader`), then
   `generate_and_persist_chunks(..., isTocBased=False)`.
3. `flush_remaining_chunks(folder)`.

Chunking behavior (`BaseChunking`):
- Title-aware: accumulates text up to `CHUNK_SIZE_IN_CHARS` (1000), splitting on
  the nearest sentence boundary, falling back to word boundary.
- **Tables become their own chunk** (`is_table=True`); the chunk stores the path
  to the table's `table_<n>_summary.txt` in `table_json`.
- Chunks are linked via `prev_chunk_id` / `next_chunk_id`.
- Persisted in batches of 25 to
  `chunks/base_chunked/page_<min>_<max>_<sha[:25]>.json`.

The on-disk chunk schema is defined by `Chunk` / `ChunkMetadata` in
[`src/chunking/chunk.py`](../../../src/chunking/chunk.py): `doctype, year, company,
section, subsection, is_table, table_json, physical_page, logical_page,
prev_chunk_id, next_chunk_id, cross_ref_chunk_ids`.

> ⚠️ **Only GOOGL is chunked by default.** Line ~24 hardcodes
> `folders_to_process = [f"{year}-10K-COMMUNICATIONSERVICES-GOOGL"]`, overriding the
> YAML. Edit this line to chunk other folders.
> `coref_chunking.py` and `crossref_chunking.py` are **empty stubs** — not used.

## 3.2 Load into Qdrant

Pipeline: [`src/pipelines/sec10k/load_to_vectordb_pipeline.py`](../../../src/pipelines/sec10k/load_to_vectordb_pipeline.py)
Client: [`src/vectorstores/qdrant_vectordb_client.py`](../../../src/vectorstores/qdrant_vectordb_client.py)

**Qdrant must be reachable first** (`QDRANT_HOST`/`QDRANT_PORT`, default `localhost:6333`).

```bash
python -m src.pipelines.sec10k.load_to_vectordb_pipeline
```

Steps it runs:
1. `BGEEmbeddingGenerator()` (dense+sparse, `BAAI/bge-m3`, dim 1024).
2. `QdrantVectorDBClient()`.
3. Create both collections: `sec10k_text` (`include_sparse=True`) and
   `sec10k_metadata` (dense only).
4. `prepare_for_ingestion` on both (sets HNSW `m=0` to speed bulk load).
5. For each chunk JSON file: embed and `upload_collection`.
6. `update_post_ingestion` on both (restores HNSW `m=16`).

Embedding inputs:
- **Text collection** dense+sparse input = `company + " " + year + " " + chunk.text`.
- **Metadata collection** dense input = `company year section subsection`.

Per-point payload (`_get_text_payload`): `doctype, year, year_datetime, company,
section, subsection, is_table, prev/next_chunk_id, chunk_file_path`.
⚠️ `table_json` is deliberately **not** stored in Qdrant — the retriever reads the
table summary from the file referenced by `chunk_file_path` to avoid DB bloat.

> ⚠️ **Hardcoded scope.** `years = ["2024","2023","2022"]` and
> `chunk_style = "base_chunked"` (lines ~16-17), and line ~31 again hardcodes the
> folder to `[f"{year}/{year}-10K-COMMUNICATIONSERVICES-GOOGL"]`. Edit to load other
> filings.

## 3.3 Collections created

| Collection | Vectors | Purpose |
|------------|---------|---------|
| `sec10k_text` | `text_dense` (1024, COSINE) + `text_sparse` | the searchable chunk text |
| `sec10k_metadata` | `text_dense` (1024) only | company/year/section metadata search (present but **disabled** in the retriever) |

HNSW `m=16, on_disk=True`. Point id = chunk_id.

## 3.4 Verify the load

```bash
# List collections + counts (Qdrant REST API)
curl http://localhost:6333/collections
curl http://localhost:6333/collections/sec10k_text
```

Expect both `sec10k_text` and `sec10k_metadata` to exist with a non-zero
`points_count` matching your chunk JSON count.

Next: [4. Retrieval & LLM answering](./04-retrieval-and-llm.md).
