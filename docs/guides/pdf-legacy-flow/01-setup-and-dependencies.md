# PDF Legacy Flow — 1. Setup & Dependencies

## 1.1 Install

Use the `py312` conda environment (it has `requirements.txt`, including `FlagEmbedding` for
BGE-M3). The legacy flow also needs PDF / VLM libraries that are **commented out** in
`requirements.txt` ("Optional: Document processing"). Install what you need for ingestion:

```powershell
conda run -n py312 python -m pip install "unstructured[pdf]" pdf2image pytesseract pypdf pymupdf
conda run -n py312 python -m pip install timm torchvision   # DETR layout detection
```

> `unstructured`'s `hi_res` strategy needs **Poppler** (for `pdf2image`) and
> **Tesseract** on the PATH. On Windows install both and ensure their `bin/`
> directories are on `PATH`.

## 1.2 `.env`

The legacy flow needs only the OpenAI key (and optional HF cache location):

```ini
OPENAI_API_KEY=sk-...           # required for summaries, query rewrite, HyDE, answers
HF_HOME=<hf-cache>   # where BGE / Jina / DETR models cache
LOG_LEVEL=INFO
```

`.env` is auto-loaded by [`src/__init__.py`](../../../src/__init__.py) via
`python-dotenv`.

## 1.3 Start Qdrant

Qdrant must be running before the **load** and **retrieval** stages.

```bash
docker run -d --name qdrant -p 6333:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant:v1.18.2
```

Use a named volume, never a Windows bind mount
([why](../xbrl-flow/08-troubleshooting.md#qdrant-wont-start-corrupted-collection)).

Host/port default to `localhost:6333` but are read from `QDRANT_HOST` / `QDRANT_PORT`
(with `QDRANT_API_KEY` / `QDRANT_HTTPS`) by the shared
[`QdrantVectorDBClient`](../../../src/vectorstores/qdrant_vectordb_client.py)
constructor, so moving Qdrant is an env change — no code edit needed. (This client
is shared with the XBRL flow; see [xbrl-flow/01](../xbrl-flow/01-setup-and-dependencies.md).)

## 1.4 Directory layout & path overrides

Key constants live in [`src/__init__.py`](../../../src/__init__.py):

| Constant | Default | Env override | Meaning |
|----------|---------|--------------|---------|
| `PROJECT_ROOT` | derived from the package location | `XBRLRAG_PROJECT_ROOT` | base of all default paths |
| `TENK_DATA_STORAGE_DIR` | `data/storage/10k` | `XBRLRAG_10K_STORAGE_DATA_DIR` | **source PDFs** live at `<dir>/<year>/<folder>.pdf` |
| `BASE_DATA_DIR` | `data/pagebreakdown` | `XBRLRAG_BASE_DATA_DIR` | working dir: split pages, images, tables, chunks, summaries |
| `CONFIGS_DIR` | `configs` | `XBRLRAG_BASE_CONFIGS_DIR` | YAML config |
| `CHUNK_SIZE_IN_CHARS` | `1000` | — | chunk budget |
| `TEXT_COLLECTION_NAME` | `sec10k_text` | — | Qdrant text collection (dense+sparse) |
| `METADATA_COLLECTION_NAME` | `sec10k_metadata` | — | Qdrant metadata collection (dense only) |

The working tree a fully processed filing produces:

```
data/pagebreakdown/<year>/<folder>/
  pages/      <folder>_page_<n>.pdf          (split single pages)
  images/     <folder>_page_<n>.jpg          (rendered page images, dpi 200)
  tables/page_<p>/table_<n>.jpeg             (DETR table crops)
                  table_<n>_metadata.txt     (score, bbox)
                  table_<n>_headerfooter.txt (extracted head/foot)
                  table_<n>_summary.txt      (VLM summary)
  pages_summary/page_<p>_summary.txt         (per-page LLM summary)
  chunks/base_chunked/page_<min>_<max>_<sha>.json   (final chunks)
```

## 1.5 The corpus config — `configs/pipelines/sec10k_data_to_process.yaml`

Read by `load_config(key)` in
[`src/pipelines/sec10k/__init__.py`](../../../src/pipelines/sec10k/__init__.py). Two
top-level keys:

- **`filings`** — `year → [folder, ...]`. Folder naming convention is
  `{YEAR}-10K-{SECTOR}-{TICKER}`, e.g. `2024-10K-COMMUNICATIONSERVICES-GOOGL`.
  The active set per year is XOM, AMZN, JPM, GOOGL, NVDA (others commented out).
- **`ticker_to_company`** — `ticker → legal company name`, injected into table
  summaries so the VLM sees the real company.

⚠️ Table summarization derives the ticker as `folder.split('-')[-1]` then looks it
up in `ticker_to_company`. Folders use `NVDA` but the map key is `NVIDIA` (and the
`AMD` value has a typo) — a real lookup mismatch. If you add companies, keep the
folder suffix and the map key consistent.

## 1.6 Dependency summary

| What | Where configured | Failure symptom |
|------|------------------|-----------------|
| Qdrant up (default `:6333`) | `QDRANT_HOST` / `QDRANT_PORT` | connection refused during load/retrieval |
| `OPENAI_API_KEY` | `.env` | 401 / auth error in summary/answer steps |
| BGE-M3 model | downloaded to `HF_HOME` | slow first run while downloading |
| Jina reranker | downloaded to `HF_HOME` | slow first run; needs `trust_remote_code=True` (already set) |
| Poppler / Tesseract | system PATH | `unstructured` `hi_res` partition fails |

Next: [2. Ingestion & preprocessing](./02-ingestion-and-preprocessing.md).
