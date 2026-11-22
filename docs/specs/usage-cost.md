# usage-cost — v1 fact and narrative responses report what they cost

Requested by faultline (scenario program step 15e, `cost-per-layer`): it sets each enrichment
layer's latency and cost against that layer's Shapley contribution to a scenario's P&L, and two of
those layers are XbrlRag endpoints — `POST /api/v1/facts/lookup` (`xbrlrag_facts`) and
`POST /api/v1/narrative/search` (`xbrlrag_narrative`).

## Requirement

Both responses carry a top-level `usage` block whose `cost_usd` is the total USD cost of every paid
model call made while producing that response. faultline reads a missing block as "cost unknown" and
`0.0` as "free", so the block is always present, and `cost_usd` is always a finite number `>= 0`.

Today neither endpoint makes a paid call: the facts lookup is SQL only, and the narrative search
embeds with BGE-M3 and reranks with jina-reranker-v3, both self-hosted (in-process or through
RagInference via `RAGINFER_URL`), not billed per call. So both report `0.0` today. The figure is
**measured, not hard-coded**: every paid model call in this codebase goes through the `LLMGateway`,
which records its cost on the active `TraceContext`. A meter that opens its own trace for the
request and reads the total back counts any gateway call a future change adds to these paths (a
query rewriter, an LLM-backed resolution) without anyone remembering to update the endpoint.

The meter is a lighter scope than `trace_query`: it emits no `query_start`/`query_summary` events
and never persists to the audit sink, because a raw fact lookup is not a query and must not add a
row to `audit.query_audit`. When it runs inside an existing trace it still hands its spend to that
trace, so the enclosing trace's totals stay complete.

## Acceptance criteria

- **C1**: Inside `with UsageMeter() as m:`, `current_trace()` is a `TraceContext`, and after
  `current_trace().record_llm_call(cost_usd=c_i)` for each `c_i` (the gateway's recording call),
  `m.usage()` returns `{"cost_usd": sum(c_i), "llm_calls": n}` within `abs=1e-12`, both after the
  block and while it is still open. With no calls it returns exactly `{"cost_usd": 0.0,
  "llm_calls": 0}`, `cost_usd` a `float`. Splitting a list of calls across two consecutive meters
  gives usages that add up to one meter over the whole list. Oracle: the sum, computed in the test.
- **C2**: The meter is not a query trace. With an `AuditSink` installed, a metered block persists
  nothing. On exit — normal or by exception, which propagates unchanged — `current_trace()` is what
  it was before entry (`None`, or the enclosing trace). An enclosing trace's `total_cost_usd` and
  `llm_calls` grow by exactly the metered block's usage (abs=1e-12). Oracle: the requirement
  (no audit row, no lost spend); sums computed in the test.
- **C3**: `UsageModel` (in `src.api.v1.schemas`) accepts a finite `cost_usd >= 0` and an
  `llm_calls >= 0`, and rejects (pydantic `ValidationError`) a negative, NaN or infinite
  `cost_usd` and a negative `llm_calls`. Oracle: faultline's contract (finite, `>= 0`).
- **C4**: `FactLookupResponse` and `NarrativeSearchResponse` have a **required** `usage:
  UsageModel` field: constructing one without `usage` raises `ValidationError`, and
  `model_dump(mode="json")["usage"]["cost_usd"]` is the value passed in, as a JSON number. Oracle:
  faultline's contract (block always present, at the top level).

## Interface

New (stubbed in the spec PR):

- `src.observability.usage_meter.UsageMeter` — a context manager:
  - `UsageMeter.__enter__(self) -> UsageMeter`
  - `UsageMeter.__exit__(self, exc_type, exc, tb) -> None` (never suppresses an exception)
  - `UsageMeter.usage(self) -> dict[str, float | int]` — `{"cost_usd": float, "llm_calls": int}`
- `src.api.v1.schemas.UsageModel(BaseModel)` — fields `cost_usd: float`, `llm_calls: int`
  (the spec PR adds the class empty; the implementer adds the fields and their constraints).

Behavior on existing code (implementer):

- `FactLookupResponse` and `NarrativeSearchResponse` gain `usage: UsageModel` (required, no
  default).
- `router.facts_lookup` and `router.narrative_search` run their service call inside a
  `UsageMeter` and return `usage=UsageModel(**meter.usage())`.

## Notes

- Not priced, by design: self-hosted model compute (BGE-M3 embedding, jina-reranker-v3) and
  infrastructure (Postgres, Qdrant, the RagInference host). Those cost machine time, not a
  per-call fee; faultline already records each call's latency, which is where that cost shows up.
  If an endpoint ever calls a billed embedding or rerank API directly (not through the gateway), it
  must record that cost on the active trace for this figure to stay true.
- `cost_usd` is the gateway's own estimate (`CostTracker` pricing in `src/llm/config.py`), the same
  number the trace and `/metrics` report; it is not rounded here.
- Other endpoints (`/facts/compute`, `/facts/bridge`, `/metrics`, `/screen`, `/answer`) are out of
  scope; faultline does not call them as a layer. `/answer` already reports cost through its trace.
- Integration (implementer, `test/unit`): a route-level test per endpoint showing the block in the
  response with `cost_usd == 0.0`.
