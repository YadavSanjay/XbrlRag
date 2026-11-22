# screen-metric-names — a screen's derived metric resolves to a metric that exists

A cross-filer screen over a derived ratio is planned as `metric_kind: "derived:<Name>"`, and the
screen tool looks `<Name>` up verbatim in `mv_derived_metrics.metric_name`. The planner prompt
gives three example names but no list. The LLM therefore invents plausible ones:

- "net profit margin" became `derived:NetProfitMargin`, but the metric is **`NetMarginPct`**;
- "year-over-year revenue growth" became `derived:RevenueYoYGrowth`, but the metric is
  **`RevenueGrowthPct`**.

Each such screen matches no rows, returns NOT_FOUND, and the answer is a refusal. In run
`eval-f2ae98c8358b` that was **every** `aq-7b` net-margin screen (FY2024–26) and **every**
`aq-2a` YoY revenue screen (FY2024–26): 6 of the 7 failing screens.

`RevenueGrowthPct` equals `mv_yoy_comparison`'s rank-1 revenue YoY, the `aq-2a` ground truth,
for every filer present in both (FY2025: 10 of 10, difference 0).

## Requirement

1. **Resolution.** The screen tool resolves a derived metric name before querying:
   - an exact name is kept;
   - otherwise a name whose *normalised* form equals exactly one known metric's normalised form
     resolves to that metric;
   - otherwise a curated alias, keyed by normalised form, resolves it;
   - otherwise it is unresolved.

   Normalisation:
   - lowercase, keep only `[a-z0-9]`;
   - remove every `yoy` and `yearoveryear`;
   - then strip one trailing `percentage`, `percent` or `pct`.
2. **Unresolvable names.**
   - The screen returns NOT_FOUND with a note naming the valid metrics.
   - The plan validator rejects a `derived:` name that does not resolve. That triggers the
     existing one-tier escalation on `plan_validation_failed`.
3. **Prompt.** The planner prompt lists the valid derived metric names. This is a new prompt
   version, validated by a slice.

## Acceptance criteria

- **C1**: `resolve_derived_metric(name, known, aliases)` follows Requirement 1. It returns the
  canonical name or `None`, and returns `None` when more than one known metric shares the
  normalised form.

  Point cases, with `known` = the 29 metric names of `mv_derived_metrics` (2026-10-15) and
  `aliases = {"netprofitmargin": "NetMarginPct"}`:
  - `NetMarginPct` gives `NetMarginPct`;
  - `NetMargin` gives `NetMarginPct`;
  - `net margin %` gives `NetMarginPct`;
  - `NetProfitMargin` gives `NetMarginPct` (alias);
  - `RevenueYoYGrowth` gives `RevenueGrowthPct`;
  - `Revenue Year-over-Year Growth Percent` gives `RevenueGrowthPct`;
  - `GrossMargin` gives `GrossMarginPct`;
  - `ReturnOnEquity` gives `ReturnOnEquityPct`;
  - `EBITDA` gives `EBITDA`;
  - `ProfitabilityScore` gives `None`;
  - an alias pointing at a name not in `known` gives `None`.

  Oracle: the definition.
- **C2**: `normalize_metric_name(name)` follows the normalisation above.

  Point cases:
  - `Revenue Year-over-Year Growth Percent` gives `revenuegrowth`;
  - `NetMarginPct` gives `netmargin`;
  - `ROE pct pct` gives `roepct` (only one trailing `pct` is stripped).

  Oracle: the definition.
- **C3**: Properties:
  - `resolve_derived_metric(k, known, …) == k` for every `k` in `known`;
  - the result is always in `known` or `None`;
  - the result does not change with case, spacing or punctuation of `name`;
  - adding an alias never changes the result for a name that already resolves without it.

  Oracle: the definitions.

## Interface

New functions (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_factstore_screen.normalize_metric_name(name: str) -> str`
- `src.retriever.xbrl.xbrl_factstore_screen.resolve_derived_metric(name: str, known: Collection[str], aliases: Mapping[str, str]) -> str | None`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- **Two constants in the screen module:**
  - `DERIVED_METRICS`: the `mv_derived_metrics` names, with a comment that they mirror
    `34_mv_derived_metrics.sql`;
  - `DERIVED_METRIC_ALIASES`: curated synonyms, at least `netprofitmargin`.
- **`XbrlFactstoreScreen`:** resolves `derived:<Name>` before `_query_derived`, and records
  the resolution in `notes`. An unresolved name gives NOT_FOUND with the valid names in
  `notes`.
- **`PlanValidator._validate_screen`:** rejects an unresolvable `derived:` name with a message
  listing the valid names.
- **Planner prompt:** v4 lists `DERIVED_METRICS` in the screen guidance. Update the registry
  and `DEFAULT_VERSIONS`, then validate with a screen slice and a regression sample.

## Notes

- **Expected effect:** the 6 refused screens (`aq-7b` NetMarginPct ×3, `aq-2a` ×3) resolve.
- **Alphabet FY2025** has a `RevenueGrowthPct` but no `mv_yoy_comparison` row (its one-year
  revenue concept switch). It ranks 6th, so the top-5 ground truth is unaffected.
