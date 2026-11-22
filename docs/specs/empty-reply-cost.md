# empty-reply-cost — a billed reply with no content is still recorded

When a provider returns a reply with no content, the adapters raise `ValueError("LLM returned empty
message content")` before they read its usage. The gateway then logs an `llm_call` with no tokens
and no cost, and adds nothing to the trace. The provider still bills the call. A reasoning model
that spends its whole output budget on reasoning does exactly this, so the calls most likely to be
expensive are the ones that vanish.

The `html_rag` pilot lost two such calls on `deepseek-flash`. `configs/llm/pricing.yaml` says "a
cost cap is never understated". With this fault, it can be.

A provider refusal already works this way round: the error carries the usage and the gateway
records it ([llm-refusals-and-store](llm-refusals-and-store.md)). This change gives an empty reply
the same accounting. It stays an error.

## Requirement

1. **The error does not change.** An empty reply raises a `ValueError` with the same message, is
   not retried, and still advances the run's consecutive-failure count.
2. **The tokens are recorded.** The `llm_call` event of an empty reply, still `outcome="error"`,
   holds `prompt_tokens`, `completion_tokens`, `total_tokens`, `cached_prompt_tokens`,
   `cache_write_tokens`, `reasoning_tokens` and `finish_reason`, read from the reply.
3. **The cost is recorded.** The call is priced as a successful one is, and the cost is on the
   event, in the trace's totals and in the `CostTracker`.
4. **It counts towards the run.** `trace.llm_calls` and the trace's token and cost totals include
   the call, so the run budget checked before the next call sees it.
5. **All three reply shapes.** Chat Completions (OpenAI-format providers, DeepSeek), Anthropic
   Messages and OpenAI Responses.
6. **A reply with no usage records zeros.** It raises the same error, and the call is counted with
   0 tokens and 0 cost.
7. **Nothing else changes.** Not a successful call, a refusal, a content-filter block or a
   transport error, and not the tool-turn parsers.

**In scope:** requirements 1-7. **Out of scope:** retrying or escalating an empty reply; the size of
the output budget ([stage-llm-output-cap](stage-llm-output-cap.md)); audit rows for the baseline arms.

## Acceptance criteria

Bug-fix change: no new function and no stub. Every test fails on the code at the base of this
branch with an assertion.

- **C1**: A DeepSeek reply with 8,000 prompt tokens of which 1,000 cached, 2,000 completion tokens
  and no content adds 0.004506 USD to the trace and the tracker, and raises the same error.
  Property: for any counts, the cost is the closed form over the committed rates. Oracle: a hand
  sum over `configs/llm/pricing.yaml`, and requirement 3.
- **C2**: That call's event carries `outcome="error"`, the tokens, `finish_reason="length"` and the
  cost; it was attempted once under `max_attempts=3`; `trace.llm_calls` and
  `consecutive_failures` each rise by 1. A reply with no usage counts as one call at 0 tokens and 0
  cost. Property: for any counts, the event's tokens equal the reply's. Oracle: requirements 1, 2, 4
  and 6.
- **C3**: The same holds for an Anthropic reply and an OpenAI Responses reply with no content.
  Metamorphic: doubling the tokens doubles the recorded cost on each shape. Oracle: requirement 5
  and the committed rates.
- **C4**: With `max_cost_usd` below the first empty reply's cost, the next call in that trace halts
  with `RunBudgetExceeded` before reaching the provider; above it, the next call goes out.
  Metamorphic: the same limit either side of the cost decides it. Oracle: requirement 4.

## Interface

No public change: `LLMGateway.complete(...)` as today. How the usage travels from adapter to gateway
is the implementer's choice; a `ValueError` subclass carrying it, as `LLMRefusalError` does, keeps
requirement 1.

## Notes

- Evidence, 2026-11-01: two `html_rag` calls with `outcome="error"`, `cost_usd=None`, about 12 s
  each, both after `LLM response truncated: finish_reason=length`. The case's `cost_usd` was the
  router's alone, $0.0004 to $0.0005.
- DeepSeek rates in the C1 sum, per 1,000 tokens: input 0.0003, cached 0.000006, output 0.0012.
  7 x 0.0003 + 1 x 0.000006 + 2 x 0.0012 = 0.004506.
- `pricing.yaml` tracks peak rates, so a tracked figure is an upper bound of the bill on
  off-peak hours. That stays true once this change is in; today it is not true of these calls.
- The gateway, the tracker, the trace and each adapter's `parse` are real. Only `get_client` and
  `invoke` are replaced: they are the network boundary.
- `src/llm/escalation.py` treats this `ValueError` as bad output, and a subclass keeps that.
