# deepseek-provider — DeepSeek as a third provider and the `deepseek` profile

v2 uses DeepSeek as its low-cost development model (`VISION.md`, "Boundaries & assumptions";
feature manifest `WS10-P1`): slices and development runs on DeepSeek, decisions on OpenAI
and Anthropic milestone runs. Today the provider configuration
([`llm-provider-profiles`](llm-provider-profiles.md)) allows only `openai` and `anthropic`,
and its C1 tests assert that a `deepseek` provider is rejected.

DeepSeek's API is OpenAI-compatible (`https://api.deepseek.com`, Chat Completions) with
three differences that matter here:
- the output budget is `max_tokens`; `max_completion_tokens` is not a documented parameter;
- reasoning ("thinking") is on by default and controlled by `reasoning_effort`
  (`none`, `low`, `high`, `max`; `minimal` maps to `low`, `medium` and `xhigh` to `high`);
- caching is automatic: there are no explicit cache points. Usage reports cache hits as
  `prompt_tokens_details.cached_tokens`, like OpenAI.

The gateway already has an `OpenAICompatAdapter` (an `OpenAIAdapter` on a `base_url`). This
change adds a DeepSeek provider to the configuration, routes its models to a DeepSeek
adapter built on it, and adds the `deepseek` profile. No stage, prompt or eval behaviour
changes for the existing profiles.

## Requirement

1. **Configuration** (`configs/llm/llm.yaml`, validated by `src/llm/profiles.py`):
   - A provider row may carry `base_url: str`. The allowed provider names are `openai`,
     `anthropic` and `deepseek`. Any other name still raises `LlmConfigError`.
   - `deepseek` **requires** `base_url`, and it must start with `https://`. `openai` and
     `anthropic` must **not** carry one (their SDK defaults apply). A violation raises
     `LlmConfigError`.
   - The committed file declares `deepseek: {api_key_env: DEEPSEEK_API_KEY, base_url:
     "https://api.deepseek.com"}`.
   - Models `deepseek-flash` (`max_output_tokens: 8000`, `effort: low`) and
     `deepseek-v4-pro` (`max_output_tokens: 16000`, `effort: high`), both on `deepseek`.
   - Profile `deepseek`: small `deepseek-flash`, medium `deepseek-flash`, frontier
     `deepseek-v4-pro`. The other profiles and `active_profile` are unchanged.
   - `effort` is allowed on `anthropic` **and** `deepseek` models, still not on `openai`
     models.
   - `LlmSettings.base_url_for(provider)` returns the provider's `base_url`, or `None`
     when it has none. An undeclared provider raises `LlmConfigError`.
2. **Prices** (`configs/llm/pricing.yaml`, USD per 1,000 tokens): DeepSeek's **peak**
   rates, so a cost cap computed from them is never too low (off-peak is half):

   | Model | `input` | `cached_input` | `output` |
   |---|---|---|---|
   | `deepseek-flash` | 0.0003 | 0.000006 | 0.0012 |
   | `deepseek-v4-pro` | 0.00132 | 0.000044 | 0.00396 |

   No `cache_write` (DeepSeek does not bill writes) and no `tiers`.
3. **Routing:**
   - `resolve_model_ref(m)` for a model `m` configured in `llm.yaml` on provider
     `deepseek` returns `provider == "deepseek"` and `base_url ==` that provider's
     `base_url`, unless the caller passes an explicit `provider` or `base_url`. For every
     other model the result is unchanged (e.g. `gpt-5-mini` → `openai`, `base_url is None`;
     an unlisted `gpt-4o` → `openai`).
   - `key_env_for("deepseek-flash", settings)` is `DEEPSEEK_API_KEY`;
     `missing_api_keys` lists `DEEPSEEK_API_KEY` for the `deepseek` profile when it is unset.
   - The gateway's default adapter for provider `deepseek` is a `DeepSeekAdapter` with the
     ref's `base_url` and the gateway's API key.
4. **Request parameters** (`DeepSeekAdapter.build_params`, same signature as
   `OpenAIAdapter.build_params`):
   - always a Chat Completions request: `model` and `messages`, never `input`, even when a
     message carries `"cache_point": True`;
   - every message is passed with its `cache_point` key removed and nothing else changed;
   - the output budget goes out as `max_tokens`; `max_completion_tokens` is never sent;
   - `temperature` is sent only when given;
   - `response_format="json"` → `{"type": "json_object"}`; a dict passes through;
   - `reasoning_effort`: the caller's value when given, else the model's configured
     `effort` from `llm.yaml`, else absent. The value is sent unchanged (the API maps
     `medium` and `xhigh` itself).
5. **Usage and cost:** a DeepSeek reply is parsed by the inherited `parse`: prompt,
   completion and reasoning tokens, and `cached_prompt_tokens` from
   `usage.prompt_tokens_details.cached_tokens`. `cost_usd` with the row above charges
   cached prompt tokens at `cached_input` and the rest at `input`.

## Acceptance criteria

- **C1**: The committed configuration loads with the DeepSeek provider, models, profile and
  prices of requirements 1 and 2, prices within `abs=1e-12`.

  Point cases:
  - `load_committed_settings(profile="deepseek", environ={})` gives tier models
    `{small: deepseek-flash, medium: deepseek-flash, frontier: deepseek-v4-pro}`;
  - `base_url_for("deepseek") == "https://api.deepseek.com"`;
    `base_url_for("openai") is None`;
  - `effort_for("deepseek-flash") == "low"`, `effort_for("deepseek-v4-pro") == "high"`;
  - `rates_for("deepseek-flash") == {input: 0.0003, cached_input: 0.000006,
    output: 0.0012}` and likewise for `deepseek-v4-pro`;
  - `providers_in_use()` is `{"deepseek"}` for the `deepseek` profile.

  Oracle: DeepSeek's Models & Pricing page (USD, retrieved 2026-10-29): `deepseek-flash`
  peak $0.006 / $0.30 / $1.20 and `deepseek-v4-pro` peak $0.044 / $1.32 / $3.96 per 1M
  tokens (cache hit / cache miss / output), divided by 1,000.
- **C2**: Invalid DeepSeek configurations raise `LlmConfigError`:
  - `deepseek` without `base_url`; with `base_url: "http://..."`;
  - `base_url` on `openai` or on `anthropic`;
  - a provider named anything outside the three (e.g. `mistral`), and a model on it;
  - `effort` on an `openai` model (still), while `effort` on a `deepseek` model loads.

  Property: for any of the three names and any `base_url` value (absent, `https://...`,
  `http://...`), loading succeeds exactly when the rule of requirement 1 allows it.

  Oracle: requirement 1.
- **C3**: Routing follows the configured provider.

  Point cases: `resolve_model_ref("deepseek-flash")` → provider `deepseek`, base_url
  `https://api.deepseek.com`; `resolve_model_ref("deepseek-flash", base_url="https://x")`
  → base_url `https://x`; `resolve_model_ref("gpt-5-mini")` → `openai`, no base_url;
  `resolve_model_ref("gpt-4o")` → `openai`; `key_env_for("deepseek-v4-pro", settings)` is
  `DEEPSEEK_API_KEY`; an `LLMGateway`'s default adapter for a `deepseek` ref is a
  `DeepSeekAdapter` whose `base_url` is the ref's.

  Property: for every model in the committed `llm.yaml`, `resolve_model_ref(m).provider`
  is `deepseek` exactly when its row's provider is `deepseek`, and otherwise equals
  `infer_provider(m)` (today's rule).

  Oracle: requirement 3 and the committed `llm.yaml`.
- **C4**: `DeepSeekAdapter.build_params` produces the request of requirement 4.

  Point case: two messages, the first with `"cache_point": True`; `model="deepseek-flash"`,
  `max_completion_tokens=900`, `temperature=None`, `response_format="json"`,
  `reasoning_effort=None` → exactly `{model, messages (no cache_point key), max_tokens: 900,
  response_format: {"type": "json_object"}, reasoning_effort: "low"}`. With
  `reasoning_effort="high"`, `reasoning_effort` is `"high"`.

  Property: for any list of messages (with or without `cache_point`) and any budget, the
  params contain no `input` and no `max_completion_tokens`, `max_tokens` equals the
  budget, and the messages equal the inputs with `cache_point` removed, in order.

  Oracle: DeepSeek's Chat Completions reference (retrieved 2026-10-29) and requirement 4.
- **C5**: A DeepSeek-shaped reply is parsed and costed.

  Point case: a reply (a plain object) with `prompt_tokens=1000`, `completion_tokens=500`,
  `prompt_tokens_details.cached_tokens=400`,
  `completion_tokens_details.reasoning_tokens=120` → `ParsedResponse` with those counts
  and `cached_prompt_tokens=400`; `cost_usd(rates_for("deepseek-flash"), prompt_tokens=1000,
  completion_tokens=500, cached_prompt_tokens=400)` is
  `0.6 × 0.0003 + 0.4 × 0.000006 + 0.5 × 0.0012 = 0.0007824`, within `abs=1e-12`.

  Property: for any token counts with `cached ≤ prompt`, the cost equals
  `((prompt − cached) × input + cached × cached_input + completion × output) / 1000`.

  Oracle: requirement 5, worked by hand.

## Interface

- `src.llm.profiles.LlmSettings.base_url_for(self, provider: str) -> str | None`
- `src.llm.gateway.adapters.deepseek_adapter.DeepSeekAdapter(OpenAICompatAdapter)`, with
  `build_params(self, *, messages: list[dict], model: str, max_completion_tokens: int | None = None, temperature: float | None = None, response_format: str | dict | None = None, reasoning_effort: str | None = None, extra: dict | None = None) -> dict`
- Behaviour changes, no signature change: `resolve_model_ref`, `LLMGateway._build_default_adapter`,
  the `llm.yaml` schema (`base_url`, the `deepseek` name, `effort` on `deepseek`).

## Notes

- **Retired tests updated in this spec PR** (`llm-provider-profiles`), each with a comment
  naming `deepseek-provider`: the C1 cases that used `deepseek` as the forbidden provider
  now use `mistral`, and the cases that pin the committed providers (`{openai, anthropic}`)
  or the profile list include `deepseek`.
- **No network in tests.** Replies are plain objects; no test calls DeepSeek. The first
  live call is the operator's one-case smoke per stage (root `CLAUDE.md`, "Before any run
  that spends money"), with `DEEPSEEK_API_KEY` in `.env`.
- **JSON mode:** DeepSeek requires the word "json" in the prompt for `json_object`. Every
  query-stage template asks for JSON; the smoke run confirms it.
- **Off-peak pricing** (half the peak rate, outside 01:00–04:00 and 06:00–10:00 UTC on
  weekdays) is not modelled: the tracked cost is an upper bound.
- **Served model:** DeepSeek moves `deepseek-flash` between versions (now
  DeepSeek-V4.1-Flash-1017); `served-model-id` records what each reply reports.
- **Out of scope:** DeepSeek's Anthropic-compatible endpoint, its Responses API, the
  `thinking` switch (effort `none` is not used), tool calls, and US-hosted DeepSeek models.
