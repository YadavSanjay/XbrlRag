# html-rag-arm — the retriever, the answerer, the load and the `html_rag` arm

This completes the HTML flow of [v2 plan](../design/XbrlRag_v2_Plan.md) §5, rows *Retrieve*,
*Answer* and *Decline*, and adds the arm of §3.6. `ix_strip_html`, `detect_sections`, `parse_tables`
and the chunk and index stages ([html10k-chunks](html10k-chunks.md)) exist.

`html_rag` is the baseline XbrlRag has to beat, so it has to be a competent RAG: beating a weak one
proves nothing. Where a choice here could weaken it, Notes says so.

**Order of work:** `impl/html-rag-arm` starts after `html10k-chunks` is merged. This code calls
`chunk_filing`, `build_payload`, `embedding_input` and `Html10kQdrantVectorDBClient`, and C5, C6 and
C12 need them real.

## Requirement

### A. Configuration

1. **The retrieval numbers are configuration.** `load_html10k_config(path=None)` reads
   `configs/pipelines/html10k/rag.yaml` into a typed model. Every key is required, an unknown key
   raises `ValueError`, and no default for any of them sits in code. The committed file holds:

   | Key | Value | Use |
   |---|---|---|
   | `rrf_k` | 2 | RRF constant |
   | `dense_weight`, `sparse_weight` | 2.0, 1.0 | RRF weights |
   | `prefetch_limit` | 100 | Candidates per vector before fusion |
   | `rerank_candidates` | 50 | Fused candidates sent to the reranker; 30 until [html-rag-candidates](html-rag-candidates.md) |
   | `context_top_k` | 20 | Reranked chunks kept, per search |
   | `reranker_model` | `jinaai/jina-reranker-v3` | Sent to RagInference; part of the cache key |
   | `expand_neighbors` | true | Requirement 12 |
   | `expansion_score_threshold` | 0.5 | Requirement 12, on the jina score scale |
   | `expansion_max_chunks` | 20 | Requirement 12 |
   | `max_filer_searches` | 10 | Requirement 5 |
   | `table_max_chars` | 1200 | Passed to `chunk_filing` by the load |
   | `embed_batch_size` | 32 | Requirement 28 |
   | `rerank_cache_path` | `${XBRLRAG_HTML10K_RERANK_CACHE:-data/cache/html10k_rerank.sqlite3}` | Requirement 13 |

2. **Validation.** Each of `rrf_k`, `prefetch_limit`, `rerank_candidates`, `context_top_k`,
   `max_filer_searches`, `table_max_chars` and `embed_batch_size` is at least 1. Both weights are
   above 0. `expansion_max_chunks` is at least 0. `rerank_candidates <= prefetch_limit` and
   `context_top_k <= rerank_candidates`. A breach raises `ValueError`.
3. **The cache path.** `${VAR:-default}` expands. The attribute `rerank_cache_path` is a `Path`,
   and a relative one is under `PROJECT_ROOT`.

### B. What to search

4. **The same resolver as the long-context arms.** The arm calls `IntentRouter.route(case.question)`,
   built as `long_context_arms` builds it, and reads only `companies` and `periods`. `case.ticker`,
   `case.fy`, `case.cik`, `case.kind` and `case.universe` are never read. `QueryDecomposer` is not
   run, as in those arms.
5. **`search_scopes(result, max_filer_searches)`** is the pure rule from a router result to the
   searches.
   - The reason is `"; ".join(BranchDispatcher._unresolved_reasons(result))`, or `None` when there
     are none. It is the check `filing_targets` starts from.
   - The filers are the distinct `cik`s of companies that are not `is_ambiguous`, sorted. An
     ambiguous company is never filtered on, so nothing is guessed.
   - With at least one period mention, the years are `p.fiscal_year` of each period that is not
     `ambiguous`, plus every year written in that period's mention
     (`extract_years_in_range(mention, year_range=[2018, 2035])`, as in `filing_targets`), sorted.
     If no period resolved, there is no year filter.
   - With no period mention, each filer's years are its own `[last_seen_fy]`, or no year filter when
     that is `None`.
   - Each filer gets one scope, `{"ciks": [cik], "fiscal_years": years}`, sorted by `cik`, so one
     filer cannot crowd out another in a comparison.
   - With no filer: one scope, `{"ciks": [], "fiscal_years": years}`. An empty list means no filter
     on that field, and with both empty the search has no filter at all.
   - With more than `max_filer_searches` filers: one scope with every filer and the sorted union of
     the years.
6. **When the resolver cannot choose, the arm searches on what did resolve.** `filing_targets`'
   reasoning is that XbrlRag never guesses a filer or year and does not stop: it takes a chunk search
   filtered only by what resolved. A whole-filing arm has no search, so it refuses. This arm has one,
   so it does what XbrlRag does. It never refuses for that reason alone, and it records the reason
   in `selection["degraded"]`.

### C. Retrieval

7. **The question is embedded once**, through `embed_texts([("query", question)])`, however many
   scopes there are.
8. **One hybrid search per scope,** server-side, on collection `html10k_text`:

   ```python
   client.query_points(
       collection_name="html10k_text",
       prefetch=[
           Prefetch(query=dense.values, using="text_dense", limit=prefetch_limit, filter=f),
           Prefetch(query=SparseVector(...), using="text_sparse", limit=prefetch_limit, filter=f),
       ],
       query=RrfQuery(rrf=Rrf(k=rrf_k, weights=[dense_weight, sparse_weight])),
       limit=2 * prefetch_limit,
       with_payload=True,
   )
   ```

   The returned points sort by score, highest first, ties by `chunk_id` ascending; the first
   `rerank_candidates` are the candidates ([html-rag-fusion-order](html-rag-fusion-order.md); until
   then, `limit=rerank_candidates` in Qdrant's order).

   `f` is `Filter(must=[...])` with `FieldCondition(key="cik", match=MatchAny(any=ciks))` when there
   are `ciks`, and `FieldCondition(key="fiscal_year", match=MatchAny(any=fiscal_years))` when there
   are years. With neither, `f` is `None`. The weights follow the prefetch order.
9. **Each scope's candidates are reranked apart,** so a comparison keeps both filers. The reranker
   gets `embedding_input(payload)` for each candidate, in candidate order, so it can see the filer,
   year and section. A scope with no candidates makes no rerank call.
10. **Ranking.** Candidates sort by score, highest first, and ties keep candidate order. The top
    `context_top_k` of each scope are kept. Scopes follow in their given order.
11. **A failure is loud.** An exception from the embedder, Qdrant or the reranker, including the
    expansion fetch, propagates from `retrieve`. Unexpanded chunks are not silently returned.
12. **Neighbour expansion** (plan §5.3). With `expand_neighbors` on, walk the kept chunks in order.
    For each whose rerank score is at least `expansion_score_threshold`, in this order: its
    `prev_chunk_id`, its `next_chunk_id`, and, for a table chunk with `parts > 1`, each sibling part
    from 0 to `parts - 1`. A table's parts share a byte range, so a sibling's id is
    [html10k-chunks](html10k-chunks.md) requirement 12's formula on the chunk's `accession_no`,
    `html_char_start`, `html_char_end` and that part. Skip ids that are `None`, already kept or already
    chosen, and stop at `expansion_max_chunks`. Then make one `client.retrieve(collection_name=
    "html10k_text", ids=ids, with_payload=True)` call, or none with no ids. Chunks found are appended
    in the order asked for, with `is_expansion=True` and `rerank_score=None`. An id it doesn't find
    is left out.

### D. The rerank cache

13. **`CachedReranker(inner, model=..., cache_path=...)`** keeps scores in a file that survives the
    process. The key is the model, the query and the exact ordered list of documents. So a changed
    chunk, a new load or another reranker is a miss, and no index version needs tracking. The order
    is part of the key because jina-reranker-v3 is listwise.
14. **What it keeps.** `rerank` returns scores in input order, `cache_hit`, and `elapsed_ms`, the
    time the original rerank took, unchanged on a hit. So a cached run can still report what a rerank
    costs. An empty list makes no call. A wrong number of scores or a non-finite score raises
    `ValueError` and is not stored. An exception from `inner` propagates and is not stored.
15. **One rerank at a time.** Two misses never run `inner` together: the reranker is CPU-bound and
    `html_rag` reranks every case, not only narrative ones. Two concurrent calls with the same key
    make one call to `inner`.

### E. The answerer

16. **Excerpts are data.** The arm makes exactly one answer call per case, on stage `html_rag`,
    prompt version `v1`. The system prompt takes no retrieved text. The user message holds the
    question and `json.dumps(excerpts, ensure_ascii=False)`, the first `[` in that message starting
    the list. Each excerpt is exactly `{"chunk_id", "ticker", "company_name", "fiscal_year",
    "form_type", "section", "kind", "text"}`, where `section` is `section_label` and `kind` is
    `"table"` or `"prose"`, in chunk order. No other payload field reaches the model.
17. **The reply** is one JSON object:

    ```json
    {"inputs": [{"id": "a", "chunk_id": "...", "row": "Net sales", "column": "2024",
                 "printed": "$1,200", "scale": 1000000}],
     "value_expr": "a", "unit": "USD",
     "rows": [{"entity": "Data Center", "expr": "a"}],
     "passages": [{"chunk_id": "...", "quote": "..."}],
     "refused": false, "hedged": false, "text": "..."}
    ```

    Row and column labels and `printed` are copied as the filing prints them. `row` and `column` are
    `null` for a number in prose. A `value` or `numeric` key in the reply is never read.
18. **The model doesn't calculate.** Every number in `numeric_values` and `rows` is
    `XbrlCalculatorTool.evaluate(expr, bindings)`, where `bindings` maps each resolved input's `id`
    to `printed_value(printed, scale)`. A lookup is the expression `a`. `numeric_values` is the value,
    then each row's, as in `parse_structured_answer`. `rows` holds `{"entity", "value"}`.
19. **`printed_value(printed, scale)`** is the one place scale and sign are applied. `scale` must be
    the number 1, 1000, 1000000 or 1000000000 (a bool is not); otherwise `None`. A footnote marker
    such as `[2]` is ignored. Parentheses or a leading minus make it negative. `$`, `US$`, `,` and `%`
    are ignored. A scale word printed with the number (`$1.2 billion`) applies and `scale` is then
    ignored. A nil (`—`, `–`, `-`, empty) or a text with no number is `None`, never 0.
20. **`match_cell(text, row, column, printed)`** says how a citation lines up.
    - Labels compare after NFKC, casefold, whitespace collapsed, `\|` unescaped, a `[x]` marker and a
      trailing `:` dropped. Two labels match when they are equal or one contains the other.
    - The table is `text`'s lines starting with `|`. The first is the header, and the `| --- |` line
      is not a row. Cells split on an unescaped `|`.
    - `"exact"`: `row` matches a row's first cell, `column` matches a header cell after the first, and
      the cell there has the same `printed_value(_, 1)` as `printed`.
    - With `row` or `column` `None`: `"exact"` when the number of `printed` appears in `text`.
    - `"printed"`: otherwise, the number appears in `text`. Numbers compare as whole tokens, without
      sign, so `1,234` is not in `11,234`.
    - `"none"`: otherwise, and when `printed` has no number.
21. **Resolving inputs.** An input resolves when its `id` is an identifier not used before, its
    `chunk_id` was given in this call, `printed_value` is not `None`, and `match_cell` is not `"none"`.
    `"printed"` means the labels don't line up: the input is used, the answer is `hedged`, and it is
    listed in `selection["unverified"]`.
22. **Citations.** `chunk_ids` holds the `chunk_id`s of resolved inputs, then of passages given in
    this call, in that order without repeats. `fact_ids` is `[]`. A passage whose `chunk_id` was not
    given is dropped. A passage whose quote is not in its chunk, after the normalisation of
    requirement 20, stays cited, and the answer is `hedged` and it is listed in
    `selection["unverified"]`. A filing quoted loosely is not a reason to refuse.
23. **Not meaningful.** When `not_meaningful_reason(expr, bindings)` gives a reason for `value_expr`,
    no value is computed, the reason is appended to `text`, and the answer is `hedged`, which is what
    the grader's n/m path reads. A row in that state is dropped and the answer is `hedged`.
24. **Decline.** Each of these gives `refused=True`, `error=None`, `numeric_values == []`, `rows ==
    []`, `chunk_ids == []` and non-empty `text`, with this `selection["status"]`:

    | Status | When |
    |---|---|
    | `no_candidates` | Every search came back empty; no answer call is made |
    | `model_declined` | The reply says `refused: true` |
    | `unparseable` | The reply holds no JSON object; its numbers are not read |
    | `unsupported` | `value_expr` raises `CalculatorError` (an unresolved input, a bad expression, division by zero); or there is no value, no row and no n/m, and no resolved input or kept passage |

    A row whose `expr` raises `CalculatorError` is dropped and the answer is `hedged`. If every row
    is dropped and there is no value, the status is `unsupported`. Otherwise the status is `answered`.

### F. The arm

25. **Same model tier as XbrlRag.** `HtmlRagSystem.model` is `resolve_stage("synthesizer").model`,
    read when the arm is built. The stage needs no `XBRL_STAGE_TIERS` entry, as
    `long_context_multi` needs none. `build_system("html_rag")` builds it offline.
    `"html_rag"` joins `system_names()`, after the existing names. `build_system("html_rag",
    model=m)` raises `ValueError`, like every arm that is not a long-context one.
26. **The record.** Every answer that reached the resolver carries `selection`, JSON-safe:
    - `status`, `reason` (why, or `None` when answered);
    - `degraded`: requirement 6's reason, or `None`;
    - `scopes`: as searched;
    - `retrieved`: per chunk in chunk order, `{"chunk_id", "accession_no", "ticker", "fiscal_year",
      "section_key", "kind", "html_char_start", "html_char_end", "rerank_score", "is_expansion"}`;
    - `rerank_calls`, `rerank_cache_hits`, `rerank_ms` (the sum of `elapsed_ms`);
    - `calculations`: `[{"expr", "bindings", "value"}]`, the value's first, then the rows';
    - `unverified`: `[{"chunk_id", "detail"}]`;
    - `retrieval_config`: every setting of requirement 1 except `rerank_cache_path`, so a run says
      which numbers produced it.
27. **Accounting and failures** work as for the other arms, through `answer_in_trace`: `query_id`,
    `latency_ms`, and `cost_usd` covering the router and the answer calls. An exception from the
    router, embedder, Qdrant, reranker or provider becomes `error`, and the `selection` so far is
    kept. `RunHalt` propagates from both calls. There are no exclusions: every case stays in the
    denominator. The default embedder is `RemoteEmbedder`, the default reranker `RemoteReranker(model=
    reranker_model)`, and the Qdrant client comes from the `QDRANT_*` env, all built on first use.

### G. The load

28. **`Html10kLoadPipeline.load_filing(filing, source_key)`** reads the primary document from the
    store, then `ix_strip_html`, `chunk_filing(stripped, accession_no, table_max_chars=config.
    table_max_chars)` and `build_payload`. If `client.count(...)` for that `accession_no` equals the
    number of payloads and is above 0, it returns `"skipped"` and embeds nothing, so a load that
    stops can restart. Otherwise it embeds `embedding_input(payload)` through `embed_texts`, in
    batches of at most `embed_batch_size`, then deletes that accession's points if it had any, then
    calls `upload_chunks`, and returns `"loaded"`. Nothing is deleted until the embeddings exist.
29. **`run(filings)`** calls `ensure_collection` once, then loads each entry. An exception on one
    filing, or a `source_key` of `None`, is listed in `failed`, and the rest go on.
30. **`list_annual_filings(conn, sec_edgar_root, tickers=None, fiscal_years=None)`** reads
    `filing_registry`, `entity_master` and `accession`, on the same conditions as
    `PostgresFilingResolver`: `fiscal_period = 'FY'`, `superseded_by IS NULL`, `form_type = ANY(%s)`
    with `ANNUAL_FORMS` as its one parameter. Each row's columns are `cik, ticker, company_name,
    accession_no, fiscal_year, fiscal_period, form_type, period_end, entry_url`. `filing` is the first
    eight, with `fiscal_year` an `int` and `period_end` a string; `fiscal_year` is the registry's
    label, never derived. `source_key` is `entry_url` relative to the root (`/` separators, compared
    as `canonical_key_from_entry_url` compares), or `None` when it isn't under the root.

### H. Leakage (plan §5.2)

31. **At query time the arm reads two database tables, `entity_master` and `fiscal_calendar`,** both
    through `EntityExtractor`, the resolver the long-context arms share. It reads no filing store and
    has no `FilingResolver`. It queries only the Qdrant collection `html10k_text`.
32. **The load reads `filing_registry`, `entity_master` and `accession`,** for filing metadata and the
    document's path. No fact, concept, context or chunk-link table.
33. **The new modules** (`src/retriever/html10k/`, `src/pipelines/html10k/`,
    `src/eval/systems/html_rag.py`) import no fact-store code: from `src.retriever.xbrl` only
    `intent_router`, `entity_extractor`, `branch_dispatcher` and `xbrl_calculator_tool`; nothing
    from `src.pipelines.xbrl`, `src.eval.systems.xbrlrag_full`, or any module whose own name starts
    with `xbrl_` elsewhere under `src`; from `src.ingest.sec_edgar` only `storage`.
    `src.eval.systems.long_context` and `long_context_arms` are allowed; they import no fact store.

**In scope:** requirements 1-33, and the prompt pair of stage `html_rag`. **Out of scope:** mapping
text-key spans onto chunks, and scoring retrieval recall (see Notes); running the load or any paid
run; `frontier_sec_tools`; the fallback role (plan §5.1, item 3); any change to the XBRL flow.

## Acceptance criteria

The fakes are the I/O boundaries only: a provider adapter behind a real `LLMGateway` (router and
answer each), an `EntityExtractor` on a fake connection, a fake Qdrant client, a fake embedder, a fake
reranker, an in-memory `FilingStore` and a fake database connection. The arm, `IntentRouter`, the
extractor's logic, the calculator, the prompt registry, the retriever, the score cache and the
config loader are real. Scores are rational numbers in the tests, and every expected value is
worked by hand.

- **C1**: `"html_rag"` is in `system_names()` after the seven existing names; `build_system` builds it
  offline, with that `name`, refuses a `model`, and its `model` is the synthesizer's on each profile.
  The stage resolves to prompt `v1`, with `{question}` and `{excerpts}` in the user template. Oracle:
  requirements 16, 25.
- **C2**: The committed file loads with the values of requirement 1. A typo, a missing key and each
  range breach raise. The cache path expands and is a `Path`. Metamorphic: changing a key's value in
  the file changes that attribute and no other. Oracle: requirements 1-3.
- **C3**: `search_scopes` gives the hand-listed scopes and reason for one and two filers, a range,
  no period, a `None` last-seen year, an ambiguous company beside a resolved one, an ambiguous
  period, no company, nothing resolved and too many filers. Property: filers never repeat, and a
  company's scope holds exactly the years computed in the test. Oracle: requirements 5-6.
- **C4**: The search request has the hand-listed collection, prefetches, filters, RRF and limit, all
  read from config; the question is embedded once. Metamorphic: other config values reach the
  request unchanged. Oracle: requirements 7-8; Qdrant's `Rrf` model.
- **C5**: The reranker gets `embedding_input` text in candidate order; chunks come back in hand-sorted
  order, cut at `context_top_k`; two filers are reranked apart and both kept; an empty scope makes no
  rerank call; a failing reranker raises. Property: for any scores, the order equals the sort computed
  in the test. Oracle: requirements 9-11.
- **C6**: Expansion pulls the hand-listed neighbours and sibling parts, in order, with the id formula,
  one fetch; it respects the threshold and the cap; expansion off makes no fetch; a failed fetch raises.
  Property: an expansion chunk is never also a kept one, and there are at most
  `expansion_max_chunks`. Oracle: requirement 12; html10k-chunks requirement 12's `uuid5`.
- **C7**: A repeat, in the same object and in a new one on the same file, makes no call to the reranker
  and returns the same scores and `elapsed_ms`; a changed document, query, order or model is a miss;
  empty makes no call; bad output raises and isn't kept. Two misses never overlap, and two equal
  concurrent calls make one. Property: for any scores, a stored result reads back exactly. Oracle:
  requirements 13-15.
- **C8**: The excerpts are the hand-written objects; filing text with quotes, braces and an instruction
  reaches only the user message, JSON-encoded; an answer's `chunk_ids` are the hand-listed ones; a
  cell's labels line up or the answer is `hedged`; a loose quote is `hedged`; an unknown chunk isn't
  cited. Property: `chunk_ids` is always a subset of the chunks given. Oracle: requirements 16-17,
  20-22.
- **C9**: A lookup, a difference, a percentage and a breakdown equal the hand-worked values; a `value`
  in the reply is ignored; `printed_value` gives the listed value for each printed form; a nil is never
  0; an n/m case gives no number and is `hedged`. Property: for any printed integers and scale, the
  value equals the sum computed in the test from the integers, not by the arm. Oracle: requirements
  18-19, 23; `src/retriever/xbrl/xbrl_calculator_tool.py`'s grammar.
- **C10**: Each status of requirement 24 gives the stated answer, with no answer call for
  `no_candidates` and a cost of the router call alone. A failed row is dropped and `hedged`. Property:
  an answer never has a number with an unresolved input behind it. Oracle: requirement 24.
- **C11**: `selection` holds the hand-written record, survives `json.dumps` and survives a provider
  error; cost is the router's and the answer's at `MODEL_PRICING` rates; `query_id` differs and
  `latency_ms >= 0`; each failure becomes `error`; `RunHalt` propagates from both calls; a
  repeat run has the same scores with `rerank_cache_hits` up and `rerank_calls` at 0, and the same
  `rerank_ms`; a degraded case is searched, not refused. Property: changing `case.ticker`, `fy`, `cik`,
  `kind` or `universe` changes nothing sent. Oracle: requirements 4, 6, 26-27.
- **C12**: The load passes `table_max_chars` from config, so a table of about 1,500 characters splits at
  1200 and not at 5000; points and vectors match; batches are bounded; a repeat is `skipped` with no
  embedding; a partial filing is replaced; a failed embedding deletes nothing; one bad filing leaves the
  rest. `list_annual_filings` gives the hand-written entries. Property: for any number of chunks, every
  batch is within the limit and all are uploaded. Oracle: requirements 28-30; a hand-built document.
- **C13**: A whole answer runs only SQL on `entity_master` and `fiscal_calendar`, touches only
  `html10k_text`, and the load's SQL names only its three tables; the new modules' imports obey
  requirement 33; a poisoned payload (`xbrl_fact_ids`, `xbrl_concepts`) never reaches the model.
  Metamorphic: a poisoned payload changes nothing sent. Oracle: requirements 31-33; plan §5.2.

## Interface

```python
# src/retriever/html10k/config.py
class Html10kRagConfig(BaseModel)   # one field per key of requirement 1
load_html10k_config(path: Path | None = None) -> Html10kRagConfig

# src/retriever/html10k/scopes.py
search_scopes(result: IntentRouterResult, max_filer_searches: int)
    -> tuple[list[dict[str, list]], str | None]

# src/retriever/html10k/rerank_cache.py
class RerankResult(NamedTuple)      # scores: list[float], cache_hit: bool, elapsed_ms: float
class CachedReranker:
    __init__(self, inner: Any, *, model: str, cache_path: Path) -> None
    rerank(self, query: str, documents: Sequence[str]) -> RerankResult

# src/retriever/html10k/retriever.py
class RetrievedChunk(NamedTuple)    # chunk_id, accession_no, text, cik, ticker, company_name,
                                    # fiscal_year, form_type, section_key, section_label,
                                    # contains_table, table_index, part, parts, html_char_start,
                                    # html_char_end, prev_chunk_id, next_chunk_id, rerank_score,
                                    # is_expansion
class Html10kRetrieval(NamedTuple)  # chunks: list[RetrievedChunk], rerank_calls: int,
                                    # rerank_cache_hits: int, rerank_ms: float
class Html10kRetriever:
    __init__(self, *, embedder: BaseEmbeddingGenerator, client: QdrantClient,
             reranker: CachedReranker, config: Html10kRagConfig) -> None
    retrieve(self, query: str, scopes: Sequence[Mapping[str, Sequence[object]]]) -> Html10kRetrieval

# src/retriever/html10k/answerer.py
printed_value(printed: str, scale: object) -> float | None
match_cell(text: str, row: str | None, column: str | None, printed: str) -> str

# src/eval/systems/html_rag.py
class HtmlRagSystem:                # name "html_rag", attribute model
    __init__(self, *, gateway: LLMGateway | None = None, router: IntentRouter | None = None,
             embedder: Any = None, qdrant_client: Any = None, reranker: Any = None,
             config: Html10kRagConfig | None = None) -> None
    answer(self, case: EvalCase) -> SystemAnswer

# src/pipelines/html10k/load_to_vectordb_pipeline.py
list_annual_filings(conn: Any, sec_edgar_root: str, *, tickers: Sequence[str] | None = None,
                    fiscal_years: Sequence[int] | None = None) -> list[dict[str, Any]]
class Html10kLoadPipeline:
    __init__(self, *, store: FilingStore, embedder: BaseEmbeddingGenerator, client: QdrantClient,
             config: Html10kRagConfig) -> None
    load_filing(self, filing: Mapping[str, Any], source_key: str) -> str
    run(self, filings: Iterable[Mapping[str, Any]]) -> dict[str, list]
```

A spec-time class holds only a docstring and stubs, so the named tuples and the config model are
declared empty and the implementer adds these fields. Spec tests reach them only through these
functions.

Behaviour on existing code and new files, which the implementer adds:

- `configs/pipelines/html10k/rag.yaml`, with the values of requirement 1.
- `prompts/templates/xbrl/registry.yaml` gains stage `html_rag`, with `v1` templates under
  `_base/html_rag/v1/`. The user template holds `{question}` and `{excerpts}`, and no `[` before
  `{excerpts}`. No existing stage's version moves.
- `system_names()`, `build_system()` and the package docstring.
- A CLI on the load module (`--ticker`, `--fiscal-year`, `--all`), and unit tests for it.
- `requirements*.txt`: the lower bound of `qdrant-client` moves to 1.17, which `Rrf` weights need. The
  current `>=1.7,<2` is looser than this code allows.
- The module `CLAUDE.md` and `docs/guides/html-flow/` of plan §5.4. `WS10-H2` in the manifest moves when
  the arm is in. `html_rag` in `docs/design/XbrlRag_Evaluation.md` and the evaluation guide.

## Notes

**These numbers are provisional.** Requirement 1's values come from one review, on 2026-10-30: dev
data, in-loop, template numeric questions only, with a lower-bound and an upper-bound gold.
Differences under about 3 points were not resolved there, and the jina-against-bge comparison has 15
cases. Nothing was measured on narrative questions. That's why they are configuration, and why every
answer records them. Revisit `dense_weight` and `sparse_weight` on narrative keys, and
`expansion_score_threshold` if the reranker changes (it is on jina's scale). `context_top_k` of 20 is
this spec's choice: the review says 15-20, and it found 0.811 recall at 10 and 0.878 at 20.

**Choices that could weaken the baseline, and what was done about each.**

| Risk | What this spec does |
|---|---|
| The current retriever's 10 candidates and equal RRF | 30 candidates, since 50 ([html-rag-candidates](html-rag-candidates.md)), dense 2:1 |
| A question the resolver can't place gets refused | Searches on what resolved (requirement 6); refusing would be easier to code and would flatter XbrlRag |
| One filer crowds out another in a comparison | One search and one rerank per filer, each keeping `context_top_k` |
| A filer filter is missing, so the reranker can't tell filings apart | The reranker sees `embedding_input`, with filer, year and section |
| Silent loss of context | Expansion failures are loud, not swallowed as in `_expand_with_neighbors` |
| A table split across parts | Sibling parts come with a kept part |
| Strict citation checks turn right answers into refusals | Labels compare loosely; a label or quote mismatch is `hedged`, not refused. Only a number that isn't in the cited chunk at all blocks an answer |
| `hedged` softens grading | It does not change a correct answer's grade, and it keeps a wrong one off the silent-wrong count. That favours the baseline, which is the safe direction |
| A low score threshold that declines good answers | There is none. Declining is left to no candidates, the model and citations, until a score floor can be measured |
| Screens | Not excluded. A screen names no filer, so it runs a year-filtered search, and it will do poorly. That's a real property of this design, and the case stays counted |
| The default of 2,000 completion tokens in `StageLLM` | Shared with the other arms. A long breakdown could hit it. Check `finish_reason` in the pilot |

**What is inherited, on purpose.** `last_seen_fy` isn't corrected until the `last-seen-fy` change
lands, so a question with no year can search a year with no chunks
(CRM's latest, NVDA's 2027), which ends in `no_candidates`. Both flows move together when that
merges, and that's the point of sharing the resolver. No relaxation of the year filter is added: it
would answer from the wrong year.

**The known gap: text keys and chunk offsets. Scoped out, not fixed here.** Plan §3.3 records
text-key passages as spans in the canonical HTML, and chunk offsets are UTF-8 bytes in the stripped
document, which are different bytes. So narrative retrieval can't be scored by offset yet. What this
change does is keep that possible: `selection["retrieved"]` records every chunk's accession and byte
range, so a run can be scored afterwards with no re-run. A follow-up spec should map passages onto
chunks, preferably by normalised text containment, which needs no byte mapping. Until then, read
narrative `html_rag` results as answer quality only. Retrieval recall as an eval metric (review item
F) goes in the same follow-up.

**What's in the record and what's not cached.** Only rerank scores are cached. The review also
suggested caching candidates, but Qdrant takes about 25 ms and one query embedding well under a
second, against about 69 s for a rerank of 30. A cached run's `latency_ms` is short by design, so
report `rerank_ms` next to it. The default cache file is under `data/`, which is per-checkout: in a
worktree, set `XBRLRAG_HTML10K_RERANK_CACHE` to share it.

**Text in the reply.** The prompt tells the model not to state a computed figure in `text`, but
nothing strips one. Grading reads `numeric_values` and `rows`, which only the calculator fills. The
judge reads `text`.

**Other known limits.**
- The primary document is the source (plan §5.3). A filer that prints its statements in an exhibit has
  none here, the same limit as the long-context arms.
- Two live annual filings for one year (a 10-K and a full 10-K/A with no `superseded_by`) are both
  loaded and both searched; the resolver refuses that case for the long-context arms.
- Questions about a quarter resolve a fiscal year and search that year's annual filing.
- `unit` from the reply isn't checked before arithmetic. The filing's units are the model's reading,
  and it picks the expression.
- jina-reranker-v3 is CC BY-NC 4.0, which matters to the public-release decision. `reranker_model`
  and RagInference's `RERANKER=bge` are the way out.

**For the operator; neither has been run.**
- **The load.** About 13.7 hours of RagInference at 0.796 s per chunk, plus about 0.6 hours for
  `table_max_chars=1200`, and the HTML flow's chunk count will differ. It needs a window agreed with
  the maintainer, because it holds the service that the paid runs need. Set
  `RAGINFER_TIMEOUT_S=1800`. It can stop and restart at a filing boundary. Dev filers only: this
  collection is not for held-out filers, which need their own load, in their own window. Changing
  `table_max_chars` or chunking later means a new load, since chunk ids change.
- **A paid pilot.** After the load, the checks in the root `CLAUDE.md`, then a one-case smoke, then a
  small slice with a hard `--max-cost-usd`. With an empty cache, expect about 69 s of rerank per filer
  searched in each case. Run about 100 cases side by side with bge before freezing the baseline (review
  item G), including text cases.
- **Check before the load:** `GET /healthz` names `jinaai/jina-reranker-v3`, which is what
  `reranker_model` sends.

**Retired test changed in this spec PR.** `test/spec/long-context-arms/test_c1_names.py` pins the
seven-name tuple. CI requires graduated tests to pass before and after this change, so it now holds
on both sides: the first seven names. C1 here pins `html_rag`'s place, without an exact tuple, so
that `frontier_sec_tools` can add its own name.

**Not in spec tests:** the prompt's wording; the load's CLI; the default backends' construction against
real services; the pilot. Run all of `test/spec` after implementing.

**Oracles.** Hand-built payloads, replies, tables and registry rows; AAA, BBB and CCC are synthetic.
No held-out filer appears. No expected value comes from running this code.

**The tests were checked for being satisfiable.** Before locking, a throwaway implementation in a
scratch folder, on top of the `impl/html10k-chunks` code, passed every test here; it found two
errors in the tests, which were fixed against the requirements. It isn't committed and it isn't a
design: `CachedReranker` not subclassing `BaseReranker`, for one, is only because that module pulls
in torch through `retriever_util`, which the slim image doesn't have.
