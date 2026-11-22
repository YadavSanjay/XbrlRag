# served-model-id: record the model version each provider actually served

External test v1: the code freeze, and the risk of model alias drift. `configs/llm/llm.yaml`
names models by alias (`gpt-5`, `gpt-5-mini`, `gpt-5.6-sol`, `claude-opus-5-5`, …). A
provider may move an alias to a new snapshot at any time, and today nothing records which
snapshot answered: the gateway's `llm_call` events, `LLMCallResult.model` and the eval
run's `config` carry only the **requested** id.

Both providers report the model that served each call in the response body:
- OpenAI Chat Completions and Responses: `response.model` (e.g. a dated snapshot such as
  `gpt-5-2025-08-07` for the alias `gpt-5`);
- Anthropic Messages: `message.model`.

This change carries that value from the adapter to the eval run record, so every quoted
run proves which snapshot answered, and a run in which an alias moved mid-run shows it.
It changes no pricing, no prompt, no request and no answer.

## Requirement

Terms: the **requested id** is the model id the gateway was asked for (`ModelRef.model`,
today's `model` field everywhere). The **served id** is the model id the provider's
response reports.

1. **Adapters.** `ParsedResponse` (`src/llm/gateway/adapters/base.py`) gets a field
   `served_model: str | None = None`, last, so every existing construction stays valid.
   - `OpenAIAdapter.parse` sets it from the raw response's `model` attribute on **both**
     paths: Chat Completions and the Responses API path (`raw.object == "response"`).
   - `AnthropicAdapter.parse` sets it from the raw message's `model` attribute, on the
     success path **and** on the usage carried by a refusal (`LLMRefusalError.usage`).
   - `OpenAICompatAdapter` inherits the OpenAI behaviour.
   - A raw response with no `model` attribute, or one that is `None` or an empty or
     whitespace-only string, gives `served_model = None`. Never an error. The value is
     otherwise kept exactly as reported (no lowercasing or trimming of a non-empty value).
2. **Gateway.** `LLMCallResult` (`src/llm/gateway/gateway.py`) gets a field
   `served_model: str | None = None`, set from the parsed response on success.
   - `model` keeps its meaning: the requested id.
   - The `llm_call` event of outcome `ok` and of outcome `refusal` carries a
     `served_model` field: the parsed (or refusal usage's) served id, or `None`. An
     `error` event carries `served_model = None` (there is no response).
   - Cost is unchanged: the cost tracker is still called with the **requested** id, so a
     served id with no row in `configs/llm/pricing.yaml` never raises and never changes
     the cost.
3. **Trace.** `TraceContext` (`src/observability/trace_context.py`) gets a field
   `served_models: dict[str, list[str]]`, default empty: requested id → the sorted list
   of distinct served ids seen in this trace. `record_llm_call` gains two keyword
   arguments, `model: str | None = None` and `served_model: str | None = None`, and adds
   `served_model` under `model` when **both** are non-empty; otherwise it records nothing
   in `served_models` (its existing totals are unchanged). The gateway passes both on
   success and on a refusal that carries usage.
4. **Arms.** `SystemAnswer` (`src/eval/core/system.py`) gets a field
   `served_models: dict[str, list[str]]`, default empty, with the same shape.
   - `answer_in_trace` (`src/eval/systems/_stage_llm.py`, used by `naive_rag_*`,
     `closed_book` and `long_context`) copies the trace's `served_models` onto the
     answer, including for a refused answer and an errored answer (whatever calls the
     trace saw before the failure).
   - `XbrlRagFullSystem.answer` copies the captured trace's `served_models` onto its
     answer, as it already copies `query_id` and `cost_usd`. This includes the error
     answer it returns when `pipeline.generate` raises: that answer carries whatever
     calls the captured trace saw before the failure.
   - The copy is a new dict of new lists: mutating the answer's value does not change
     the trace, or the other way round.
5. **Run record.** `EvalRunner.run` (`src/eval/core/runner.py`) sets
   `report.config["served_models"]` to the union over every answered case of its
   answer's `served_models`: requested id → sorted distinct served ids. It is set on
   every run, an empty dict when no case reported one, including an aborted run (the
   union of the cases that ran). On a resumed run (`prior=`) the prior report's
   `config["served_models"]`, when present, is merged into the union (a prior report
   without the key contributes nothing). This holds also when `prior` already covers
   every case and the runner returns without running any. The eval store
   already persists `config` as jsonb, so the run row carries it with no DDL change.
6. **No other behaviour changes.** Requests, prompts, retries, escalation, budgets,
   cost and grading are unchanged; existing fields keep their values.

## Acceptance criteria

- **C1**: Adapters. `OpenAIAdapter.parse` (Chat Completions and Responses shapes) and
  `AnthropicAdapter.parse` (success, and a refusal's `usage`) return
  `served_model == raw.model` exactly for any non-empty string, and `None` when `model` is
  missing, `None`, `""` or whitespace. Every other `ParsedResponse` field is unchanged
  from the same raw response parsed without a `model` attribute. Oracle: the raw fake's
  own `model` attribute.
- **C2**: Gateway. Through a real `LLMGateway` with a fake adapter: `LLMCallResult` has
  `model ==` the requested id and `served_model ==` the adapter's served id; the `ok`
  and `refusal` `llm_call` events carry that `served_model`; an `error` event carries
  `None`; `cost_usd` equals the cost of the same call when the served id is `None`, also
  when the served id is a string absent from `pricing.yaml`. Oracle: the fake's
  configured values and the committed pricing row.
- **C3**: Trace and arms. Inside one trace, calls with served ids `s1, s2, s1` for
  requested `m` (and one with served `None`) give `served_models == {m: sorted({s1,
  s2})}`; `answer_in_trace` and `XbrlRagFullSystem.answer` (with an injected pipeline
  that calls a real gateway under its own trace) return answers carrying that dict,
  including for a refusal and an error, as independent copies. Oracle: the sequence of
  served ids the fakes were configured with.
- **C4**: Run record. `EvalRunner.run` over a fake system whose answers carry
  `served_models` sets `report.config["served_models"]` to their union (sorted, distinct,
  per requested id); `{}` when none; merged with `prior.config["served_models"]` on a
  resume; present on an aborted run. Oracle: the union computed in the test from the
  fakes' configured values.

## Interface

```
# src/llm/gateway/adapters/base.py
ParsedResponse.served_model: str | None = None          # new last field

# src/llm/gateway/gateway.py
LLMCallResult.served_model: str | None = None           # new last field

# src/observability/trace_context.py
TraceContext.served_models: dict[str, list[str]]        # new field, default {}
TraceContext.record_llm_call(*, prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                             retries=0, cached_prompt_tokens=0,
                             model: str | None = None,
                             served_model: str | None = None) -> None

# src/eval/core/system.py
SystemAnswer.served_models: dict[str, list[str]]        # new field, default {}

# llm_call event: new field "served_model" (str | None) on every outcome
# EvalRunner.run: report.config["served_models"]: dict[str, list[str]]
```

No new functions, so no stubs. Tests read new fields with `getattr(obj, name, default)`
and set them on fakes by attribute assignment, so before the implementation they fail
with an assertion, not a `TypeError` or `AttributeError`.

## Notes

- **Fakes only at the I/O boundary:** raw provider replies are `SimpleNamespace` objects
  shaped like the SDK responses (copy the shapes in
  `test/spec/llm-anthropic-and-caching/conftest.py` and
  `test/spec/llm-refusals-and-store/conftest.py`); a fake `ProviderAdapter` behind a
  real `LLMGateway` with a real `CostTracker`; a fake pipeline injected into
  `XbrlRagFullSystem`; a fake `SystemUnderTest` for the runner. Keep the process-wide
  audit sink off, as the refusals spec conftest does.
- **Pricing:** use a model id that has a row in `configs/llm/pricing.yaml` as the
  requested id, and a served id that has none (e.g. `"<requested>-2099-01-01"`).
- **Not in scope:** a `served_model` column in the `audit.v_llm_*` views, a per-case
  eval column, and pinning dated ids in `llm.yaml`. `src/llm/batch.py` wraps the legacy
  `BaseLLMWrapper`, not the gateway, and is out of scope.
