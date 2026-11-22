# openai-6-1-profile — the gpt-6.1 profile, its prices, Flex and the long-context timeout

The v2 looks run on gpt-6.1 at OpenAI's Flex tier
([measurement plan](../eval/heldout_v2/measurement_plan.md) §1 decisions 1 and 10, §5, §8 and
§12 items 1–3). Before the first gpt-6.1 dollar the repo needs:

- price rows for `gpt-6.1-sol` and `gpt-6.1-terra`, and the missing cache-write price of
  `gpt-6-luna`; a wrong row would misstate every cost and every cost cap without anyone
  noticing;
- an `openai-6.1` profile;
- a per-run `service_tier`, sent on every OpenAI call and recorded with the run, so a run
  meant for Flex cannot silently run at Standard (or the reverse) unrecorded;
- a longer request timeout for the long-context stages, whose Flex calls can take minutes;
- `gpt-6-luna` as the milestone judge, replacing `gpt-5.6-luna`.

**Pricing stays at the Standard tier** (plan §5, "Flex"): the gateway records list price, so
the caps stay conservative and per-question costs are list-price costs, even on a Flex run.
Flex halves every token class; that is disclosed, not modelled.

The default `openai` profile (gpt-5, which shuts down 2026-12-11) is **not** changed here.
429 handling during Flex runs (plan §10.5) belongs to `eval-run-checkpoint`, not this change:
the gateway's retry loop and circuit breaker are untouched.

## Requirement

1. **Prices** (`configs/llm/pricing.yaml`, USD per 1,000 tokens = list price per 1M / 1000),
   in the existing schema: the long-context rates are a `tiers` entry with
   `above_prompt_tokens: 272000`, exactly as `gpt-5.6-sol`'s, so a prompt over 272,000 tokens
   pays the tier's rates on the whole call.

   | Model | `input` | `cached_input` | `cache_write` | `output` | tier above 272,000: input / cached / write / output |
   |---|---|---|---|---|---|
   | `gpt-6.1-sol` | 0.002 | 0.0001 | 0.0025 | 0.01 | 0.004 / 0.0002 / 0.005 / 0.015 |
   | `gpt-6.1-terra` | 0.001 | 0.00005 | 0.00125 | 0.005 | 0.002 / 0.0001 / 0.0025 / 0.0075 |
   | `gpt-6-luna` | 0.0001 (unchanged) | 0.00001 (unchanged) | **0.000125 (new)** | 0.0005 (unchanged) | none |

   Every other row is unchanged. `MODEL_PRICING` (src/llm/config.py) exposes the same rates.
2. **Models and profile** (`configs/llm/llm.yaml`):
   - model rows, all `provider: openai`, no `effort`, `temperature` left at its default:
     `gpt-6-luna` (`max_output_tokens: 4000`), `gpt-6.1-terra` (8000), `gpt-6.1-sol` (8000),
     the ceilings of the gpt-5.6 rows of the same size;
   - profile `openai-6.1`: small `gpt-6-luna`, medium `gpt-6.1-terra`, frontier `gpt-6.1-sol`;
   - `openai`, `openai-5.6`, `anthropic`, `deepseek` and `active_profile` are unchanged; with no
     explicit profile and an empty environment the active profile is still `openai`.
   - Per-call parameters as for the other OpenAI reasoning models: `SEC_TABLE_MODEL_CONFIGS`
     gains `gpt-6.1-sol` and `gpt-6.1-terra` (`max_completion_tokens` 8000, `temperature` 1),
     so `call_params(resolve_model_ref(m).config, ...)` gives each openai-6.1 model its
     `max_output_tokens` as the budget and temperature 1, whatever the caller's defaults
     (as `gpt-6-luna`, `gpt-6-sol` and the gpt-5.6 models already do).
3. **Service tier.** `src.llm.request_options.openai_service_tier(environ=None)` reads
   `XBRLRAG_OPENAI_SERVICE_TIER` (from `os.environ` when *environ* is None):
   - unset, empty or only whitespace → `None` (no tier requested, the provider's default);
   - otherwise the value with surrounding whitespace stripped, which must be one of
     `auto`, `default`, `flex`, `priority` (case-sensitive, as the API takes it); anything
     else raises `LlmConfigError`.
4. **The OpenAI adapter sends it on every call.** With a tier set, `OpenAIAdapter`
   (provider `openai`) puts `"service_tier": <tier>` in every request it builds:
   `build_params` on the Chat Completions path, `build_params` on the Responses path (a
   request with a cache point) and `build_tool_params`. With no tier set the request has no
   `service_tier` key, so it is exactly today's request. The value is read at each build, not
   when the adapter is constructed. `OpenAICompatAdapter`, `DeepSeekAdapter` and
   `AnthropicAdapter` never send `service_tier` (Flex is an OpenAI-platform tier). An invalid
   value raises `LlmConfigError` from the OpenAI adapter's build, before any provider call.
5. **The long-context stages get a 15-minute request timeout.**
   `src.llm.request_options.request_timeout_s(stage)` returns `900.0` for the stages
   `long_context` (the oracle and resolved arms), `long_context_multi` and
   `frontier_sec_tools`, and `None` for every other stage. `LLMGateway.complete` and
   `LLMGateway.complete_tools` add `"timeout": <seconds>` to the request params when it is
   not None, for every provider (both SDKs take a per-request `timeout`); otherwise the
   params carry no `timeout` key and the SDK's client default applies, as today. The value
   reaches the adapter's `invoke`, i.e. the SDK call.
6. **The run records the tier.** `EvalRunner.run_many` (and so `run`, `run_suite` and
   `run_graded_many`) sets `report.config["service_tier"] = openai_service_tier()` on every
   report it creates, read once when the run starts: `None` when no tier is set. An invalid
   value raises `LlmConfigError` before any case is answered. The run config is persisted with
   the run (`eval_run.config`), beside `llm_profile`.
7. **`scripts/eval/run_suite.py --service-tier {auto,default,flex,priority}`** sets
   `XBRLRAG_OPENAI_SERVICE_TIER` for the duration of the run and restores the previous value
   afterwards, as `--llm-profile` does with `XBRLRAG_LLM_PROFILE`. Without the flag the
   environment's value (or its absence) applies unchanged. Any other value is a usage error
   (exit status 2) before any case is answered.
8. **Judge** (`configs/eval/judge.yaml`): `milestone_models` is exactly `[gpt-6-luna]`;
   `development_model` stays `claude-haiku-5-5`. The rule that a judge is never from the
   generator's model family is unchanged: `check_judge_family("gpt-6-luna", "anthropic")`
   passes and `check_judge_family("gpt-6-luna", "openai-6.1")` raises `ValueError`.

## Acceptance criteria

- **C1**: The committed prices of requirement 1, within `abs=1e-12`, and the costs they give.

  Point cases:
  - `rates_for("gpt-6.1-sol")` is the row and its one tier as in the table; likewise
    `gpt-6.1-terra`; `rates_for("gpt-6-luna")` has `cache_write == 0.000125`, no `tiers`, and
    its other three rates unchanged; `MODEL_PRICING` holds the same values.
  - `cost_usd(rates_for("gpt-6.1-sol"), prompt_tokens=100_000, cached_prompt_tokens=60_000,
    cache_write_tokens=10_000, completion_tokens=2_000)` is
    `(30_000 × 2 + 60_000 × 0.10 + 10_000 × 2.50 + 2_000 × 10) / 1e6 = 0.111`.
  - The same call at `prompt_tokens=300_000, cached=200_000, written=50_000,
    completion=4_000` is `(50_000 × 4 + 200_000 × 0.20 + 50_000 × 5 + 4_000 × 15) / 1e6 = 0.55`.
  - At exactly 272,000 prompt tokens the base rates apply; at 272,001 the tier's.

  Property: for any token counts (cached + written ≤ prompt), each new model's cost equals
  the table's per-1M rates applied by hand, the tier chosen by `prompt > 272_000`.
  Metamorphic: within one tier, doubling every count doubles the cost.

  Oracle: the OpenAI pricing page (`developers.openai.com/api/docs/pricing`, Standard, read
  2026-11-03), as transcribed in the measurement plan §5 and §12 item 1.
- **C2**: The committed `openai-6.1` profile and model rows of requirement 2.

  Point cases: `load_committed_settings(profile="openai-6.1", environ={})` gives tier models
  `{small: gpt-6-luna, medium: gpt-6.1-terra, frontier: gpt-6.1-sol}`, `providers_in_use()
  == {"openai"}`, each model's `max_output_tokens` as in requirement 2, `effort_for` None and
  `temperature_allowed` True; `load_committed_settings(environ={})` is still `openai` with
  `{gpt-5-nano, gpt-5-mini, gpt-5}`; `openai-5.6`, `anthropic` and `deepseek` keep their
  models; `missing_api_keys` for `openai-6.1` with an empty environment is
  `["OPENAI_API_KEY"]`; `call_params` gives each openai-6.1 model its ceiling and
  temperature 1 for caller defaults `(16_000, 0.0)`.

  Property: for every XBRL stage, the `openai-6.1` model is the profile's model for the
  stage's tier in `XBRL_STAGE_TIERS`; for any caller default budget and temperature,
  `call_params` for an openai-6.1 model is `{max_completion_tokens: ceiling, temperature: 1}`.

  Oracle: requirement 2 (the measurement plan's decision 1).
- **C3**: `openai_service_tier` follows requirement 3.

  Point cases: unset, `""` and `"  "` → None (each together with a set value in the same
  test); `"flex"`, `" flex "` → `"flex"`; each of `auto`, `default`, `priority` → itself;
  `"FLEX"`, `"batch"`, `"scale"`, `"standard"` → `LlmConfigError`.

  Property: for any string, the result is the stripped string when it is one of the four,
  None when it strips to empty, and `LlmConfigError` otherwise.

  Oracle: requirement 3; OpenAI's API reference (`service_tier`: `auto`, `default`, `flex`,
  `priority`) and flex-processing guide.
- **C4**: The adapters send the tier as requirement 4 says.

  Point cases, with `XBRLRAG_OPENAI_SERVICE_TIER=flex`: `OpenAIAdapter.build_params` without
  a cache point has `service_tier == "flex"` and is otherwise the request without the
  variable; with a cache point (the Responses path) likewise; `build_tool_params` likewise.
  Unset: no `service_tier` key on any of the three. `OpenAICompatAdapter.build_params`,
  `DeepSeekAdapter.build_params` and `.build_tool_params`, and `AnthropicAdapter.build_params`
  have no `service_tier` with the variable set. `"bogus"` → `LlmConfigError` from
  `OpenAIAdapter.build_params`. Through a real `LLMGateway` whose OpenAI adapter's client is
  a fake recording the SDK call's kwargs, `complete` with the variable `flex` sends
  `service_tier="flex"` to `chat.completions.create`, and a `complete` with a cache point
  sends it to `responses.create`; the variable is changed between two calls on the same
  gateway and each call carries the value current at its build.

  Property: for any of the four tiers or unset, and either path, `service_tier` is in the
  params exactly when set, equal to it, and the remaining keys equal the unset request's.

  Oracle: requirement 4.
- **C5**: The long-context timeout of requirement 5.

  Point cases: `request_timeout_s` is 900.0 for the three long-context stages and None for
  `synthesizer`, `plan_generator`, `eval_judge`, `closed_book`, `naive_rag`, `html_rag` and
  `""` (each None case in a test that also checks a long-context stage). Through a real
  `LLMGateway` with a fake adapter recording what `invoke` receives: `complete(stage=
  "long_context")` passes `timeout == 900.0`, `complete_tools(stage="frontier_sec_tools")`
  passes `timeout == 900.0`, and `complete(stage="synthesizer")` passes no `timeout` key.

  Property: for any stage name, `"timeout" in params` exactly when the stage is one of the
  three, and then it is 900.0; the other params are unchanged by the stage name.

  Oracle: requirement 5; OpenAI's flex-processing guide (raise the client timeout to 15
  minutes for Flex), measurement plan §8.
- **C6**: The run records the tier (requirements 6 and 7).

  Point cases: an `EvalRunner` run of a fake system over two cases with the variable `flex`
  has `report.config["service_tier"] == "flex"`; with it unset, `None` (both in one test);
  `run_many` over two systems puts it on both reports; `"bogus"` raises `LlmConfigError` and
  the fake system answered nothing. `run_suite.main(["--service-tier", "flex", ...])` runs
  with the variable `flex` and restores the previous value (unset, or a prior `priority`)
  afterwards; `--service-tier bogus` exits with status 2 before running.

  Property: for any of the four tiers or unset, the recorded value equals
  `openai_service_tier()` at the run's start.

  Oracle: requirements 6 and 7.
- **C7**: The judge configuration of requirement 8.

  Point cases: `load_judge_settings().milestone_models == ("gpt-6-luna",)`,
  `development_model == "claude-haiku-5-5"`; `gpt-6-luna` is an `openai` model in `llm.yaml`;
  `check_judge_family("gpt-6-luna", "anthropic")` returns None;
  `check_judge_family("gpt-6-luna", p)` raises `ValueError` for `p` in `openai`,
  `openai-5.6`, `openai-6.1`.

  Property: for every committed profile and every configured judge model,
  `check_judge_family` passes exactly when the judge's provider is not among the profile's
  tier models' providers.

  Oracle: requirement 8; measurement plan decision 10; the rule in `configs/eval/judge.yaml`.

## Interface

- `src.llm.request_options.openai_service_tier(environ: Mapping[str, str] | None = None) -> str | None`
- `src.llm.request_options.request_timeout_s(stage: str) -> float | None`
- Behaviour changes, no signature change: `OpenAIAdapter.build_params` / `build_tool_params`,
  `LLMGateway.complete` / `complete_tools` (params only; the retry loop `_call` is untouched),
  `EvalRunner.run_many` (report config), `scripts/eval/run_suite.py` (`--service-tier`), and
  the committed `pricing.yaml`, `llm.yaml`, `judge.yaml` and `SEC_TABLE_MODEL_CONFIGS`.

## Notes

- **Prices** come from the OpenAI pricing page as read on 2026-11-03 and transcribed in the
  measurement plan §5 (the table) and §12 item 1. The long-context rates do not affect a look
  (the arms' window is 250K tokens) but are recorded so a cost is never understated.
- **No network in tests.** OpenAI replies are plain objects; the SDK client is a fake that
  records its kwargs. The first live gpt-6.1 call is the operator's smoke (plan §12).
- **Retired tests updated in this spec PR**, each with a comment naming `openai-6-1-profile`:
  the `llm-provider-profiles` and `deepseek-provider` tests that pin the set of committed
  profiles now allow `openai-6.1`; tests that use `gpt-5.6-luna` as a configured milestone
  judge use `gpt-6-luna`.
- **Out of scope:** retiring or repointing the default `openai` profile (must happen before
  2026-12-11); 429 / retry / breaker handling under Flex (`eval-run-checkpoint`); a DDL column
  for the tier (it is in the run's `config` jsonb, like `llm_profile`); recording the tier the
  provider reports back per call.
- **Timeout baseline.** The gateway's SDK clients are built without a `timeout`, so today
  they use the SDKs' default (600 s for OpenAI and Anthropic); `LLMConfig.timeout` (60 s)
  applies only to the legacy wrappers.
