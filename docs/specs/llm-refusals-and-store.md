# llm-refusals-and-store — refusals counted and costed, OpenAI responses not stored

Two small changes to the LLM gateway, made before the code freeze of external test v1.

What's wrong today:
- **Refusals vanish from the rollups.** The gateway records a refused call as an
  `llm_call` event with `outcome: "refusal"` (`llm-anthropic-and-caching`, requirement 4).
  But `audit.v_llm_calls` counts only `outcome = 'error'`, `/metrics` exports only errors,
  and `audit.v_model_cost` sums only `outcome = 'ok'`.
- **Refused calls cost nothing on our books, but some are billed.** The Anthropic adapter
  raises `LLMRefusalError` before it reads `usage`. So a refused call records no tokens and
  no cost, and the trace total, the eval runner's `--max-cost-usd` and the run budget never
  see it. Anthropic bills some refusals (see Notes).
- **OpenAI keeps the requests.** Responses API calls run with `store` at its default
  (`true`), so OpenAI retains each request and reply. Nothing in this repo reads a stored
  response (no `previous_response_id`). Chat Completions already defaults to `store: false`.

## Requirement

1. **Billing rule.** `refusal_is_billed(category, completion_tokens)` in
   `src/llm/gateway/adapters/anthropic_adapter.py` returns `True` when either:
   - `completion_tokens > 0` (a mid-stream refusal: output was already streamed); or
   - `category` is one of `"bio"`, `"frontier_llm"`, `"reasoning_extraction"`.

   Otherwise it returns `False`, including for `category=None`. The billed categories are
   one module-level constant in that file, with the date of the source page in a comment,
   because Anthropic says the list may change.
2. **The refusal carries its usage.** `LLMRefusalError` gains three attributes:
   - `category: str | None`: `stop_details.category`, or `None`;
   - `usage: ParsedResponse | None`: the reply's usage, mapped exactly as a successful
     reply's usage is (cache reads and writes are subsets of `prompt_tokens`), with
     `content=""`;
   - `billed: bool`: `refusal_is_billed(category, usage.completion_tokens)`.

   `LLMRefusalError("msg")` with nothing else keeps working: `None`, `None`, `False`.
   `AnthropicAdapter.parse` sets all three on a `stop_reason == "refusal"` reply. The
   message text is unchanged.
3. **The gateway records a refusal's tokens and cost.** On `LLMRefusalError` it still
   makes one attempt only, records one `llm_call` event with `outcome: "refusal"` and
   re-raises the same exception. Additionally:
   - when `usage` is present, the event carries `prompt_tokens`, `completion_tokens`,
     `total_tokens`, `cached_prompt_tokens` and `cache_write_tokens` from it;
   - the event carries `refusal_category` and `billed`;
   - `cost_usd` on the event is the cost tracker's cost of that usage when `billed`
     (the same formula as a successful call), and `0` when not billed or when `usage` is
     `None`;
   - a billed refusal is recorded in the cost tracker; an unbilled one is not;
   - the active trace gets one `record_llm_call` with those tokens and that cost, so
     `trace.total_cost_usd` and `trace.llm_calls` include the refusal;
   - `trace.consecutive_failures` is left unchanged (a refusal neither trips nor resets
     the circuit breaker).
4. **The views count refusals.** In `scripts/ddl/audit/analyst_views.sql`:
   - `audit.v_llm_calls` gets a `refusals` column, `count(*) FILTER (WHERE
     payload->>'outcome' = 'refusal')`, placed right after `errors`. The view is still
     dropped first. `errors` still counts only `'error'`.
   - `audit.v_model_cost` sums `outcome IN ('ok', 'refusal')`, so billed refusals reach
     the per-model cost.
5. **`/metrics` exports refusals.** `metrics_exporter` emits a counter family
   `llm_refusals_total` with labels `stage` and `model`, one sample per `v_llm_calls` row,
   from its `refusals` column. The `model_cost_usd` help text says "billed calls" instead
   of "successful calls".
6. **The LLM page shows refusals.** `ui/src/pages/technical/LlmPage.tsx` shows a
   Refusals column beside Errors, and `ui/src/lib/types.ts` adds `refusals?: number`.
   (Not covered by spec tests; the implementer checks it with the UI build.)
7. **OpenAI Responses calls are not stored.** `OpenAIAdapter.build_params` for a request
   with a cache point (the Responses path) adds `"store": False`. Any `extra` is still
   applied last. The Chat Completions path is unchanged: no `store` key.

## Acceptance criteria

Fakes are at the I/O boundaries only:
- fake raw Anthropic replies passed to `parse`;
- a fake adapter behind a real `LLMGateway`, with a real `CostTracker` and a real trace;
- a fake `Reader` for `metrics_exporter` returning view rows;
- the committed DDL file, read as text.

`refusal_is_billed`, the adapters' `build_params` and `parse`, the gateway, the cost
tracker, the trace and the exporter are real.

- **C1**: the billing rule.
  - Each of `bio`, `frontier_llm`, `reasoning_extraction` with 0 completion tokens is
    billed.
  - `cyber`, `general_harms`, `None` and an unknown string with 0 completion tokens are
    not billed.
  - Any category, including `None`, with 1 or more completion tokens is billed.

  Property: for random categories and completion counts, the result equals
  `completion_tokens > 0 or category in {bio, frontier_llm, reasoning_extraction}`.
  Oracle: requirement 1 and Anthropic's refusals page (see Notes).
- **C2**: the refusal carries its usage.
  - A fake `claude-opus-5-5` refusal reply with `stop_details.category = "bio"` and usage
    `input_tokens=1000`, `cache_read_input_tokens=9000`, `cache_creation_input_tokens=500`,
    `output_tokens=0` raises `LLMRefusalError` with `category == "bio"`,
    `usage.prompt_tokens == 10500`, `usage.cached_prompt_tokens == 9000`,
    `usage.cache_write_tokens == 500`, `usage.completion_tokens == 0` and `billed is True`.
  - The same reply with `category = "cyber"` has `billed is False`. A reply with no
    `stop_details` has `category is None`.
  - `LLMRefusalError("x")` has `category is None`, `usage is None` and `billed is False`.

  Property: over random usage counts, `usage.prompt_tokens == input + cache_read +
  cache_creation`, matching what `parse` gives a successful reply with the same usage.
  Oracle: requirement 2 and `llm-anthropic-and-caching` C3's usage mapping.
- **C3**: the gateway records a refusal.
  - A fake adapter on `claude-opus-5-5` raises a billed refusal with 100,000 prompt tokens
    (90,000 cached, 0 written) and 0 completion tokens, under `max_attempts=3`. Then:
    - it is called once;
    - the same exception reaches the caller;
    - the single `llm_call` event has `outcome: "refusal"`, the five token fields,
      `refusal_category`, `billed: true`, and `cost_usd` equal to the closed form at the
      committed `pricing.yaml` rates;
    - `trace.total_cost_usd` and `trace.llm_calls` grew by that cost and by 1;
    - the cost tracker holds one record with that cost.
  - The same usage with `billed=False` gives `cost_usd == 0`, no cost-tracker record, and
    the tokens still on the event and in the trace.
  - A refusal with `usage=None` gives `cost_usd == 0` and does not raise.
  - `consecutive_failures` is the same before and after, starting from 0 and from 2.

  Metamorphic: doubling every token count of a billed refusal doubles `cost_usd`. Oracle:
  requirement 3 and `llm-provider-profiles`' cost rule.
- **C4**: the DDL counts refusals. Reading `scripts/ddl/audit/analyst_views.sql` as text:
  - the `audit.v_llm_calls` definition has a `refusals` column filtered on
    `outcome = 'refusal'`, placed right after `errors`;
  - `errors` still filters on `'error'` only;
  - a `DROP VIEW IF EXISTS audit.v_llm_calls` comes before its `CREATE`;
  - `audit.v_model_cost` filters on `outcome IN ('ok', 'refusal')`.

  Property: whitespace and letter-case changes in the SQL text don't change the result.
  The checks normalize them, so the implementer's formatting is free. Oracle:
  requirement 4.
- **C5**: `/metrics` exports refusals. With a fake reader returning two `v_llm_calls` rows
  (`refusals` 3 and 0), the exposition has a `# TYPE …llm_refusals_total counter` line and
  one `llm_refusals_total{stage=…,model=…}` sample per row with those values. The
  `llm_errors_total` samples are unchanged. Property: over random row sets, there is one
  refusals sample per row, equal to its `refusals`. Oracle: requirement 5.
- **C6**: OpenAI Responses calls are not stored.
  - `build_params` with a cache point returns `store: False`.
  - Without a cache point, it returns no `store` key.
  - `extra={"store": True}` still wins (it's applied last).

  Property: over random messages with at least one cache point, `store` is `False` and the
  rest of the dict equals what `llm-anthropic-and-caching` requirement 7 specifies.
  Oracle: requirement 7 and OpenAI's Responses API reference (`store` defaults to `true`).

## Interface

New (stubbed in the spec PR), in `src/llm/gateway/adapters/anthropic_adapter.py`:

- `refusal_is_billed(category: str | None, completion_tokens: int) -> bool`

Behaviour on existing code, which the implementer adds:

- `LLMRefusalError`: the attributes `category`, `usage` and `billed`, with defaults
  `None`, `None` and `False` (requirement 2). A keyword-only constructor is fine, as long
  as `LLMRefusalError("msg")` still works.
- `AnthropicAdapter.parse`: fills them on a refusal.
- `LLMGateway.complete`: the refusal accounting in requirement 3.
- `analyst_views.sql`, `metrics_exporter._collect_llm`, the LLM page and its type.
- `OpenAIAdapter._responses_params`: `store: False`.

A retired test changes in this spec PR:
`test/spec/llm-anthropic-and-caching/test_c5_openai_routing.py` compares the Responses
dict exactly, so it gains `"store": False`, with a comment naming this change.

## Notes

- **Refusal billing**, from Anthropic's "Refusals and fallback" page, section "How refusals
  are billed" (read 2026-10-21):
  - A refusal before any output is billed when `stop_details.category` is `"bio"`,
    `"frontier_llm"` or `"reasoning_extraction"`. Any other category, or `null`, is not
    billed. Either way, token counts appear in `usage`.
  - A mid-stream refusal bills the input tokens and the output already streamed, at
    normal rates.
  - The billed categories may change. That's why requirement 1 keeps them in one constant
    with a date.
- **No long-context premium.** The same check confirmed that Claude Opus 5.5 has none:
  "Claude 4.6 and later models (except Claude Haiku 5.5) … include the full 1M token
  context window at standard pricing". So `pricing.yaml` needs no tier for the 221K-token
  TSM filing.
- **OpenAI refusals are out of scope.** OpenAI marks a refusal only under structured
  outputs, which this repo doesn't use (JSON is prompt-instructed). The OpenAI adapter
  keeps raising nothing special.
- **Why it matters for the benchmark:**
  - refusal/decline is a pre-registered metric, and the arms already grade
    `LLMRefusalError` as a refused answer (`_stage_llm.answer_in_trace`);
  - a billed refusal's cost now reaches `SystemAnswer.cost_usd` through the trace, so
    `--max-cost-usd` sees it.
- All fixtures are synthetic. No network.
