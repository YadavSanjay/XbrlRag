# LLM Module (`src/llm/`)

Two layers live here:

1. **The XBRL LLM harness** (the product path): a provider-neutral gateway that every
   query-time LLM call goes through, with retry, cost, tracing, prompt-registry resolution,
   model escalation, a process-wide rate limiter and a per-run budget. Design:
   [`docs/design/XbrlRag_LLM_Harness.md`](../../docs/design/XbrlRag_LLM_Harness.md).
2. **LangChain wrappers** (`openai_wrapper.py`, `anthropic_wrapper.py`,
   `huggingface_wrapper.py`, `batch.py`, `sec10k_table_processor.py`), used by the PDF-legacy
   flow. Maintenance-only.

## Module structure

```
src/llm/
├── profiles.py                # loads + validates configs/llm/{llm,pricing}.yaml; active profile; cost_usd; keys
├── stage_models.py            # a stage's model (active profile, read at construction) and provider key
├── config.py                  # XBRL_QUERY_MODEL_TIERS, ESCALATION_POLICY, MODEL_TIERS, MODEL_PRICING (from
│                              #   the YAML), SEC_TABLE_MODEL_CONFIGS (per-call tokens/temperature), wrapper configs
├── gateway/
│   ├── gateway.py             # LLMGateway: retry / cost / trace loop, budget check, llm_call events
│   ├── model_ref.py           # ModelRef + resolve_model_ref: model id -> (provider, series, tier, config);
│                              #   call_params: token budget + temperature (None for temperature: false)
│   ├── json_utils.py          # extract_json: tolerant JSON extraction from model output
│   └── adapters/              # ProviderAdapter + OpenAIAdapter / OpenAICompatAdapter / AnthropicAdapter
├── xbrl_gateway.py            # build_xbrl_gateway: one injection point for the five XBRL stages
├── escalation.py              # ladder resolution, trigger check, model_escalation events
├── run_budget.py              # RunBudget + RunHalt (RunBudgetExceeded, CircuitBreakerOpen)
├── rate_limiter.py            # RPM / TPM limiter (shared process-wide by build_xbrl_gateway)
├── cost_tracker.py            # token + USD accounting incl. cache reads/writes; raises on an unpriced model
├── instrumented_openai_client.py  # backward-compatible OpenAI-pinned façade over LLMGateway
├── base.py                    # BaseLLMWrapper (legacy wrapper interface)
├── openai_wrapper.py / anthropic_wrapper.py / huggingface_wrapper.py   # legacy LangChain wrappers
├── batch.py                   # BatchProcessor, batch_with_retry (legacy)
└── sec10k_table_processor.py / sec10k_page_summary*.py / sec10k_table_summary_reader.py  # PDF flow
```

## The XBRL harness in brief

```python
from src.llm.gateway import resolve_model_ref
from src.llm.stage_models import resolve_stage
from src.llm.xbrl_gateway import build_xbrl_gateway

ref = resolve_model_ref(resolve_stage("synthesizer").model)  # active profile; never hardcode a model
gateway = build_xbrl_gateway("synthesizer")                   # shared rate limiter + run budget
```

**Models and profiles.** Model choice is committed config in
[`configs/llm/llm.yaml`](../../configs/llm/llm.yaml): providers, one row per model and the
profiles (`openai` default, `openai-5.6`, `anthropic`, and `deepseek` for development), each
mapping the small / medium /
frontier tiers to models. Pick one with `XBRLRAG_LLM_PROFILE` or a run's `--llm-profile`; the
stages read it when they are built. Design:
[XbrlRag_LLM_Harness.md](../../docs/design/XbrlRag_LLM_Harness.md) §2.

Stages call `gateway.complete(...)` with their resolved `ModelRef`, the prompt refs from
`prompts/prompt_registry.py`, and `response_format="json"`. Every call is recorded on the
active trace as an `llm_call` event (tokens incl. cached, USD cost, latency, `prompt_version`).

**Cache points and refusals** (design §2.1, spec `docs/specs/llm-anthropic-and-caching.md`):

- Mark a message `"cache_point": True` when the prompt up to its end is a prefix worth
  caching (at most 3 per request; more raise `ValueError` before any call). Anthropic gets
  a `cache_control` block on that message; OpenAI sends such a request through the
  Responses API with an explicit `prompt_cache_breakpoint` (gpt-5.6 otherwise places its
  implicit breakpoint after the varying tail). Requests without a cache point are unchanged.
- Claude calls always stream, send the model's `max_output_tokens` and configured `effort`
  (`output_config.effort`), and never send `temperature` or `thinking`.
- A provider refusal raises `LLMRefusalError` (a `ValueError`). The gateway never retries
  it and records `outcome: "refusal"`; the synthesizer degrades without escalating, and the
  pipeline returns a declined answer (`metadata.refused`). A Claude refusal carries its
  `category`, `usage` and `billed` (`refusal_is_billed`: a mid-stream refusal, or category
  `bio` / `frontier_llm` / `reasoning_extraction`); the gateway puts its tokens on the event
  and the trace and charges its cost only when billed (spec
  `docs/specs/llm-refusals-and-store.md`). `audit.v_llm_calls.refusals` and
  `llm_refusals_total` count refusals.
- OpenAI Responses calls send `store: False`, so OpenAI does not retain them.
- Every SDK client is built with `max_retries=0`: the gateway's loop is the only retry layer.
- DeepSeek (`DeepSeekAdapter`, spec `docs/specs/deepseek-provider.md`) is OpenAI-compatible
  at the provider row's `base_url`. Requests are always Chat Completions: cache-point markers
  are dropped (DeepSeek caches automatically), the budget goes out as `max_tokens`, and
  `reasoning_effort` is the caller's or the model's configured `effort`. It reads only
  `DEEPSEEK_API_KEY`, never the OpenAI key.

| Env | Effect (all off by default) |
|---|---|
| `XBRLRAG_RUN_MAX_COST_USD`, `_LLM_CALLS`, `_TOKENS`, `_CONSECUTIVE_FAILURES` | per-query ceiling and circuit breaker; a trip raises `RunHalt` and the pipeline degrades gracefully |
| `XBRLRAG_LLM_RPM`, `XBRLRAG_LLM_TPM` | one process-wide rate limiter for every stage |
| `XBRLRAG_TRACE_LLM_BODIES=0` | stop recording prompt / response bodies on `llm_call` events |
| `XBRLRAG_LLM_PROFILE=<name>` | the LLM profile (`configs/llm/llm.yaml`) every stage runs on; unset means `openai`. An unknown name fails loud |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` (or `<VAR>_FILE`) | the key of each provider the active profile uses |
| `XBRLRAG_USE_REPLACEMENT_MODELS=1` | legacy: the `openai-5.6` profile when `XBRLRAG_LLM_PROFILE` is unset |

Rules for new code: route every LLM call through the gateway (no direct provider SDK calls),
bound every new loop or fan-out, and let `RunHalt` propagate (never swallow it in a broad
`except`). See [`.claude/rules/rag.md`](../../.claude/rules/rag.md).

**Pricing.** [`configs/llm/pricing.yaml`](../../configs/llm/pricing.yaml) holds per-1K-token
input / output / cached-input / cache-write rates and optional prompt-size tiers;
`MODEL_PRICING` in `config.py` is read from it. A model with no row cannot be called: the
cost tracker raises rather than record $0. The cache multipliers are dated assumptions
tracked in the [re-audit register](../../docs/harness/REAUDIT.md).

## Legacy wrappers (PDF flow)

LangChain-based wrappers with `tenacity` retry, an RPM/TPM limiter and a `CostTracker`:

```python
from src.llm.openai_wrapper import OpenAIWrapper
from src.llm.config import OpenAIConfig
from src.llm.cost_tracker import CostTracker

tracker = CostTracker(log_file="logs/llm_costs.jsonl")      # JSONL, one record per call
wrapper = OpenAIWrapper(OpenAIConfig(model="gpt-4o-mini"), cost_tracker=tracker)
print(wrapper.generate("Explain RAG in 50 words"))
print(wrapper.get_cost_summary()["total_cost_usd"])
```

`HuggingFaceWrapper` and `AnthropicWrapper` share the `BaseLLMWrapper` interface;
`BatchProcessor` runs bounded concurrent batches; `SEC10KTableProcessor` summarizes table
images with a vision model for the PDF pipeline. The provider wrappers import their SDKs
best-effort, so a missing optional dependency never makes `src.llm.config` or
`src.llm.cost_tracker` unimportable.

## Tests

```bash
conda run -n py312 python -m pytest test/unit/test_llm_gateway.py test/unit/test_model_ref.py \
    test/unit/test_json_utils.py test/unit/test_escalation.py test/unit/test_run_budget.py \
    test/unit/test_xbrl_gateway_factory.py test/unit/test_prompt_cache_tokens.py \
    test/unit/test_llm_anthropic_and_caching_wiring.py -q   # harness
conda run -n py312 python -m pytest test/unit/test_llm.py test/unit/test_llm_features.py -q   # legacy wrappers
```

No test needs an API key; providers are faked at the adapter boundary.
