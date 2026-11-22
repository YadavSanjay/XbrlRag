# PDF Legacy Flow — 4. Retrieval & LLM Answering

This is the query side: turn a natural-language question into a cited answer.

## 4.1 Run a query from the command line

Pipeline: [`src/pipelines/sec10k/retrieval_pipeline.py`](../../../src/pipelines/sec10k/retrieval_pipeline.py)

```bash
python -m src.pipelines.sec10k.retrieval_pipeline
```

The `__main__` runs a hardcoded test query with `RetrievalType.HYDE`, `top_k=10`.
Edit the query in `__main__` to test your own, or use the programmatic API (§4.4).

**Qdrant must be running and loaded; `OPENAI_API_KEY` must be set.**

## 4.2 Retrieval modes (`RetrievalType`)

| Mode | Class | Behavior |
|------|-------|----------|
| `basic` | `BaseRetriever` | embed → hybrid search → rerank → answer |
| `subquery` | `SubQueryRetriever` | router decomposes/paraphrases, no HyDE |
| `hyde` | `HyDEQueryRetriever` | router + paraphrase + **HyDE** hypothetical answers, then retrieve |

## 4.3 What happens inside a query

1. **Router** ([`llm_query_router.py`](../../../src/retriever/llm_query_router.py)) —
   `route_query` → `DIRECT` / `PARAPHRASE` / `DECOMPOSE` / `DECOMPOSE_AND_PARAPHRASE`
   (default `gpt-5-mini`; falls back to `DIRECT` on parse error).
2. **Query expansion** — paraphrase
   ([`llm_query_rewriter.py`](../../../src/retriever/llm_query_rewriter.py)) and/or
   HyDE hypothetical answers
   ([`llm_hyde_generator.py`](../../../src/retriever/llm_hyde_generator.py)).
3. **Year extraction**
   ([`extract_year_util.py`](../../../src/retriever/extract_year_util.py)) —
   `extract_years_in_range(query)` parses FY/FY'21/ranges and returns `[]` when the query
   writes no year. ⚠️ The legacy retriever then falls back to `LEGACY_DEFAULT_FISCAL_YEARS`
   (`[2024]`, in `base_retriever.py`) for the Qdrant `year` filter, so a query with no
   year only searches 2024.
4. **Hybrid search** — query embedded dense+sparse, `hybrid_query_text(...)` with
   two `Prefetch` (sparse + dense) fused via `FusionQuery(Fusion.RRF)`, filtered by
   `year`. ⚠️ The metadata-collection search path is present but **commented out /
   disabled** in [`base_retriever.py`](../../../src/retriever/base_retriever.py).
5. **Load chunk text** from the JSON files referenced in each point's
   `chunk_file_path`.
6. **Rerank** ([`jina_reranker.py`](../../../src/retriever/jina_reranker.py),
   `jinaai/jina-reranker-v3`) — ⚠️ Jina is the **active** reranker; the BGE reranker
   is present but commented out. Final score blends original-query, subquery, and
   table-relevance signals (`0.45 / 0.35 / 0.2`) plus a recency boost.
7. ⚠️ **MMR diversity is disabled** (`diverse_chunks = reranked_chunks`).
8. **Table wrapping** — table chunks are wrapped as
   `<sec10k_table_in_json>...</sec10k_table_in_json>` so the LLM recognizes them.
9. **Answer generation**
   ([`llm_rag_query.py`](../../../src/retriever/llm_rag_query.py),
   `RAGResultGenerator`, default `gpt-5-mini`) — calls OpenAI Chat Completions,
   expects strict JSON (`answer_summary`, `answer_details[]`, `unanswered_parts[]`,
   `metadata`), prompts from `Sec10KPrompts.RAG_RESULT_SYSTEM/_USER`.

Result type: `RagResponse(query, answer_summary, answer_details, retrieved_chunks)`.

## 4.4 Invoke programmatically (how another subsystem calls the legacy flow)

```python
from src.pipelines.sec10k.retrieval_pipeline import RetrievalPipeline, RetrievalType

pipeline = RetrievalPipeline()                       # builds RAGResultGenerator + retrievers
resp = pipeline.generate(
    query="What were Alphabet's total revenues in fiscal 2024?",
    retrieval_type=RetrievalType.HYDE,               # BASIC | SUBQUERY | HYDE
    top_k=10,
)
print(resp.answer_summary)
for d in resp.answer_details:
    print(d)

# Batch:
resps = pipeline.generate_batch([q1, q2], RetrievalType.BASIC, top_k=10)
```

The canonical call chain:

```
RetrievalPipeline.generate(query, RetrievalType.X, top_k)
  → {BaseRetriever | HyDEQueryRetriever | SubQueryRetriever}.retrieve(query, top_k)
  → RAGResultGenerator.generate_answer(query, chunks)
  → RagResponse
```

### Via the REST API / UI

The FastAPI BFF wraps this through
[`src/api/pipelines.py`](../../../src/api/pipelines.py) → `run_sec10k(query, mode, top_k)`,
mapping `mode` `"basic"|"hyde"|"subquery"` → `RetrievalType`. POST to
`/api/query` with `mode: "basic"` (or `hyde`/`subquery`):

```bash
curl -s http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query":"Alphabet 2024 revenue?","mode":"hyde","top_k":10}'
```

⚠️ The sec10k path returns `query_id: null` — unlike the XBRL flow it has **no
per-query trace / audit row**, so the Technical observability console shows XBRL
queries only. See [xbrl-flow/07-api-ui-observability.md](../xbrl-flow/07-api-ui-observability.md)
for running the API + UI (shared between both flows).

## 4.5 Known sharp edges (recap)

- No-year queries silently scope to 2024 (`LEGACY_DEFAULT_FISCAL_YEARS`).
- Metadata search + MMR are disabled in `base_retriever.py`.
- `SubQueryRetriever` has known unpacking bugs; **BASIC and HYDE are the
  well-trodden paths**.
- All LLM steps require `OPENAI_API_KEY` and default to `gpt-5-mini`/`gpt-5-nano`.

Next: [5. Troubleshooting](./05-troubleshooting.md).
