# CLAUDE.md — `src/retriever/xbrl/` (XBRL plan-based retriever)

The query-side internals of the **XBRL flow** (separate from the legacy PDF
retriever in `src/retriever/`). Orchestrated by
[`src/pipelines/xbrl/retrieval_pipeline.py`](../../pipelines/xbrl/retrieval_pipeline.py)
(`XbrlRetrievalPipeline.generate`).

- **Operator guide:** [`docs/guides/xbrl-flow/06-retrieval-pipeline.md`](../../../docs/guides/xbrl-flow/06-retrieval-pipeline.md)
- **Design:** [`docs/design/XbrlRag_Architecture.md`](../../../docs/design/XbrlRag_Architecture.md) (query pipeline, fuzzy intent vs deterministic resolution, why lookups are strict), [`XbrlRag_Evaluation.md`](../../../docs/design/XbrlRag_Evaluation.md) §7 (rules learned from the accuracy work)
- The root [`CLAUDE.md`](../../../CLAUDE.md) has the full stage-by-stage map; this file is the local invariants an editor of this module must not break.

## Stage flow (per sub-query)

```
QueryDecomposer → IntentRouter (inside BranchDispatcher) → _resolve_plan
  → PlanValidator → PlanExecutor(lookup|compute|screen|aggregate|breakdown|narrative)
  → XbrlRagSynthesizer → XbrlFaithfulnessVerifier
```

`QueryDecomposer` runs first and splits a COMPOUND query into ordered sub-queries
(most queries → one passthrough). A passthrough whose plan is refused with
`error_code` `"nested_ranking"` (a ranking within another ranking's result; planner prompt
v10) is decomposed **once** more with that code as PLANNER_FEEDBACK (decomposer v4) and run
as compound (`XbrlRetrievalPipeline._redecompose_on_refusal`, `decision` events at point
`redecompose_on_refusal`); sub-queries never re-decompose, a failed retry keeps the
refusal, and `RunHalt` propagates. A dependent sub-query's `<<TOP:id:n>>` token is
replaced by the dependency's first screen step's rows in the step's own order (never
re-sorted), each with its rank: `"AMZN (#1), MSFT (#2)"`; `n == 1` stays the bare ticker, and
without a screen the plan's lookup tickers go in unlabelled (`ranked_top.py`,
`_inject_dependencies`). The router strips one trailing `(#k)` from every lifted company
mention before it resolves (`strip_rank_label`; docs/specs/ranked-top-injection.md).
`IntentRouter` runs *inside*
`BranchDispatcher.decide()` — they share the one `traced_stage("intent_router")`.

## Model tiers — single source of truth

A stage's model is the active LLM profile's model for its tier
([`configs/llm/llm.yaml`](../../../configs/llm/llm.yaml), read at construction via
[`src/llm/stage_models.py`](../../llm/stage_models.py); an explicit `model=` wins);
**never hardcode a model name here.** The
decomposer, intent router, plan generator and synthesizer share the medium tier; the
faithfulness verifier runs on the small tier (`reasoning_effort="low"`, bounded batches).

Per-call token/temperature come from `stage_call_params(ref.config, ...)`: the OpenAI models'
`SEC_TABLE_MODEL_CONFIGS` rows (gpt-5* models force `temperature=1` and count reasoning
tokens against `max_completion_tokens`), else the model's whole `max_output_tokens` from
`llm.yaml` (8,000 for a model with neither). Never a fixed per-stage number: reasoning
shares the budget, and a fixed 2,000 left DeepSeek replies empty at `finish_reason=length`
on both escalation rungs. `ref.config` is read per attempt, so an escalated model gets its
own ceiling. A `temperature: false` model (Claude) gets no temperature. Every stage
builds its `LLMGateway` through `src/llm/xbrl_gateway.py::build_xbrl_gateway` (shared rate
limiter + per-run budget): retry, cost, tracing, prompt registry. The gateway raises
`ValueError("LLM returned empty message content")` when a reasoning model burns its whole
budget, which is why the verifier and decomposer bound reasoning with
`reasoning_effort="low"`. A `RunHalt` (budget, circuit breaker, Flex cap, stop) must
propagate; never swallow it in a broad `except`. The decomposer and verifier re-raise it
ahead of their `except Exception` fallbacks, and `XbrlRetrievalPipeline.generate` /
`generate_from_decision` annotate the trace `halted` then re-raise it, so no answer is built
for a halted query here. The halted answer is built only at the API boundary
(`src/api/pipelines.py::xbrl_answer`; docs/specs/xbrlrag-run-halt.md).

## Invariants / gotchas

- **Numbers are audited, not generated.** `lookup`/`screen`/`compute` produce every
  number with a `fact_id` citation + confidence; the synthesizer LLM narrates only,
  never does arithmetic.
- **Which filing answers a period** (S1, `restatement.py`, docs/specs/restatement-s1.md):
  the flat lookup reads the filer's measure from every filing and keeps the year's own
  column (exact DEI end, never a comparative) as the latest later-labelled canonical filing
  restates it; `as_originally_reported` reads the original filing's own column; the flat
  screen restates its fy pick the same way and keeps `fiscal_year == fy`. Do not put a
  `fiscal_year`/`fiscal_period` filter back into the S1 query: a later 10-K or 10-Q
  comparative restates the year. Multidim, segment, geographic and derived paths are not
  S1 yet.
- **Which candidate answers a lookup** is `family_resolution.candidate_order`
  (docs/specs/concept-family-coverage.md): a primary before a stated substitute, then the
  latest period, a presented statement line, rank, a valued fact and the finer `decimals`.
  Components never answer but stay candidates for the ambiguity checks. A substitute
  answer carries `substitution` {code, note, concept} to the synthesizer (prompt v2
  states the note). The preferred-concept credit is `rank_credit` (pinned by
  revenue-equivalent-concepts) or `member_credit` (a decided same-measure member or a
  substitute); the Debt component guard applies only to dimensioned lookups.
- **Entities are resolved deterministically** in `entity_extractor.py` (SQL on
  `entity_master` / `fiscal_calendar`) — the LLM never invents a CIK / fiscal year.
  Company tiers: ticker, committed alias, exact normalized name, then a fuzzy name only if
  it scores >= 0.75 **and** beats the runner-up by 0.10 (`entity_resolution.py`,
  docs/specs/entity-resolution-margin.md); otherwise `ambiguous` (up to 3 `candidates`) or
  `unresolved`. Never loosen this to "best match wins": a lookalike ("Microchip" → Micron)
  would be answered with real citations. A non-screen query with such a mention is
  **refused** by the pipeline (`_unresolved_company_response`, "did you mean …") before
  any plan or retrieval runs; the dispatcher's degrade-to-narrative still applies to an
  unresolved *period* or a missing company.
- **`branch_dispatcher.py` gotcha:** a `NUMERIC_SCREEN` names no company and must go
  straight to `PLAN_GENERATOR` — it must **not** degrade to narrative. Only a
  *non-screen* numeric query with an unresolved company/period degrades
  (`_unresolved_reasons`). `intent=NARRATIVE` → `NARRATIVE_BRANCH`.
- **`screen` is cross-filer:** one SQL over all filers, **no `cik`**; its `StepResult`
  carries a `rows` list (not a scalar), each row with its own `fact_id`
  (`xbrl_factstore_screen.py`). The verifier resolves screen evidence per-row by
  `fact_id`. A screen for FY N ranks only FY-N figures (others go to `excluded`).
  A screen ranks USD amounts only: a filer reporting in another currency is a
  `currency` exclusion. `mv_derived_metrics` has no unit column, so a derived screen
  reads its component facts' units in a separate query (`derived_metric_unit`; it passes
  `fy` so the spec fakes answer it); a ratio of one currency is `pure` and ranks every
  filer, a mixed-unit row is a `unit_mismatch` exclusion
  (docs/specs/units-currency-integrity.md).
  **A flat screen picks as the lookup does:** `_query_flat` returns every candidate row of
  each filer's latest year (with `concept_role`, `presented`, `decimals`) and
  `select_preferred_concept` orders them with `candidate_order` itself, never a copy; a
  filer whose first row is a stated substitute or only components is listed apart
  (docs/specs/screen-candidate-order.md). **Except a defined family** (`Debt`,
  `DeferredRevenue`): `select_screen_picks` keeps every filer at the row
  `family_resolution.measure_column` names (the lookup's `_defined_column` uses the same
  rule), and the filer is ranked only by its S4 measure; an unpresented measure is
  `metric_not_tagged`, and only a non-primary pick that reaches no measure is
  `component_only` (docs/specs/s4-screen-defined-members.md). Keep the literal `unit = 'USD'` (the
  restatement-s1 fake recognises the query by it).
  **No per-view query has a `LIMIT`:** the queries are per-ticker (`DISTINCT ON (ticker)`
  or every row), so a cap there keeps the alphabetically first filers before ranking. Every filer
  is ranked; `row_limit` (default None) only trims the ranked rows and is reported as
  `to_dict()["row_cap"]` (docs/specs/screen-full-universe.md).
- **A filed nil is never a value** (stored as value NULL; `value IS NULL` is the nil
  test). A lookup whose answer period holds only nils is `NOT_FOUND` with a note naming
  the nil fact ids; an earlier column never stands in. A screen never ranks a nil row:
  it is a `nil` exclusion (`split_nil_rows`). In SQL, `ORDER BY value DESC` puts NULL
  first, so every per-filer `DISTINCT ON` / `RANK` must prefer valued rows
  (`DESC NULLS LAST` or `(value IS NULL)`) (docs/specs/sign-and-nil.md).
- **Member screens pick one member per filer, in Python, over uncollapsed rows**
  (docs/specs/segment-lookup-screens.md). The segment and geographic queries have **no
  `DISTINCT ON`**: the per-filer choice (`choose_member_row`: exact matches only when there
  are any; several member keys or values is `ambiguous_member`, never the largest) needs
  every row. A segment screen ranks only `SEGMENT_SCREEN_AXES` (business segments,
  products) and single-dimension facts; **both are SQL predicates** (`segment_axes`,
  `NOT d.has_multiple_dimensions` through a `LATERAL` read of `mv_financial_facts`), not
  Python filters: the screen-full-universe fake serves rows without those columns.
  Europe/EMEA resolve by the filer's member label (`REGION_OF_REQUEST`), never `geo_code`;
  a country still matches `geo_code`. Every unranked member gets one reason
  (`currency` > `ambiguous_member` > `multi_dimension_only` > `different_member` >
  `period_not_filed` > `member_not_found` > `metric_not_tagged`); `excluded` keeps only
  the `period_not_filed` ones. The queries return every unit so a foreign currency is
  stated, not dropped.
- **Member screens pin the duration** (docs/specs/period-disclosure.md R2): the segment
  and geographic SQL carry `duration_kind = %(duration_kind)s` (`_member_duration_kind`:
  FY annual, Q1 quarter, Q2/Q3 quarter or half/nine_month with `ytd`, `instant` for
  instant families), so a 10-Q's year-to-date never answers a quarter and a 10-K's
  quarter-length member fact never answers the year. The segment label match is
  `lower(segment_label) LIKE member_like OR compact(segment_label) LIKE '%'||member_key||'%'`
  (the first only short-cuts the second); the locked fakes of segment-lookup-screens and
  period-disclosure each model one of those forms, so keep both.
- **Period lengths** (`period_disclosure.py`, pure): `duration_class` bands a stored
  period by days (quarter 84-119, half 168-189, nine_month 252-280, annual 350-380, else
  `other`); `year_length_disclosure` states two compared years' day counts (a note when
  6+ days apart, `53-week` when either has 370-372); `opening_balance` /
  `average_based_ratio` are the average-balance rule; `derive_q4` is FY minus the
  nine-month YTD on one start date, or a refusal. Every emitted date is a stated date
  (stored end - 1 day). The views mirror them (R7.1, R7.4, R7.5): both base MVs'
  `duration_kind` CASE uses the same bands (`other` included; kept in step by
  `test/unit/test_period_disclosure_wiring.py`), `mv_yoy_comparison` carries both
  periods and `days_current`/`days_prior` (the v1 bridge returns `year_length_note`), and
  `mv_derived_metrics` averages with the same filing's opening balance, its `formula`
  saying `(closing; opening balance not filed)` otherwise.
- **Q4 lookups** (`q4_lookup.py` `select_q4`, pure; docs/specs/q4-lookup.md): no stored row
  is labelled Q4 (a 10-K is `FY`), so a `fiscal_period: Q4` lookup never pins a
  `duration_kind`. A duration family with no dimension reads every row of the measure (the
  flat S1 query, `s1_select=False`), decides the unit as any lookup, and `select_q4`
  answers: a **filed** fourth quarter of the 10-K's own column first (one equal to its own
  year is a mis-tag and is set aside), else **derived** = the year's row minus the same
  label's nine-month row on the same start (`derive_q4`), both on one S1 basis (`latest`;
  `as_filed` when only one side was restated; `original`). The result's `q4` record
  carries both fact ids (`component_fact_ids` `q4_year`/`q4_nine_month` in the executor),
  the period evidence is the quarter, and the synthesizer (v8) states the note. An instant
  family answers as `FY` with the note "Q4 ends on the fiscal year's end"; a dimensional Q4
  is `NOT_FOUND` (member views have no S1 column selection yet).
- **Year lengths in answers** (`year_length_answers.py`, pure;
  docs/specs/period-year-length-answers.md): the periods compared are the **cited facts'
  stored periods**, never a fiscal-year label. An OK `compute`/`cagr` gets
  `StepResult.year_lengths` from `compared_year_lengths` over its referenced lookups (the
  cited candidate's `period_start`/`period_end`, `plan_executor.cited_period`; a derived or
  summed lookup has none), logged on its `numeric_lineage` event; the synthesizer (v7)
  states each `note` verbatim and the pipeline logs a `decision` event
  (`point="year_length_note"`) when a note is in neither the summary nor a statement citing
  its step (`evidence_index` resolution; a paraphrased summary alone does not fire it). A derived
  growth screen reads its component facts' periods in `_query_fact_units` and carries
  `screen_year_lengths` of the ranked rows on `ScreenResult.year_lengths`; the pipeline
  appends `screen_year_length_text` (`append_year_length_disclosures`, between the screen
  and aggregate disclosures). The note never changes a value, and its numbers (ISO dates,
  `N days`, `53-week`) are exempt in `read_stated_numbers`, so never reword it.
- **A one-dimension lookup never answers with a cell** (a fact with a second, non-neutral
  dimension), whichever dimension the flat view lists first: `_set_aside_cells` drops them
  from the flat rows. With no member fact, `_sum_of_cells` answers the sum of the member's
  cells over one other axis only when the filer's own facts for those members add up to
  its undimensioned total (`_partition_sum`); the result carries `sum_of` (the summed
  facts' ids). Otherwise it refuses with a note naming the cells
  (docs/specs/segment-lookup-screens.md R1).
- **`compute` keeps units.** Every compute step carries `unit` = `expr_unit(expr, units)`
  over its inputs' units (`pure`/`Rate` are dimensionless; the store's `shares*USD` is
  USD per share). Two currencies, a sum of different units, or an input with no unit is
  `ERROR`; so is any input step that is not OK (an `AMBIGUOUS` lookup still reports a
  value; the executor never uses it). Order: non-OK inputs → units → n/m → evaluate.
  `XbrlCalculatorTool.evaluate` itself stays unit-blind for its other callers.
- **Citations are checked deterministically before the verifier LLM**
  (`citation_integrity.py`, docs/specs/citation-integrity.md). A statement citing **any**
  id that resolves to nothing in its own (sub-)query is dropped (never skipped), and so is
  one stating a number that matches none of its cited values at the approved precision
  (the number reader is `src/numerics/stated_numbers.py`, shared with the eval grader).
  Both drops are `UNSUPPORTED` verdicts with a `check` field and a `decision` event at
  point `citation_integrity`; they are never sent to the LLM. Ids resolve by the same rule
  for the LLM's evidence. A compound answer's merged statements keep their ids and carry
  `subquery_id` (`_merge_responses`); resolve them against
  `execution_result["subqueries"][subquery_id]`.
  **A cited screen's derived facts are its evidence** (`screen_evidence.py`,
  docs/specs/verifier-derived-disclosures.md). A statement cites a screen when it cites
  its step id or any row's `fact_id`; that screen's counts (`screen_count_values`: ranked
  members before `top_n`, universe members, rows returned, exclusions) and the rank of
  each row cited by its own `fact_id` are exact number-check candidates, never a cited
  screen's counts for an uncited one and never a percent. The verifier LLM sees each
  screen row's `rank` and `period_end` and, per cited screen, one entry with `screen` =
  `screen_summary(step)` (on the step's own entry when its id is cited); prompt
  `faithfulness_verifier/v2` tells it these support rank, count, exclusion and period-end
  claims. Don't widen `stated_numbers` to exempt "of N": a wrong count must still drop.
  **A cited value step's period is its evidence** (`step_period.py`,
  docs/specs/verifier-period-evidence.md). The executor sets `StepResult.period` on an OK
  lookup (plan `fy`/`fiscal_period`/`ytd` plus the cited fact's start and **stated** end,
  via `cited_period`), compute (each referenced step's own period, in first-reference
  order, flattened) and aggregate (the aggregated screen's fiscal year and member period
  ends); a non-OK step has none. The verifier copies it onto that step's evidence entry,
  and prompt `faithfulness_verifier/v3` says a fiscal-year or period claim matching it is
  supported. Never derive the fiscal year from a date here.
- **A total/median over a group is a screen plus an `aggregate` step**
  (`screen_aggregate.py`, `universe_routing.py`, docs/specs/screen-aggregates.md). The
  group ("the retailers", "covered companies") is found in the question's words by rule
  (`find_universe_mentions` over groups.yaml ids and the committed `universe_aliases()`
  table), never by the LLM; a group lifted as a company mention is split off before
  resolution. One group and no resolved company routes to `NUMERIC_SCREEN`; the planner
  must screen that universe and, on a cue (`group_aggregate_cue`: an `aggregate_cue` word
  such as median / combined / sum of ...; else, for one group and no company, an "add up"
  pair or "total" with no ranking, per-member or count word, docs/specs/group-sum-cue.md),
  add the aggregate (`group_plan_errors`, same retry as the validator). The
  value is computed in code over exactly the screen's rows (never over a `row_cap`-trimmed
  list); the synthesizer never adds rows itself, and a fixed sentence naming the
  aggregated members is appended after the screen's disclosure sentence.
- **`breakdown` is single-filer:** the members that partition one filer's total, chosen by
  `choose_breakdown` (the finest exact member set, never a member with its own ancestor;
  else every member plus one named reconciling line from the filing's own table or
  calculation; eliminations and zeros are never members), which the eval suite's ground
  truth shares; `choose_partition` is its plain case. A reconciling row has `kind`
  "reconciling" and `rank` None. Change it only with its locked specs
  (docs/specs/breakdown-op.md, docs/specs/breakdown-reconciling-items.md).
- **Anything that picks "the" fact orders by `concept_pref_rank`, never by `fact_id`**,
  and skips the component band (ranks ≥ 50). One value in one unit (the reporting currency
  by default); rounded duplicates are one value (most precise wins); ties are refused as
  `AMBIGUOUS`, not guessed.
- **Executor fallbacks are narrow and OK-only.** Sub-family → `Revenue`, glued-hint split,
  sibling intersection, derived gross profit: each is accepted only when the result is OK
  with a one-to-one dimension match, never changes the original steps, and emits a
  `decision` event. A new fallback follows the same rules.
- **Escalation** is wired in all three stages that have a policy in
  `ESCALATION_POLICY`: `synthesizer` and `faithfulness_verifier` (on
  `parse_error`/`empty_content`), and `plan_generator` — which escalates **only** after
  its local validate-retry loop still ends in `plan_validation_failed`. A JSON-parse
  error or the prompt's own error contract is deliberately not a trigger.
- **Period / point-in-time:** `lookup`/`screen` default `fiscal_period='FY'`; quarters
  set `Q1`–`Q4` with `ytd` for cumulative; `lookup` also takes `as_of` /
  `as_originally_reported`. `EntityExtractor` resolves the natural-language period.
  Before routing, a **dependent sub-query** (`depends_on` or a `<<TOP…>>` token) that the
  decomposer wrote with no period of its own inherits the compound question's one annual
  fiscal year as " for fiscal year Y" in its text (`subquery_period.py`, pure, applied in
  `QueryDecomposer.decompose` after the `split_loses_operation` check; one `decision`
  event `inherit_period` per rewritten sub-query; docs/specs/dependent-subquery-period.md).
  A question with no year, two years, a range, a quarter, a relative phrase, a date or
  "calendar" gives nothing; the bare word `current` is not a period phrase, and a
  year-relative phrase (`latest`, `current fiscal year`, …) directly followed by a year
  ("Assets, Current fiscal year 2024") states that year, not a relative period. A
  dependent sub-query whose only year is a lone "from <year>" with growth/change wording
  ("growth from FY24") is rewritten to "from fiscal year B to fiscal year E" (E = the
  question's one later year, else B + 1; `decision` event `from_end_year`, no
  `inherit_period`); without such a word "from FY24" stays a point-in-time lookup
  (docs/specs/subquery-period-phrases.md).
  A mention naming several years ("FY2023 through FY2025", "between … and …", a list)
  resolves to **one `ResolvedPeriod` per year**, all with the same `mention`, so it never
  takes the one-step lookup fast path and every consumer (planner scope, narrative filter,
  `html_rag` scopes, long-context targets) unions `fiscal_year`. A range over
  `MAX_RANGE_YEARS = 10` years is one *ambiguous* period (degrades), never a guessed subset
  (`extract_year_util.py`, docs/specs/fiscal-year-range.md). A range the router LLM splits
  into two mentions ("FY23", "FY25") gets its middle years back after `resolve_periods`:
  `EntityExtractor.complete_stated_ranges` (called from `IntentRouter.route`) reads the
  *question* (`fiscal_date.missing_range_years`), and when it states a range whose two
  endpoints are both resolved FY periods, appends each missing year as an FY period whose
  mention is the range as written, logging `decision` `stated_range_fill`. A list ("FY23 and
  FY25") adds nothing; ranges over 10 years, quarter ranges and one-endpoint matches add
  nothing; dated endpoints read the primary filer's calendar only when the question writes a
  full date (docs/specs/fiscal-year-range-split.md). Router prompt v4 asks for a range as one
  mention; the fill is the guarantee when the model splits anyway.
  A mention that writes a **full date** ("the fiscal year ended February 2, 2025") resolves
  through the filer's own `fiscal_calendar` rows (`fiscal_date.py`, pure; the read stays in
  `EntityExtractor._resolve_dated`): the period ending on that date within 7 days, so Home
  Depot's date is fiscal 2024, not the date's year. A date matching no row, or a tie, is one
  *ambiguous* period, never the digits; no calendar rows or no resolved company keeps the
  digits reading. A relabelled date logs a `decision` event at point `period_resolution`
  (docs/specs/fiscal-year-ended-date.md).
  A mention with a digit that writes **no fiscal year in 2018-2035** ("Q3", "H1", "the
  10-K", "fiscal 2015") is one *ambiguous* period with `fiscal_year=None` and a reason; it
  has year evidence, so it degrades the query. `extract_years_in_range` has no default year:
  a text that writes none reads as `[]` (docs/specs/no-default-year.md); only the PDF legacy
  `BaseRetriever` keeps its own `LEGACY_DEFAULT_FISCAL_YEARS`.
  A failed mention with **no year evidence** ("year-over-year", "each year", "annual
  filing": no digit and no relative, sub-annual, number or month word,
  `period_evidence.has_year_evidence`) is dropped from `IntentRouterResult.periods` when
  another period of the query resolved, with a `decision` event
  `ignore_yearless_mention` at point `period_resolution`; `raw_extraction` keeps it. It
  then also bars the one-step lookup fast path (it may shape the period). A failed
  mention with year evidence ("Q3", or "last fiscal year" with no anchored company), or a
  query where no period resolved, still degrades (docs/specs/yearless-period-mention.md).
  "last/previous/preceding fiscal year" are prior-year phrases (`last_seen_fy - 1`, like
  "prior fiscal year"); "last fiscal quarter" is not (docs/specs/last-fiscal-year-phrase.md).
- **Injectable components:** every sub-component defaults to `from_env()` and is
  constructor-injectable — that is how tests stub them.
- **Optional remote inference:** `xbrl_narrative_retriever.py` uses the shared
  RagInference service (remote embedder/reranker) when `RAGINFER_URL` is set;
  otherwise BGE-M3 + Jina load in-process. The serving image is **torch-free** and has
  no local fallback — keep new imports on this path routed through
  `src/retriever/endpoints.py` and listed in `TORCH_FREE_MODULES`
  (`test/unit/test_torch_free_contract.py`), or the image breaks at import.

## Changing a stage? Measure it.

Any edit here moves a number in the accuracy suite. Validate on a slice: the target cases
plus a seeded regression sample (`scripts/eval/build_slice_suite.py`), run with
`run_suite.py --suite-path … --persist-audit`, compared case by case with
`compare_runs.py`. Blame a loss on the change only after its trace shows the changed code
path. See [guide 11](../../../docs/guides/xbrl-flow/11-evaluation.md). Have the
`xbrl-answer-evaluator` subagent check a generated answer independently. **Doer ≠ judge:
the stage's author does not grade the stage's output.**
