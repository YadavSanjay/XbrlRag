# baseline-arms — closed-book and naive-RAG comparison systems

Part of external test v1, first of two changes (the long-context arm is the
second). It adds two systems the eval runner can score on the same suite as XbrlRag, so a
reader can see what the plan and fact store buy over approaches they already know:

- **`closed_book`**: the model alone, with no documents. It's a control for how often the
  model states a wrong figure on its own, not a benchmark.
- **`naive_rag`**, in two variants:
  - **plain** (`naive_rag_plain`): hybrid search over the whole collection;
  - **filtered** (`naive_rag_filtered`): hybrid search restricted to the question's filer
    and fiscal year, a generous baseline that is hard to call a strawman.

No XbrlRag pipeline behaviour changes.

## Requirement

1. **One answer format.** Both arms ask the model for the JSON answer object of
   arm-neutral-grading and turn its reply into a `SystemAnswer` with
   `src.eval.core.answer_parsing.parse_structured_answer`, so they are graded by exactly the
   rules XbrlRag is. An arm never produces `fact_ids`.
2. **Closed book.** One LLM call per case. The model sees the question and nothing
   retrieved. Its prompt allows it to say it doesn't know.
3. **Naive RAG retrieval.**
   - The question is embedded once, with the same BGE-M3 dense+sparse embedder XbrlRag
     uses.
   - One hybrid (RRF-fused) query runs against the same Qdrant collection
     (`xbrl_sec10k_text`), with `limit=10`.
   - **No reranker.** Reranking costs about 2.8 s per chunk on the CPU this runs on, so
     reranking 50 candidates would take about 19 hours for one 482-case run. The post
     states that no reranker was used.
   - Neighbour chunks are not added.
4. **The filter.** The plain variant passes no filters. The filtered variant restricts to
   `ticker = case.ticker` and `fiscal_year = case.fy`, each only when the case has it, so
   a cross-filer screen (no ticker) is filtered by fiscal year only. The suite's cases
   carry `ticker` and `fy` but no `cik`, so the Qdrant client's hybrid query gains a
   ticker filter. The collection already has a `ticker` payload index.
5. **The context.** All retrieved chunks go into one LLM call, in retrieval order, each
   with its `chunk_id` and its text. Chunk text is untrusted filing text: it goes into
   the user message through `json.dumps`, never into the system prompt or a format
   template. If retrieval returns no chunks, the arm makes no LLM call and refuses.
6. **Citations.** `SystemAnswer.chunk_ids` holds the chunk ids the answer cites, limited
   to chunks that were actually retrieved for that case, in order of first citation,
   without duplicates. A cited id that wasn't retrieved is dropped. Closed-book answers
   cite nothing.
7. **Gateway, prompts and tier.** Every LLM call goes through `LLMGateway` with stage
   `closed_book` or `naive_rag`, on the **same `medium` tier** as the synthesizer. Prompts
   are versioned files resolved by the XBRL prompt registry (`v1` for both stages).
8. **Accounting.** Each answer opens its own trace, so it has a `query_id`, its LLM
   calls' `cost_usd` (the runner charges that against `--max-cost-usd`, so it must not
   be lost) and `latency_ms`.
9. **Failures.** Nothing raises out of `answer()` except a run halt. A provider or
   retrieval error becomes `SystemAnswer(error=...)`. `RunHalt` (`RunBudgetExceeded`,
   `CircuitBreakerOpen`) propagates, per the repo's RAG rules.
10. **Selection.** `scripts/eval/run_suite.py --system` accepts every name in
    `system_names()`, and builds the system with `build_system`. Constructing a system
    makes no network calls; its gateway, embedder and Qdrant client are built on first
    use when not injected.

## Acceptance criteria

The fakes in the tests are the I/O boundaries only: a provider adapter behind a real
`LLMGateway`, an embedder, and a Qdrant client. The arm code, the gateway, the prompt
registry and the answer parser are real.

- **C1**: `ClosedBookSystem.answer(case)`:
  - makes exactly one provider call;
  - sends messages that contain `case.question` verbatim and no chunk text;
  - returns the numbers, rows, `refused` and `hedged` of the reply as
    `parse_structured_answer` reads them, with `fact_ids == []` and `chunk_ids == []`,
    even when the reply lists `cited_chunks`.

  Oracle: requirements 1 and 2. The expected values are the reply object's own fields
  (hand-built and random objects).
- **C2**: `NaiveRagSystem.answer(case)` (plain):
  - embeds the question once;
  - calls `hybrid_query_text` once, on collection `xbrl_sec10k_text` with `limit=10` and
    `tickers_in_query`, `fiscal_years_in_query`, `ciks_in_query` all `None`;
  - sends one provider call whose user message contains every retrieved chunk's text and
    `chunk_id`, in retrieval order;
  - when retrieval returns no points, makes no provider call and returns
    `refused=True`, `numeric_values == []`, `cost_usd == 0`.

  Oracle: requirements 3 and 5, with fake points built in the test.
- **C3**: `NaiveRagSystem(filtered=True)` passes `tickers_in_query == [case.ticker]` when
  `case.ticker` is set and `None` otherwise, and `fiscal_years_in_query == [case.fy]` when
  `case.fy` is set and `None` otherwise. `ciks_in_query` is always `None`. The plain
  variant passes `None` for all three whatever the case carries. Oracle: requirement 4.
- **C4**: `chunk_ids` is the reply's `cited_chunks` restricted to the retrieved chunk ids,
  in order of first appearance, deduplicated. `fact_ids` is always `[]`. Oracle:
  requirement 6, hand-built and random citation lists.
- **C5**: accounting, for every arm that made a provider call:
  - `query_id` is a non-empty string, different for two answers;
  - `cost_usd > 0`;
  - doubling the fake provider's reported prompt and completion tokens doubles
    `cost_usd`, within 1e-9 relative (pricing is linear in tokens, and the fakes report
    no cached tokens);
  - `latency_ms >= 0`.

  Oracle: requirement 8.
- **C6**: failures:
  - a non-transient provider exception gives `error` non-empty and `numeric_values == []`,
    and does not raise;
  - so does an exception from the embedder or from `hybrid_query_text`;
  - a gateway whose `RunBudget` has `max_llm_calls=0` makes `answer()` raise `RunHalt`.

  Oracle: requirement 9.
- **C7**: selection and configuration:
  - `system_names()` returns exactly `("xbrlrag_full", "closed_book", "naive_rag_plain",
    "naive_rag_filtered")`;
  - `build_system(name)` returns a `SystemUnderTest` whose `name` is `name`, for each of
    them, without network access;
  - any other name raises `ValueError`;
  - for either model set (`use_replacement` false or true),
    `xbrl_stage_models(use_replacement)["closed_book"]` and `["naive_rag"]` both equal its
    `["synthesizer"]`;
  - the XBRL prompt registry resolves both stages for the default provider and series at
    version `v1`.

  Oracle: requirements 7 and 10.

## Interface

New (stubbed in the spec PR):

- `src.eval.systems.closed_book.ClosedBookSystem`
  - `__init__(self, gateway: LLMGateway | None = None) -> None`
  - `answer(self, case: EvalCase) -> SystemAnswer`
  - its `name` is `"closed_book"`.
- `src.eval.systems.naive_rag.NaiveRagSystem`
  - `__init__(self, *, filtered: bool = False, gateway: LLMGateway | None = None,
    embedder: BaseEmbeddingGenerator | None = None, vector_db_client: Any = None) -> None`
  - `answer(self, case: EvalCase) -> SystemAnswer`
  - its `name` is `"naive_rag_filtered"` when `filtered`, else `"naive_rag_plain"`.
- `src.eval.systems.system_names() -> tuple[str, ...]`
- `src.eval.systems.build_system(name: str, *, persist_audit: bool = False) -> SystemUnderTest`.
  `persist_audit` applies to `xbrlrag_full` only.

Behaviour on existing code, which the implementer adds:

- `XbrlQdrantVectorDBClient.hybrid_query_text` gains `tickers_in_query: list[str] | None =
  None`, a `MatchAny` on payload `ticker`, combined with the other filters by AND.
- `XBRL_STAGE_TIERS` gains `closed_book` and `naive_rag` at `medium`.
- `prompts/templates/xbrl/registry.yaml` gains the two stages, with `v1` templates under
  `_base/<stage>/v1/`.
- `scripts/eval/run_suite.py --system` takes its choices from `system_names()` and builds
  the system with `build_system`.
- The new modules get their docstrings, `name` attributes, and the `src/eval/systems`
  package docstring and `__all__` are updated (a spec PR may only add stubs).

## Notes

- **Fakes.** The provider adapter fake follows `test/unit/test_llm_gateway.py`'s
  `_FakeAdapter`: it records the params it was given and returns a `ParsedResponse` with
  the reply text and token counts. The embedder fake returns
  `{"query": (DenseEmbedding, SparseEmbedding)}`. The Qdrant fake records the keyword
  arguments of `hybrid_query_text` and returns an object with `.points`, each having
  `.id`, `.score` and `.payload` (`text`, `ticker`, `fiscal_year`, …). Tests pass the
  gateway with `rate_limiter=None` and an explicit `RunBudget`.
- **Not in spec tests:**
  - the prompt wording, which is reviewed in the PR and published with the results;
  - live retrieval quality;
  - a 10-case smoke of each arm against the real backends, which the implementer runs
    and reports in the PR.
- **Reporting, not grading:** splitting closed-book results before and after the model's
  training cutoff is done when the runs are analysed, not in this change.
- **Manifest.** `WS9-E5` in `docs/harness/feature_manifest.yaml` names
  `src/eval/systems/xbrlrag_naive_rag.py` as its proposed target. The implementer points
  its checks at `naive_rag.py` and `closed_book.py`. It flips to `done` only with the
  long-context arm (the second change).
- **Oracles:** hand-built synthetic cases, points and replies. No suite files, database
  or network.
