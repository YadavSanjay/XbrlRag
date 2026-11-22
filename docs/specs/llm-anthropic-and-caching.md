# llm-anthropic-and-caching — a complete Claude adapter and explicit prompt-cache points

Second of two multi-provider changes (`llm-provider-profiles` added the profiles, pricing
and cache-write accounting). This change does two things:
- **It completes the Anthropic adapter**, so Claude 5.5 models run the way the API
  expects.
- **It adds explicit prompt-cache points for both providers**, so the long-context arm's
  repeated filing is read from cache instead of paid in full each time.

The design follows the sister project faultline's `llm-anthropic-backend`. It differs on
OpenAI, which faultline left on automatic caching.

What's wrong today:
- **The Anthropic adapter is incomplete.** It calls `messages.create` without streaming,
  drops effort, has no `refusal` handling, and uses an SDK pulled in only indirectly by
  `langchain-anthropic`.
- **No cache hits on gpt-5.6.** On gpt-5.6 models, OpenAI's implicit cache point falls at
  the end of the last user message. The long-context arm's question sits there, so no
  question ever reuses the cached filing (the 2026-10-21 pilot read 0 cached tokens).
  OpenAI documents explicit cache points **only for the Responses API**.

## Requirement

1. **A provider-neutral cache point.** In the gateway's neutral message list, a message
   dict may carry `"cache_point": True`. This means "the prompt up to the end of this
   message is worth caching".
   - The gateway passes messages to the adapter unchanged.
   - `cache_point_count(messages)` counts them.
   - More than 3 cache points in one request raise `ValueError` before any provider call.
     OpenAI allows 4 cache writes, one of them implicit; Anthropic allows 4 breakpoints,
     and the system prompt uses one.
2. **Anthropic requests.** `AnthropicAdapter.build_params` returns exactly the kwargs of
   one `messages.stream` call:
   - `model`;
   - `max_tokens`: the model's `max_output_tokens` from `configs/llm/llm.yaml` when the
     model is configured, because adaptive thinking shares this budget. Otherwise
     `max_completion_tokens`, or 4096 when that's absent too;
   - `system`: today's single text block with `cache_control: {"type": "ephemeral"}`, when
     there are system messages;
   - `messages`: the non-system messages in order, each `{"role", "content"}`.
     - Plain text content stays a string.
     - A cache-point message's content becomes `[{"type": "text", "text": <text>,
       "cache_control": {"type": "ephemeral"}}]`.
   - `output_config: {"effort": <effort>}`, when the model has an `effort` in the config;
   - plus any `extra`.

   These are never sent:
   - `temperature`. A non-`None` temperature for a model the config marks
     `temperature: false` raises `ValueError` before any call.
   - `thinking`. Claude 5.5 runs adaptive thinking by default.
   - `response_format`. JSON stays prompt-instructed and is read by `extract_json`.
3. **Anthropic calls and replies.**
   - `invoke(client, params)` always streams: `client.messages.stream(**params)`, then
     `get_final_message()`. It never calls `messages.create`.
   - `parse` joins the reply's `text` blocks and ignores `thinking` blocks.
   - A `stop_reason` of `"refusal"` raises `LLMRefusalError`, with the refusal category
     when the reply carries one.
   - A reply with no text raises `ValueError` as today.
4. **Refusals are final.** The gateway never retries an `LLMRefusalError`. It records an
   `llm_call` event with `outcome: "refusal"` and re-raises it to the caller.
5. **Transient errors** are classified the same way for both providers. HTTP 408, 409, 425,
   429, 529 and any status ≥ 500 are transient, and so are connection and timeout errors.
   Other 4xx errors are not.
6. **One retry layer.** Both adapters build their SDK clients with `max_retries=0`, so the
   gateway's retry loop is the only one and every attempt shows in the trace.
7. **OpenAI requests.** A request **with no cache point** goes to Chat Completions exactly
   as today. A request **with at least one cache point** goes to the Responses API.
   `OpenAIAdapter.build_params` returns exactly the kwargs of one `responses.create`
   call:
   - `model`;
   - `input`: each message as `{"role", "content": [<part>]}`.
     - `system` becomes role `developer`; `user` and `assistant` keep their roles.
     - The part is `{"type": "input_text", "text": <text>}`, or `output_text` for
       `assistant`.
     - A cache-point message's part also carries `"prompt_cache_breakpoint": {"mode":
       "explicit"}`.
   - `prompt_cache_options: {"mode": "explicit"}`;
   - `max_output_tokens`, when `max_completion_tokens` is given;
   - `temperature`, when given;
   - `reasoning: {"effort": <reasoning_effort>}`, when given;
   - `text: {"format": {"type": "json_object"}}`, when `response_format == "json"`. A
     dict `response_format` with a cache point raises `ValueError`;
   - plus any `extra`.

   `invoke` calls `client.responses.create` when the params hold `input`, and
   `client.chat.completions.create` when they hold `messages`.
8. **OpenAI Responses replies.** `parse` reads:

   | `ParsedResponse` field | Source in the Responses reply |
   |---|---|
   | `content` | `output_text` |
   | `prompt_tokens` | `usage.input_tokens` |
   | `cached_prompt_tokens` | `usage.input_tokens_details.cached_tokens` |
   | `cache_write_tokens` | `usage.input_tokens_details.cache_write_tokens` |
   | `completion_tokens` | `usage.output_tokens` |
   | `reasoning_tokens` | `usage.output_tokens_details.reasoning_tokens` |

   - Missing details count as 0.
   - `status: "incomplete"` gives `finish_reason` `"length"` when the reason is
     `max_output_tokens`, and `"stop"` for a completed reply.
   - An empty `output_text` raises `ValueError`.
9. **The long-context arm uses a cache point.** Its messages are, in order:
   - the system message;
   - a user message holding the filing part, with `cache_point: True`. It is everything up
     to and including the `json.dumps` filing text, and nothing case-specific besides the
     filing;
   - a user message holding the question part.

   The prompt moves to `v2` in the registry. For two cases on the same filing, the system
   message and the cache-point message are byte-identical.
10. **The SDK is pinned.** `anthropic==0.109.1` is pinned exactly in `requirements.txt`,
    `requirements-serve.txt` and `requirements-ingest.txt`.

## Acceptance criteria

Fakes are at the I/O boundaries only:
- a fake SDK client object passed to `invoke`, with `messages.stream`,
  `chat.completions.create` and `responses.create` that record their kwargs;
- fake raw replies passed to `parse`;
- a fake adapter behind a real `LLMGateway`, for the gateway criteria.

The adapters' `build_params`, `invoke` and `parse`, the gateway, the arm and the prompt
registry are real.

- **C1**: cache points.
  - `cache_point_count` counts the marked messages.
  - A gateway request with 4 cache points raises `ValueError` and the adapter is never
    called.
  - A request with 1 cache point reaches the adapter with its messages unchanged,
    including the marker.

  Property: for 0–3 random cache points, the gateway passes the messages through
  unchanged. Oracle: requirement 1.
- **C2**: the Anthropic request.
  - `build_params` returns exactly the dict in requirement 2, for:
    - `claude-opus-5-5` with a system message, one cache-point user message and a plain
      user message;
    - each committed Claude model, where `max_tokens` is its `max_output_tokens` and
      `output_config.effort` is its configured effort.
  - No `temperature`, `thinking` or `response_format` key appears.
  - A temperature for a `temperature: false` model raises `ValueError`.
  - An unconfigured model keeps `max_completion_tokens`.

  Property: over random message lists, only cache-point messages carry `cache_control`,
  and the text is unchanged. Oracle: requirement 2.
- **C3**: Anthropic calls and replies.
  - `invoke` calls `messages.stream` once with the params and returns the final message;
    `messages.create` is never touched.
  - `parse` joins the text blocks, skips thinking blocks, and maps usage (cache read and
    creation as in `llm-provider-profiles`).
  - A refusal raises `LLMRefusalError`, and an empty reply raises `ValueError`.

  Property: over random sequences of text and thinking blocks, `content` is the joined
  text. Oracle: requirement 3.
- **C4**: refusals in the gateway. An adapter whose `parse` raises `LLMRefusalError`:
  - is called exactly once (no retry), even with `max_attempts=3`;
  - the `llm_call` event has `outcome: "refusal"`;
  - the error reaches the caller.

  The transient table in requirement 5 holds for both adapters' `is_transient`.
  Metamorphic: the same adapter raising a transient error is retried up to `max_attempts`.
  Oracle: requirements 4 and 5.
- **C5**: OpenAI routing and the Responses request.
  - With no cache point, `build_params` returns today's Chat Completions kwargs (unchanged
    from `main`), and `invoke` calls `chat.completions.create` only.
  - With a cache point, `build_params` returns exactly the dict in requirement 7, and
    `invoke` calls `responses.create` only.
  - A dict `response_format` with a cache point raises `ValueError`.

  Property: over random messages and marker positions, only marked parts carry
  `prompt_cache_breakpoint`, and roles map as in requirement 7. Oracle: requirement 7 and
  OpenAI's prompt-caching guide (2026-10, GPT-5.6+ breakpoints).
- **C6**: OpenAI Responses replies. `parse` maps the fields as in requirement 8, including
  missing details, an incomplete reply and an empty reply. A gateway call on
  `gpt-5.6-sol` with a fake adapter reporting 1,000 written and 9,000 cached of 100,000
  prompt tokens records `cost_usd` at the committed write, read and input rates.
  Property: cost over random usages equals the closed form. Oracle: requirement 8 and
  `llm-provider-profiles`' cost rule.
- **C7**: the long-context arm.
  - A call's messages are system, then a cache-point user message containing the
    `json.dumps` filing text, then a user message containing the question.
  - Only the second message is marked.
  - The question isn't in the first two messages.
  - The registry resolves `long_context` at `v2`.

  Property, **shared prefix:** for two cases on the same filing with different
  questions, messages 1 and 2 are identical. Oracle: requirement 9.
- **C8**: one retry layer. With dummy keys, `get_client()` of both adapters returns a
  client whose `max_retries` is 0. No request is made. Oracle: requirement 6.

## Interface

New (stubbed in the spec PR), in `src/llm/gateway/adapters/base.py`:

- `class LLMRefusalError(ValueError)`: the provider declined to answer.
- `cache_point_count(messages: list[dict]) -> int`

Behaviour on existing code, which the implementer adds:

- `AnthropicAdapter`: `build_params` (requirement 2), `invoke` (streaming), `parse`
  (refusals, thinking blocks), and a client with `max_retries=0`.
- `OpenAIAdapter`: the Responses path in `build_params`, `invoke` and `parse`; the Chat
  path unchanged; a client with `max_retries=0`.
- `LLMGateway.complete`: the cache-point cap, the no-retry refusal path and the
  `outcome: "refusal"` event.
- The long-context arm's three-message layout and `v2` prompts, with the registry default
  moved to `v2`. `StageLLM` (or the arm) can send a list of user parts.
- `anthropic==0.109.1` pinned in the three requirements files.
- Docs: `docs/design/XbrlRag_LLM_Harness.md` (caching and refusals) and `src/llm/README.md`.

## Notes

- **Live checks, required in the implementation PR and not in spec tests:**
  - **gpt-5.6-sol:** a 10-case long-context pilot on 4 filings (2–3 questions each), as
    on 2026-10-21. The second and later questions on a filing must show cached tokens
    > 0. Report real tokens, writes, reads and cost, and the projection for 60 cases.
  - **Claude Opus 5.5:** the same 10 cases with `--model claude-opus-5-5`.
  - **The `anthropic` profile:** a 5-case XbrlRag slice confirming streaming, effort and
    refusal handling work end to end.
- **Retired tests.** The long-context-arm tests read each message's `content` as a
  string and join the user messages. A cache-point **message** (not a content block)
  keeps them valid: the joined user text is still the filing followed by the question.
  One retired assertion changes in this spec PR: in
  `test/spec/long-context-arm/test_lc_c6_model_selection.py`, the registry check now
  expects `long_context` at `v2`. The implementer runs all of `test/spec`.
- **Not in this change:** a 12-hour cache lifetime (2.5× write rate), Anthropic's
  1-hour cache, OpenAI JSON-schema output, and Anthropic structured outputs.
- **Oracles:** hand-built messages, replies and usage; OpenAI's prompt-caching guide and
  Anthropic's API reference for request shapes. No network.
