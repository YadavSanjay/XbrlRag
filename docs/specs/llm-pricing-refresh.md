# llm-pricing-refresh: Sonnet 5.5 cache reads and gpt-6-luna's long-context tier

## Requirement

Two rows of `configs/llm/pricing.yaml` differ from the providers' published prices (the harness
re-audit of 2026-11-14, `docs/harness/REAUDIT.md`). The rate card must state them as published,
so the gateway's per-call cost is neither overstated nor understated:

1. **`claude-sonnet-5-5` cache reads** cost $0.10 per 1M tokens, 0.05× its $2.00 input rate.
   The committed row has `cached_input: 0.0002` per 1,000 tokens ($0.20 per 1M, 0.1×), which
   overstates every Sonnet call that reads the prompt cache. The other Sonnet rates ($2.00 input,
   $2.50 five-minute cache write, $10.00 output) are unchanged. The preregistered measurement
   plan already lists Sonnet 5.5 cache reads at $0.10 (`docs/eval/heldout_v2/measurement_plan.md`,
   the price table in §5); the config now matches it.
2. **`gpt-6-luna` long-context tier.** A prompt over 272,000 input tokens pays, on the whole call,
   $0.20 input, $0.02 cached input, $0.25 cache write (30-minute) and $0.75 output per 1M tokens.
   The committed row has no tier, so such a call would be understated. The base rates ($0.10 /
   $0.01 / $0.125 / $0.50) are unchanged. The tier follows the existing tier rule (requirement 6
   of `llm-provider-profiles`): a prompt of exactly 272,000 tokens pays the base rates.

No other row changes. Only configuration changes: `cost_usd` and the gateway already apply
`cached_input` and `tiers` as written.

This change updates four tests of retired specs that asserted the old values, each marked with
a comment naming this change: `llm-provider-profiles` C5 (`COMMITTED_RATES` for Sonnet) and C6
(the Sonnet gateway cost), and `openai-6-1-profile` C1 (`gpt-6-luna` had no tier; the hand-cost
table now carries its tier).

## Acceptance criteria

- **C1**: the committed `claude-sonnet-5-5` row is input 0.002, cached_input 0.0001,
  cache_write 0.0025 and output 0.01 per 1,000 tokens within abs 1e-12, so cache reads are
  0.05× input; the cost of any call on that row equals the published per-1M rates applied by
  hand within rel 1e-12. Oracle: Anthropic pricing page
  (platform.claude.com/docs/en/about-claude/pricing, "Model pricing", Claude Sonnet 5.5: $2 base
  input, $2.50 5-minute cache write, $0.10 cache hit, $10 output per MTok), read 2026-11-14.
- **C2**: the committed `gpt-6-luna` row keeps its base rates (0.0001 / 0.00001 / 0.000125 /
  0.0005 per 1,000 tokens) and has exactly one tier, `above_prompt_tokens` 272000, with input
  0.0002, cached_input 0.00002, cache_write 0.00025 and output 0.00075, within abs 1e-12; a call
  of exactly 272,000 prompt tokens costs the base rates and one of 272,001 the tier's rates on
  the whole call, within rel 1e-12. Oracle: OpenAI pricing page
  (developers.openai.com/api/docs/pricing, Standard, "Flagship models": gpt-6-luna short context
  $0.10 / $0.01 / $0.125 (30m write) / $0.50, long context, over 272K input tokens, $0.20 / $0.02
  / $0.25 / $0.75 per 1M), read 2026-11-14.

## Interface

No new interface. `src.llm.profiles.cost_usd` and `load_committed_settings(...).pricing_table()`
are the existing readers the tests use.

## Notes

- Prices are list (Standard) prices: runs may go on OpenAI's Flex tier, but the rows stay at
  Standard so costs and caps are never understated (`configs/llm/pricing.yaml` header).
- The other rows the re-audit found incomplete (`gpt-5.6-terra` and `gpt-5.6-luna` long-context
  tiers, `gpt-6-sol`'s cache write and tier) belong to profiles that are not run; they are left
  for the change that retires those profiles.
- Effect on recorded runs: a run stores its cost when it runs, so Anthropic-arm runs recorded
  before this change carry the higher Sonnet cache-read cost. A comparison of cost across runs
  from both sides of this change states it.
