# llm-provider-profiles — provider profiles, an XbrlRag frontier tier and complete pricing

First of two changes that let the whole XBRL flow run on OpenAI (the default) or Anthropic
Claude. The design follows the sister project faultline's multi-provider work
(`llm-provider-profiles`, `llm-anthropic-backend`), adapted to XbrlRag's tiers. The second
change, `llm-anthropic-and-caching`, completes the Claude adapter and adds explicit prompt-cache
points.

Today model choice is Python constants in `src/llm/config.py`:
- two OpenAI model sets behind `XBRLRAG_USE_REPLACEMENT_MODELS`, read once at import;
- an Anthropic alias table no XBRL stage can reach;
- a pricing table with no cache-write rate, where an unpriced model silently costs $0, so
  the run's cost cap never trips for it.

Every XBRL stage also hard-requires `OPENAI_API_KEY` and looks its model up in an
OpenAI-only config table, so a Claude model fails at construction.

## Requirement

1. **Committed, validated config.** Model choice and prices live in two committed files,
   `configs/llm/llm.yaml` and `configs/llm/pricing.yaml`. They are loaded by
   `load_llm_settings(doc, pricing, *, profile=None, environ=None)` into an `LlmSettings`.
   Any unknown key at any level, a missing required key, or a value of the wrong type
   raises `LlmConfigError`. `llm.yaml` has exactly these top-level keys:
   - `active_profile`: a string that may hold `${VAR}` / `${VAR:-default}` placeholders,
     expanded from `environ` (`os.environ` when `None`). The committed value is
     `"${XBRLRAG_LLM_PROFILE:-}"`, so an unset variable expands to empty.
   - `providers`: name → `{api_key_env: str}`. The names allowed are `openai` and
     `anthropic`.
   - `models`: model ID → a row with these fields:
     - `provider`: a key of `providers`;
     - `max_output_tokens`: an int > 0;
     - `temperature`: a bool, default true. False means the model rejects sampling
       parameters;
     - `effort` (optional): one of `low`, `medium`, `high`, `xhigh`, `max`. Allowed only
       on `anthropic` models.
   - `profiles`: profile name → `{small, medium, frontier}`, each a model ID in `models`.
     All three tiers are required, and any other key raises.
2. **The profiles.** The committed profiles are exactly:

   | profile | small | medium | frontier |
   |---|---|---|---|
   | `openai` | `gpt-5-nano` | `gpt-5-mini` | `gpt-5` |
   | `openai-5.6` | `gpt-5.6-luna` | `gpt-5.6-terra` | `gpt-5.6-sol` |
   | `anthropic` | `claude-haiku-5-5` | `claude-sonnet-5-5` | `claude-opus-5-5` |

   `openai` keeps today's models, so XbrlRag's measured baseline stays comparable. The
   frontier tier is XbrlRag's: escalation climbs to it, and the frontier models for the
   benchmark's long-context comparison are `gpt-5.6-sol` and `claude-opus-5-5`. The Claude
   models have `temperature: false`.
3. **Which profile is active.** The first of these that applies wins:
   1. an explicit `profile` argument (a run's `--llm-profile`);
   2. a non-empty expanded `active_profile` (`XBRLRAG_LLM_PROFILE`);
   3. the legacy switch: `openai-5.6` when `XBRLRAG_USE_REPLACEMENT_MODELS` reads on, under
      the existing `replacement_models_enabled` rules;
   4. `openai`.

   An unknown profile name raises `LlmConfigError`.
4. **Stage → model.** Each stage keeps its tier from `XBRL_STAGE_TIERS`. Its model is the
   active profile's model for that tier:
   - `model_for_stage(stage)`, for every stage in `XBRL_STAGE_TIERS`;
   - an unknown stage raises `LlmConfigError`.

   Escalation follows the active profile's tiers.
5. **Complete pricing.** `pricing.yaml` maps model ID → a rate row in USD per 1,000 tokens:
   - `input` and `output`, required;
   - `cached_input` and `cache_write`, optional;
   - `tiers`, optional: a list of `{above_prompt_tokens: int > 0, input, output,
     cached_input?, cache_write?}`.

   The row must satisfy:
   - every model any profile uses has a row, or loading raises `LlmConfigError` naming the
     model;
   - every model in today's `MODEL_PRICING` keeps its row and rates;
   - rates are finite and ≥ 0, and unknown keys raise.

   `MODEL_PRICING` in `src/llm/config.py` stays importable and is read from this file.
6. **Cost of one call.** `cost_usd(rates, *, prompt_tokens, completion_tokens,
   cached_prompt_tokens=0, cache_write_tokens=0)` returns

   `(uncached × input + cached × cached_input + written × cache_write + completion × output) / 1000`

   where:
   - `cached` and `written` are counts out of `prompt_tokens`, and `uncached` is the rest;
   - a missing `cached_input` or `cache_write` rate falls back to `input`;
   - the applicable rates are those of the tier with the largest `above_prompt_tokens`
     below `prompt_tokens`, or the base row when none applies;
   - negative counts, or `cached + written > prompt_tokens`, raise `ValueError`.
7. **No $0 calls.** The gateway's cost tracker prices every call with `cost_usd`. A model
   with no pricing row raises `ValueError`; the cost tracker never logs a warning and
   records $0. Cache-write tokens are counted:
   - `ParsedResponse` gains `cache_write_tokens`;
   - the OpenAI adapter reads it from the usage block;
   - the Anthropic adapter maps `cache_creation_input_tokens` to it;
   - the gateway passes it to the cost and records it on the `llm_call` event.
8. **Keys per provider.** `api_key(env_var, environ=None)` returns the variable's value,
   or else the stripped contents of the file named by `<env_var>_FILE`. If neither is set,
   it raises `LlmConfigError` naming the variable. `missing_api_keys(settings,
   environ=None)` lists the variables for every provider the active profile uses that have
   no key.
9. **Stages don't assume OpenAI.** The five XBRL stages and the eval arms' shared
   `StageLLM` read the active profile **when constructed**, not only at import. The stages
   are `QueryDecomposer`, `IntentRouter`, `PlanGenerator`, `XbrlRagSynthesizer` and
   `XbrlFaithfulnessVerifier`.
   - Under `anthropic`, a stage built with only `ANTHROPIC_API_KEY` set uses the profile's
     Claude model for its tier, and doesn't raise.
   - A stage never sends `temperature` to a model with `temperature: false`.
   - Under `openai`, models and call parameters are what they are today.
10. **Compatibility.**
    - `openai_model_set`, `xbrl_stage_models`, `replacement_models_enabled` and
      `MODEL_TIERS` keep their behaviour.
    - `XBRL_QUERY_MODEL_TIERS` stays an import-time dict, equal to the active profile's
      stage models at import.
    - `run_suite.py` gains `--llm-profile` and records the profile in the run's config.

## Acceptance criteria

The oracles are the requirement text, the provider pricing pages and hand-built config
documents. Fakes are at the I/O boundaries only: a provider adapter behind a real
`LLMGateway`, and environment mappings.

- **C1**: loading and validation. The committed files load, and `load_llm_settings`
  raises `LlmConfigError` for each of:
  - an unknown key at each level;
  - a missing tier;
  - an unknown provider;
  - a profile model not in `models`;
  - a profile model with no pricing row (the error names it);
  - `effort` on an `openai` model;
  - an effort outside the allowed values;
  - a non-finite or negative rate;
  - `max_output_tokens ≤ 0`.

  Property: removing any one required key from a valid document raises. Oracle:
  requirement 1.
- **C2**: the committed profiles equal the table in requirement 2 exactly, and every Claude
  model in them has `temperature` false. Oracle: requirement 2.
- **C3**: precedence. Each row of requirement 3 wins over the rows below it, across all
  combinations of `profile`, `XBRLRAG_LLM_PROFILE` (unset, empty, or a name) and the legacy
  switch (unset, on, off), and an unknown name raises. Property: over random combinations,
  the active profile is the closed-form choice. Oracle: requirement 3.
- **C4**: stage → model.
  - For each profile and each stage in `XBRL_STAGE_TIERS`, `model_for_stage(stage)` is
    `profiles[profile][XBRL_STAGE_TIERS[stage]]`.
  - `model_for_tier` returns the table's model.
  - An unknown stage or tier raises.

  Oracle: requirements 2 and 4.
- **C5**: cost. `cost_usd` equals the closed form in requirement 6 on hand-built rows,
  including:
  - the fallbacks for missing `cached_input` and `cache_write`;
  - tier selection at, just below and just above a threshold;
  - the `ValueError` cases.

  The committed rates per 1,000 tokens are:

  | model | input | output | cached_input | cache_write |
  |---|---|---|---|---|
  | `claude-opus-5-5` | 0.004 | 0.02 | 0.0002 | 0.005 |
  | `claude-sonnet-5-5` | 0.002 | 0.01 | 0.0002 | 0.0025 |
  | `claude-haiku-5-5` | 0.0001 | 0.0005 | 0.00001 | 0.000125 |
  | `gpt-5.6-sol` | 0.004 | 0.02 | 0.0004 | 0.005 |

  `claude-haiku-5-5` also has a tier above 100,000 prompt tokens: 0.0005 / 0.0025 /
  0.00005 / 0.000625.

  Properties:
  - cost is linear in each token count;
  - moving tokens from uncached to cached never raises the cost when
    `cached_input ≤ input`.

  Exact to 1e-12 relative. Oracle: requirement 6; Anthropic's and OpenAI's pricing pages
  (2026-10), per 1M tokens ÷ 1000, with cache writes at 1.25× input.
- **C6**: no $0 calls.
  - A gateway call whose fake adapter reports `cache_write_tokens` records
    `cost_usd(rates, ...)` for the model, with the written tokens at the cache-write
    rate.
  - The `llm_call` event carries `cache_write_tokens`.
  - A call on a model with no pricing row raises `ValueError` and records no $0 cost.

  Oracle: requirement 7.
- **C7**: keys.
  - `api_key` reads the variable, then `<VAR>_FILE`, stripping whitespace.
  - It raises `LlmConfigError` naming the variable when neither is set.
  - `missing_api_keys` lists exactly the missing variables for the providers in use.

  Property: over random presence or absence of each source, the result is the closed form.
  Oracle: requirement 8.
- **C8**: stages.
  - With `XBRLRAG_LLM_PROFILE=anthropic` and only `ANTHROPIC_API_KEY` set, each of the five
    stage classes and `StageLLM` constructs without raising, and uses
    `profiles["anthropic"][tier]` for its stage's tier.
  - A call through a fake adapter sends no `temperature`.
  - With the profile `openai` and only `OPENAI_API_KEY` set, the models are today's and
    `temperature` is still sent.

  Oracle: requirement 9.
- **C9**: compatibility.
  - `openai_model_set(False)` and `openai_model_set(True)` equal the `openai` and
    `openai-5.6` profiles.
  - `XBRL_QUERY_MODEL_TIERS` equals the active profile's stage models at import.
  - `run_suite.py --llm-profile anthropic` records `"llm_profile": "anthropic"` in the run
    config, and an unknown name exits with an error.

  Oracle: requirement 10.

## Interface

New (stubbed in the spec PR), in `src/llm/profiles.py`:

- `class LlmConfigError(ValueError)`.
- `class LlmSettings`, with:
  - `active_profile` (property) `-> str`;
  - `model_for_tier(self, tier: str) -> str`;
  - `model_for_stage(self, stage: str) -> str`;
  - `provider_for(self, model: str) -> str`;
  - `temperature_allowed(self, model: str) -> bool`;
  - `effort_for(self, model: str) -> str | None`;
  - `rates_for(self, model: str) -> dict[str, Any]`;
  - `providers_in_use(self) -> set[str]`.
- `load_llm_settings(doc: Mapping[str, Any], pricing: Mapping[str, Any], *, profile: str |
  None = None, environ: Mapping[str, str] | None = None) -> LlmSettings`
- `load_committed_settings(*, profile: str | None = None, environ: Mapping[str, str] |
  None = None) -> LlmSettings`: reads `configs/llm/llm.yaml` and `pricing.yaml` under the
  project root.
- `cost_usd(rates: Mapping[str, Any], *, prompt_tokens: int, completion_tokens: int,
  cached_prompt_tokens: int = 0, cache_write_tokens: int = 0) -> float`
- `api_key(env_var: str, environ: Mapping[str, str] | None = None) -> str`
- `missing_api_keys(settings: LlmSettings, environ: Mapping[str, str] | None = None) ->
  list[str]`

Behaviour on existing code, which the implementer adds:

- `configs/llm/llm.yaml` and `configs/llm/pricing.yaml`. Pricing carries every current
  `MODEL_PRICING` row plus the new Claude rows and cache-write rates.
- `src/llm/config.py`:
  - `MODEL_PRICING` and `MODEL_TIERS` are read from the files;
  - `XBRL_QUERY_MODEL_TIERS` follows the active profile at import;
  - the existing functions keep their contracts.
- `ParsedResponse.cache_write_tokens`; both adapters fill it; `CostTracker` uses
  `cost_usd` and raises on an unpriced model; the gateway's `llm_call` event gains
  `cache_write_tokens`.
- The five stages and `StageLLM`: profile-driven model, provider-aware key, no
  `SEC_TABLE_MODEL_CONFIGS[model]` KeyError, no `temperature` for `temperature: false`
  models.
- `run_suite.py --llm-profile`. `.env.example` gains `XBRLRAG_LLM_PROFILE`,
  `ANTHROPIC_API_KEY` and their `_FILE` forms.
- Docs: `docs/design/XbrlRag_LLM_Harness.md` (model choice, profiles, pricing) and
  `src/llm/README.md`.

## Notes

- **Retired test updated in this spec PR.**
  `test/spec/long-context-arm/test_lc_c6_model_selection.py` uses `"claude-opus-5-5"` as
  its example of an unpriced model. This change prices it, so that example becomes an
  invented model ID.
- **Not in this change** (it's `llm-anthropic-and-caching`):
  - streaming Claude calls;
  - effort and thinking on the wire;
  - `refusal` handling;
  - pinning the `anthropic` SDK;
  - explicit cache points for both providers.

  Until then a Claude stage runs through today's Anthropic adapter, without effort.
- **Rates not locked by tests.** The gpt-5.6 terra and luna cache-write rates are 1.25×
  input.
- **No gpt-5.6-sol long-context tier, on purpose.** OpenAI prices prompts above 272K tokens
  at 2× input and 1.5× output, but this change doesn't record that tier, for two reasons:
  - the long-context arm excludes any filing over 250K estimated tokens, and real counts
    run 0.86–0.98 of the estimate, so no call reaches it;
  - the retired long-context test that doubles prompt tokens (up to 500K) assumes cost is
    linear.

  A later change can add the tier together with that test.
- **Retired test fixed in this spec PR.** `test/spec/model-set-switch/` has failed on `main`
  since baseline-arms added `closed_book` and `naive_rag` to `XBRL_STAGE_TIERS`. Its
  expected stage tables gain those two medium-tier stages.
- **Faultline differences, on purpose:**
  - profiles map XbrlRag's three tiers rather than each stage, because XbrlRag's stage →
    tier map already exists and escalation climbs tiers;
  - there is no `deepseek` provider;
  - the legacy replacement switch maps onto `openai-5.6`.
- **Oracles:** hand-built config documents and token counts; published pricing. No network,
  database or real keys.
