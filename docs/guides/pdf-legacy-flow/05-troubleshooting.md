# PDF Legacy Flow — 5. Troubleshooting

Common operational issues, grouped by component. For RAG/Qdrant issues shared with
the XBRL flow, see also [xbrl-flow/08-troubleshooting.md](../xbrl-flow/08-troubleshooting.md).

## Qdrant (vector DB)

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ConnectionRefusedError` / `[Errno 10061]` during load or retrieval | Qdrant not running, or not where `QDRANT_HOST`/`QDRANT_PORT` point (default `localhost:6333`) | Start Qdrant on a named volume ([setup §1.3](./01-setup-and-dependencies.md)), or set `QDRANT_HOST`/`QDRANT_PORT`. |
| `Collection 'sec10k_text' doesn't exist` at query time | Load step never ran (or ran for a different folder) | Run `load_to_vectordb_pipeline`; verify with `curl http://localhost:6333/collections`. |
| Query returns nothing | (a) wrong `year` filter; (b) empty collection | With no year in the query the retriever searches **2024** only (`LEGACY_DEFAULT_FISCAL_YEARS`): add the year, or check `points_count`. |
| Slow bulk load | HNSW indexing during upload | Expected; the pipeline sets `m=0` then restores `m=16` after. Don't interrupt mid-load. |
| Duplicated points after re-run | Point id = chunk_id (md5 of text) | Identical chunks upsert in place; changed text creates new ids. Recreate the collection for a clean reload. |

## Empty / wrong results

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| "Processed GOOGL but I wanted NVDA" | `chunking_pipeline.py:~24` and `load_to_vectordb_pipeline.py:~31` **hardcode GOOGL** | Edit those lines to your folder. |
| Only 2022 page summaries generated | `page_llm_summary_pipeline.py:~14` hardcodes `years=["2022"]` | Edit the `years` list. |
| Table summaries missing company name / wrong company | `ticker_to_company` key mismatch (folder uses `NVDA`, map key is `NVIDIA`) | Align the config map key with the folder suffix. |
| Retrieved chunk text is empty | `chunk_file_path` in the payload no longer resolves | The retriever reads chunk text from disk; don't move/delete `data/pagebreakdown/.../chunks/`. |

## Preprocessing / VLM

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `unstructured` partition fails on `hi_res` | Poppler/Tesseract missing, or yolox model not downloaded | Install Poppler + Tesseract, put on PATH; first run downloads yolox. |
| No tables detected | DETR threshold too high, or page has no real table | Lower `threshold` (default 0.8) in `extract_and_save_tables_from_image`. |
| Table summary step aborts a folder | 5 consecutive VLM errors | Check `OPENAI_API_KEY`, rate limits, and that crops + head/footer files exist. |
| Pages mis-assigned to sections | TOC detection / logical→physical page delta wrong | Inspect `sec10k_toc.detect_toc_page` output; the TOC must be in the first ~10 pages. |

## LLM / OpenAI

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| 401 / authentication error | `OPENAI_API_KEY` unset or invalid | Set it in `.env`. |
| 429 rate limit | Bursty batch summarization | The LLM layer has a `RateLimiter`; lower batch concurrency, or set RPM/TPM limits (see [`src/llm/rate_limiter.py`](../../../src/llm/rate_limiter.py)). |
| Answer JSON parse error | Model returned non-JSON | `RAGResultGenerator` expects strict JSON; the prompt enforces it — retry, or check for a model/version change. |
| Router always returns DIRECT | Router parse failure (designed fallback) | Inspect logs; verify the router prompt + model are reachable. |

## Models / first-run downloads

- First run downloads **BGE-M3**, **Jina reranker v3**, and **DETR layout** models
  to `HF_HOME` (`<hf-cache>` by default). Expect a slow first run
  and several GB of disk. Set `HF_HOME` to a drive with space.
- The Jina reranker loads with `trust_remote_code=True` (already configured).

## Path / install issues

- Default paths hang off `PROJECT_ROOT`, derived from the package location in
  [`src/__init__.py`](../../../src/__init__.py) (override with `XBRLRAG_PROJECT_ROOT`). To keep
  data elsewhere, set `XBRLRAG_BASE_DATA_DIR`, `XBRLRAG_10K_STORAGE_DATA_DIR` or
  `XBRLRAG_BASE_CONFIGS_DIR`.

## Logs

Logging is configured by [`configs/logging.yaml`](../../../configs/logging.yaml)
(console + rotating file). Raise verbosity with `LOG_LEVEL=DEBUG` in `.env`.
