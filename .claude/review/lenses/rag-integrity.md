---
name: rag-integrity
applies_to: ["src/retriever/xbrl/**/*.py", "src/pipelines/xbrl/**/*.py", "src/chunking/xbrl_html_base_chunking.py", "src/vectorstores/xbrl_qdrant_vectordb_client.py", "src/vectorstores/qdrant_vectordb_client.py", "src/embeddings/**/*.py", "src/llm/**/*.py", "prompts/**", "src/eval/**/*.py", "scripts/eval/**", "src/api/**/*.py", "src/mcp_server/**/*.py", "src/observability/**/*.py"]
---
# RAG integrity: grounding, indexes, prompts, cost, eval

The system's guarantee is "cited, audited numbers; the LLM narrates, never calculates, and an
independent verifier drops what its own citations do not support". Hunt for changes that let an
ungrounded or stale statement reach the answer, and for LLM spend without a bound.

## Hunt for

**Grounding chain** (`plan_executor` -> `XbrlRagSynthesizer` -> `XbrlFaithfulnessVerifier`)
- The synthesizer receives `fact_results` (all non-narrative steps) and `context_chunks`. A new
  step field, prompt edit, or status change that lets the model see an unresolved/`AMBIGUOUS`
  value as if usable, or that lets it do arithmetic instead of citing a `compute` step, breaks the
  "numbers are audited" invariant.
- Verifier: a statement with no `fact_ids`/`chunk_ids` is deterministically `UNSUPPORTED`; evidence
  resolves by `citation.fact_id` then `step_id`, per-row for `screen`. Hunt for: a changed
  fallback that marks a failed batch `SUPPORTED`; `_apply_report` no longer setting
  `fully_answered=False` or `metadata.faithfulness.status` when `VerifierStatus != OK`;
  `drop_unsupported` defaulted off; evidence truncation (`DEFAULT_MAX_CHUNK_CHARS` = 600) or
  batch size (`DEFAULT_VERIFIER_BATCH_SIZE` = 8) changes that silently starve the judge; verdict
  index mismatch when statements are dropped (indices refer to the original list).
- Doer is not the judge: the verifier must not share a prompt, state or cached verdict with the
  synthesizer, and the stage's author must not grade its output (eval).
- Citations: every `answer_details[].fact_ids` must resolve to a real step/`fact_id`; a change in
  how `fact_id`/`chunk_id` are formed (ids, types str vs int) breaks `_resolve_fact_evidence` by
  silently resolving nothing, which then reads as `UNSUPPORTED` rather than as an error.
- `_merge_responses` / `<<TOP:id:n>>` substitution (`retrieval_pipeline.py`): merged answers must
  keep each sub-query's citations and `fully_answered`/faithfulness metadata; a dependent
  sub-query given the wrong top-N (ordering by `rank`, falling back to plan tickers) answers about
  the wrong companies.

**Untrusted text as instructions**
- Filing text (narrative chunks, table cells, footnotes), company names/labels, and user queries
  are data. They are embedded in prompts via `json.dumps(...)` into `context_chunks`, plan JSON
  and verifier evidence. Hunt for: chunk text interpolated into a system prompt or a
  `str.format` template (`self._prompt.system.format(...)` with unescaped braces), chunk text
  that can choose tools/ops/SQL, an LLM-emitted value used in SQL without parameters or the
  allow-lists (`plan_validator`, `COMPUTE_EXPR_ALLOWED_RE`, `XbrlCalculatorTool` AST allow-list,
  `AXIS_HINT_TO_LOCAL`), and a widened calculator grammar. Never `eval`/`exec`.
- `publish_gate_clause` and similar fragments use f-string SQL on trusted constants only; a
  user- or LLM-derived value reaching an f-string SQL fragment is a P0/P1.

**Chunking, embeddings, index invalidation**
- `chunk_id = uuid5(accession_no:html_char_start:html_char_end)`: the id depends only on the
  offsets, not the text. Any change to canonicalization, chunk size (`CHUNK_SIZE_IN_CHARS`=1000),
  table handling, or section assignment that alters text without changing offsets leaves stale
  payloads in Qdrant under the same id; one that shifts offsets orphans old points and breaks
  `prev_chunk_id`/`next_chunk_id` links, `chunk_cross_links.chunk_id` joins and `html_char_*`
  citations. A re-index plan (re-run loader with replace semantics, bump collection) must
  accompany it.
- Payload schema (`XBRLChunkPayload`, `_to_payload_dict`, `_XBRL_PAYLOAD_INDEXES`): a new filter
  field with no payload index, a renamed key read by the retriever/UI/API, or `text` dropped for
  the metadata-only collection. Dense/sparse vector names and dimensions must match the embedder
  (BGE-M3) used by both the loader and `XbrlNarrativeRetriever`; a model or dimension change
  without a new collection mixes embedding spaces.
- Retrieval filters: narrative search must be constrained by resolved `cik`/`fiscal_year`
  (and `section_path_ancestors` when hinted). A dropped filter, `fiscal_period` ignored for
  10-Q, or chunk expansion (`prev`/`next`) that crosses a filing boundary pulls another company's
  or year's text into the context.
- Loader (`XBRLVectorDBLoader`): `ensure_collections`, dry-run behavior, `skipped_no_sections`
  accounting; a stage that discovers nothing must fail loudly, not report success.
- Remote vs local inference: `RAGINFER_URL` set -> HTTP embedder/reranker; unset -> in-process.
  Silent fallback from remote to a different model, or a different embedder at query vs index
  time, is a wrong-retrieval bug. New imports on the serving path must keep it torch-free.

**Cache correctness** (`src/pipelines/xbrl/query_cache.py`)
- Key is the normalized query string only. Anything that changes the answer for the same query
  (published filings, model tier, prompt version, `as_of`) must be covered by the epoch flush
  (`query_cache_epoch`, bumped by the publish gate) or be part of the key. A new answer-affecting
  input without invalidation serves stale numbers; the TTL-bounded epoch read is the accepted
  staleness window. Cached responses must be deep-copied in and out.
- L2 (vector similarity) is opt-in; a threshold change can return a *different company's* answer
  for a paraphrase. Treat any loosening as a correctness change.

**LLM cost and budget**
- Every query-time LLM call goes through `LLMGateway` (retry, cost, tracing, prompt-registry
  resolution, escalation). A direct provider SDK call bypasses `cost_tracker`, `RunBudget`
  (`XBRLRAG_RUN_MAX_COST_USD/_LLM_CALLS/_TOKENS/_CONSECUTIVE_FAILURES`) and tracing.
- New loops/fan-out (per-screen-row verifier batches, sub-query fan-out, retry x escalation) need
  a bound; `RunBudgetExceeded`/`CircuitBreakerOpen` (`RunHalt`) must propagate, not be swallowed
  by a broad `except Exception` that turns a halted run into a degraded "answer".
- Escalation (`ESCALATION_POLICY`, `escalation_ladder`, `is_escalation_trigger`): plan_generator
  escalates only after `plan_validation_failed` persists; synthesizer/verifier on
  `parse_error`/`empty_content`. Widening triggers multiplies spend.
- Model names only from the active profile in `configs/llm/llm.yaml` (priced in `pricing.yaml`; an unpriced model raises); never send `temperature` to a `temperature: false` (Claude 5.5) model; gpt-5* calls force `temperature=1` and count
  reasoning tokens against `max_completion_tokens`, so lowering a token cap or dropping
  `reasoning_effort="low"` on the verifier/decomposer reintroduces empty-content failures.
- Eval/batch scripts must keep a hard `--max-cost-usd`.

**Prompts and registry**
- Prompt changes are model-facing behavior changes: they move accuracy numbers. A template edit
  without a version bump (`v1` resolved via `_base`) rewrites history for cached/audited runs;
  a response-format change must update the JSON contract constant in the Python module
  (`XBRL_SYNTHESIZER_RESPONSE_FORMAT`, `FAITHFULNESS_RESPONSE_FORMAT`) and `extract_json` consumers.
- Placeholders in `system.txt`/`user.txt` must match `.format(...)` keyword arguments exactly;
  literal braces in a template need doubling.

**Eval integrity** (`src/eval/**`, `scripts/eval/**`)
- Headline accuracy and `silent-wrong rate` (confident + cited + wrong) are release gates. Hunt
  for graders that pass refusals as correct, drop failing cases, compare against the system's own
  output, or change `grounding precision` semantics (the *right* fact, not merely *a* fact).
- Narrowed runs (`--kind`, `--rerun-failed`, `--sample`) must be stored `tier='partial'` and never
  surfaced as a baseline. Narrative cases must stay serialized (`DEFAULT_KIND_CONCURRENCY`).
  Persisted text must go through `store._scrub` (NUL bytes).
- The golden set / `baseline_v1.json` re-baselined in the same change that lowers the score it
  guards is a finding unless explained.

**Observability**
- New LLM/SQL surfaces emit trace events (`traced_stage`, `log_event`: `llm_call`, `sql_query`,
  `numeric_lineage`, `verifier_error`, `budget_halt`) and respect `XBRLRAG_TRACE_LLM_BODIES`.
  Prometheus names `arag_*` are a public contract; renaming breaks the Grafana/alert rules in
  `configs/monitoring/`. Stdout writes in `src/mcp_server/` corrupt the stdio protocol.

## Severity anchors

- **P0:** an ungrounded or wrong-company/period statement can reach the answer with a clean
  `fully_answered` and faithfulness status (verifier bypass or fail-open, verdict index shift,
  fact-id resolution broken, retrieval filter dropped); LLM/user text reaching SQL or the
  calculator unvalidated; an unbounded LLM loop with no budget or breaker; eval grader that
  converts failures into passes.
- **P1:** stale index/cache can serve outdated numbers or text after a publish; chunk id or payload
  schema change without a re-index path; embedder mismatch between loader and retriever; a direct
  provider call bypassing the gateway; `RunHalt` swallowed; prompt/format contract mismatch;
  `partial` run stored as a baseline.
- **P2:** truncation/batch-size tuning that raises `UNSUPPORTED` false drops; missing trace event;
  escalation trigger widened with bounded cost; prompt change without version note.
- **P3:** prompt wording, log verbosity.

## Proof recipes (py312, PYTHONPATH = repo root; fakes only, never a live LLM/Qdrant/DB)

- **Verifier with a fake gateway:** build `XbrlRagResponse` fixtures (see
  `test/unit/test_xbrl_faithfulness_verifier.py`) and assert: no-evidence statement ->
  `UNSUPPORTED` without an LLM call; a raising/empty fake -> `fully_answered=False` and
  `metadata.faithfulness.status != "ok"`; dropping statement 0 does not shift later verdicts.
- **Degradation paths:** `test_xbrl_synthesizer_degrade.py` pattern, and a fake gateway raising
  `RunBudgetExceeded`: confirm it propagates rather than becoming a normal answer.
- **Budget:** `RunBudget(max_llm_calls=1).check(llm_calls=1)` raises; env-built budget with
  nothing set stays inert (`is_active() is False`).
- **Chunk ids:** call the chunker on a small hand-written canonical HTML fixture twice with a
  one-character text edit that keeps offsets, and confirm whether `chunk_id`s collide; if the
  change touches canonicalization, diff chunk offsets before/after on the fixture.
- **Prompt contract:** load each stage through `get_xbrl_registry()`; `.format` the system/user
  text with the keyword set the module passes, and assert no `KeyError`/stray braces.
- **Cache:** `SemanticQueryCache` with `maybe_invalidate(epoch+1)` then `get` returns `None`;
  a mutated returned response does not alter the stored copy.
- **Torch-free:** `pytest -q test/unit/test_torch_free_contract.py`.
- **Eval graders:** feed `close_enough` / extractors with refusal, empty-rows and mismatched-unit
  responses; run `test_numeric_grader.py`, `test_eval_core.py`, `test_sql_derived_suite.py`.
- Measuring the accuracy effect needs the live suite (`scripts/eval/run_suite.py`, Postgres +
  OpenAI + a cost cap): not runnable in a review; state it under **Skipped**.

## Not findings

- Graceful degradation at query time (decomposer passthrough, synthesizer degraded response,
  verifier `PARTIALLY_SUPPORTED` on failure) when it still flags `fully_answered=False`.
- The query cache being off by default, in-process, and TTL-stale by up to the epoch TTL.
- The BFF swallowing exceptions into a 200 + `error`; v1 raising real codes; no auth.
- Chunk text truncated to 600 chars *in the verifier prompt only*.
- Legacy PDF-flow retriever/chunker behavior (maintenance-only) and `deepeval` code.
- External benchmarks (FinanceBench, FinLoRA) being designed but unscheduled.
- Model tier values in `configs/llm/llm.yaml` profiles (that config is the source of truth); only a
  stage hardcoding a model name is a finding.
