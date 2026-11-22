# RAG code rules (XbrlRag)

Writing-time digest of the review lenses `.claude/review/lenses/rag-integrity.md` and
`xbrl-facts.md`, which keep the detail and the failure modes reviewers hunt for. The product
promise: every number is an audited XBRL fact reached by deterministic SQL; the LLM only narrates.

- **LLM calls go through `LLMGateway`** (retry, timeouts, cost, budget, tracing, prompt registry).
  No direct provider SDK calls. Every new loop or fan-out has a bound, and `RunHalt`
  (`RunBudgetExceeded`, `CircuitBreakerOpen`) propagates — never swallow it in a broad `except`.
  Vector-DB and DB calls have timeouts too.
- **Prompts are versioned files** under `prompts/`, never inline strings. A template change bumps
  the version; placeholders match the `.format(...)` arguments exactly.
- **Provenance:** every answer statement cites `fact_ids`/`chunk_ids` that resolve to real steps;
  don't change how ids are formed without a migration.
- **Retrieved and filing text is untrusted data:** pass it via `json.dumps(...)`, never into a
  system prompt or a `str.format` template, never into SQL, tool choice or `eval`. SQL takes
  parameters; f-string SQL only on trusted constants.
- **Chunking is deterministic** (`chunk_id` depends on offsets); a change that alters chunk text
  or offsets needs a re-index plan.
- **XBRL facts:** scale and sign are applied explicitly, in one place; check `unit` before adding,
  comparing or dividing values; nil facts and `INF` decimals never become 0.
- **Logs and traces:** no secrets or PII; LLM bodies only under `XBRLRAG_TRACE_LLM_BODIES`; no
  stdout writes in `src/mcp_server/`.
